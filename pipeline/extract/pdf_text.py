"""Download high-value BSE announcement PDFs and extract their text.

We don't try to ingest every filing - hundreds of GBs. We focus on the
four document kinds that hold the diligence content the framework needs:
  - investor presentation
  - earnings-call transcript
  - financial results
  - press release (only when headline hints at order / capacity / kV / MVA)

The PDF itself is NOT committed to git. Only extracted text lands in the
`documents` table.

Robustness notes (after first prod run):
  - throttle 0.4s between requests; BSE rate-limits aggressively
  - validate downloaded bytes start with `%PDF-` before parsing
  - parse pypdf in `strict=False` mode (BSE attachments are often
    malformed but contain extractable text)
  - failed docs are re-queued on next run (extract_status='failed' rows
    are NOT in the "already done" set the candidate query uses)
"""

from __future__ import annotations
import hashlib
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA
from ..db import connect

HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/pdf,*/*",
}

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 400
SLEEP_BETWEEN = 0.4   # seconds; BSE rate limit

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


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=20))
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


def _is_pdf(path: Path) -> bool:
    """Quick magic-byte check so we don't try to parse a 4-byte 403 HTML."""
    try:
        with path.open("rb") as f:
            head = f.read(5)
        return head == b"%PDF-"
    except Exception:
        return False


def _extract_text(path: Path) -> tuple[str, int]:
    """Try pypdf first (fast). If it fails or yields almost no text, fall
    back to pdfminer.six (slower but tolerates malformed PDFs)."""
    # 1. pypdf
    text = ""
    pages_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        pages_count = len(reader.pages)
        chunks = []
        for i in range(min(pages_count, MAX_PAGES)):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                chunks.append(t)
        text = "\n\n".join(chunks)
    except Exception:
        text = ""

    if len(text.strip()) >= 200:
        return text, pages_count

    # 2. pdfminer fallback
    try:
        from pdfminer.high_level import extract_text as miner_extract
        from pdfminer.pdfpage import PDFPage
        miner_text = miner_extract(str(path), maxpages=MAX_PAGES) or ""
        if not pages_count:
            with path.open("rb") as f:
                pages_count = sum(1 for _ in PDFPage.get_pages(f))
        if len(miner_text.strip()) > len(text.strip()):
            text = miner_text
    except Exception:
        pass

    return text, pages_count


def _candidate_rows(conn, limit_per_company: int, since: str | None,
                    include_failed: bool) -> list[dict]:
    """Pick announcements that classify as high-value AND either:
      - have no document row yet, OR
      - have one with status='failed' (so we retry across runs)
    """
    where = ["a.pdf_url IS NOT NULL", "a.pdf_url <> ''"]
    params: list = []
    if since:
        where.append("a.broadcast_ts >= ?")
        params.append(since)

    join_pred = "d.extract_status='ok'"
    rows = conn.execute(
        f"""
        SELECT a.id AS aid, a.company_id, a.headline, a.category, a.subject,
               a.pdf_url, a.broadcast_ts, co.short
        FROM announcements a
        JOIN companies co ON co.id = a.company_id
        LEFT JOIN documents d ON d.pdf_url = a.pdf_url AND {join_pred}
        WHERE {' AND '.join(where)}
          AND d.id IS NULL
        ORDER BY a.broadcast_ts DESC
        """,
        params,
    ).fetchall()

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
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0

    with connect() as conn:
        candidates = _candidate_rows(conn, limit_per_company, since, True)
        print(f"  pdf: {len(candidates)} candidates queued")

        with tempfile.TemporaryDirectory(prefix="pdfdl_") as tmp:
            for r in candidates:
                url = r["pdf_url"]
                dest = Path(tmp) / hashlib.sha1(url.encode()).hexdigest()[:16]
                try:
                    nbytes = _download(url, dest)
                    if not _is_pdf(dest):
                        raise ValueError("downloaded file is not a PDF")
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
                    written += 1
                    print(f"    {r['short']:18s} [{r['doc_kind']:22s}] "
                          f"{pages}p {nbytes//1024}KB  ok")
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:200]}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO documents(
                            company_id, announcement_id, doc_kind, pdf_url,
                            extracted_at, extract_status, extract_error,
                            source, source_url
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (r["company_id"], r["aid"], r["doc_kind"], url,
                         now, "failed", err[:500],
                         "bse_attachment", url),
                    )
                    print(f"    {r['short']:18s} FAIL  {err[:100]}")
                finally:
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                    time.sleep(SLEEP_BETWEEN)
    return written
