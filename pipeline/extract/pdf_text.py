"""Download high-value BSE announcement PDFs and extract their text.

We don't try to ingest every filing - that would be hundreds of GBs.
Instead we focus on the four document kinds that hold the diligence
content the framework needs:
  - investor presentation
  - earnings-call transcript
  - financial results
  - press release (only if the headline hints at order win / capacity)

The PDF itself is NOT committed to git (too large). Only the extracted
text is stored, in the `documents` table.
"""

from __future__ import annotations
import hashlib
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from pypdf import PdfReader
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA
from ..db import connect

HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/pdf,*/*",
}

MAX_BYTES = 25 * 1024 * 1024     # 25 MB hard cap, skip larger files
MAX_PAGES = 400                  # stop extraction beyond this many pages

# How we classify announcements into doc_kind. Match against subject + headline.
KIND_RULES: list[tuple[str, re.Pattern]] = [
    ("investor_presentation",
     re.compile(r"investor\s*present|analyst\s*present|institutional\s*invest", re.I)),
    ("transcript",
     re.compile(r"transcript|conference\s*call|earnings\s*call|concall", re.I)),
    ("results",
     re.compile(r"financial\s*results|quarterly\s*results|audited\s*results", re.I)),
    ("press_release",
     re.compile(r"order|capacity|expansion|commission|inauguration|capex|"
                r"award|contract\s*win|kv|mva|gva", re.I)),
]


def classify(category: str, subject: str, headline: str) -> str | None:
    text = " ".join([category or "", subject or "", headline or ""])
    for kind, pat in KIND_RULES:
        if pat.search(text):
            return kind
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _download(url: str, dest: Path) -> int:
    with requests.get(url, headers=HEADERS, timeout=60, stream=True) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size and size > MAX_BYTES:
            raise ValueError(f"too large: {size} bytes")
        written = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64_000):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ValueError(f"exceeded {MAX_BYTES} bytes mid-download")
                f.write(chunk)
        return written


def _extract_text(path: Path) -> tuple[str, int]:
    reader = PdfReader(str(path))
    pages = []
    n = min(len(reader.pages), MAX_PAGES)
    for i in range(n):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            t = ""
        if t:
            pages.append(t)
    return ("\n\n".join(pages), len(reader.pages))


def _candidate_rows(conn, limit_per_company: int, since: str | None) -> list[dict]:
    where = ["a.pdf_url IS NOT NULL", "a.pdf_url <> ''"]
    params: list = []
    if since:
        where.append("a.broadcast_ts >= ?")
        params.append(since)
    rows = conn.execute(
        f"""
        SELECT a.id AS aid, a.company_id, a.headline, a.category, a.subject,
               a.pdf_url, a.broadcast_ts, co.short
        FROM announcements a
        JOIN companies co ON co.id = a.company_id
        LEFT JOIN documents d ON d.pdf_url = a.pdf_url
        WHERE {' AND '.join(where)}
          AND d.id IS NULL
        ORDER BY a.broadcast_ts DESC
        """,
        params,
    ).fetchall()
    # cap per company
    counts: dict[int, int] = {}
    keep = []
    for r in rows:
        kind = classify(r["category"], r["subject"], r["headline"])
        if not kind:
            continue
        cid = r["company_id"]
        if counts.get(cid, 0) >= limit_per_company:
            continue
        counts[cid] = counts.get(cid, 0) + 1
        d = dict(r); d["doc_kind"] = kind
        keep.append(d)
    return keep


def ingest(limit_per_company: int = 12, since: str = "2024-01-01") -> int:
    """Download + extract for up to `limit_per_company` new docs per company."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0

    with connect() as conn:
        candidates = _candidate_rows(conn, limit_per_company, since)
        print(f"  pdf: {len(candidates)} candidates queued")

        with tempfile.TemporaryDirectory(prefix="pdfdl_") as tmp:
            for r in candidates:
                url = r["pdf_url"]
                dest = Path(tmp) / hashlib.sha1(url.encode()).hexdigest()[:16]
                doc_id = None
                try:
                    nbytes = _download(url, dest)
                    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                    text, pages = _extract_text(dest)
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO documents(
                            company_id, announcement_id, doc_kind, pdf_url,
                            pdf_sha256, pdf_bytes, page_count, full_text,
                            extracted_at, extract_status, extract_error,
                            source, source_url
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (r["company_id"], r["aid"], r["doc_kind"], url,
                         sha, nbytes, pages, text,
                         now, "ok", None,
                         "bse_attachment", url),
                    )
                    doc_id = cur.lastrowid
                    written += 1
                    print(f"    {r['short']:18s} [{r['doc_kind']:22s}] "
                          f"{pages}p {nbytes//1024}KB  {url.rsplit('/',1)[-1][:40]}")
                except Exception as e:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO documents(
                            company_id, announcement_id, doc_kind, pdf_url,
                            extracted_at, extract_status, extract_error,
                            source, source_url
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (r["company_id"], r["aid"], r["doc_kind"], url,
                         now, "failed", str(e)[:500],
                         "bse_attachment", url),
                    )
                    print(f"    {r['short']:18s} FAIL {type(e).__name__}: {str(e)[:80]}")
                finally:
                    try:
                        dest.unlink()
                    except Exception:
                        pass
    return written
