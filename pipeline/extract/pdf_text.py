"""Download high-value BSE announcement PDFs and extract their text.

Parallelized via ThreadPoolExecutor - the bottleneck is network IO, not
CPU, so concurrent downloads give a ~5-8x speedup. BSE handles ~10
concurrent attachment requests without rate-limiting in practice.
"""

from __future__ import annotations
import hashlib
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

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
DEFAULT_WORKERS = 8
PER_WORKER_THROTTLE = 0.15

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


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _download(session: requests.Session, url: str, dest: Path) -> int:
    with session.get(url, timeout=60, stream=True) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size and size > MAX_BYTES:
            raise ValueError(f"too large: {size}")
        written = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64_000):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ValueError(f"exceeded {MAX_BYTES}")
                f.write(chunk)
        return written


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(5)
        return head == b"%PDF-"
    except Exception:
        return False


def _extract_text(path: Path) -> tuple[str, int]:
    """pypdf first (fast); pdfminer.six fallback if pypdf yields nothing."""
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
                              AND d.extract_status = 'ok'
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


def _process_one(session: requests.Session, r: dict, tmp_dir: Path) -> dict:
    url = r["pdf_url"]
    dest = tmp_dir / hashlib.sha1(url.encode()).hexdigest()[:16]
    out = dict(r)
    try:
        nbytes = _download(session, url, dest)
        if not _is_pdf(dest):
            raise ValueError("not a PDF")
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        text, pages = _extract_text(dest)
        out.update(status="ok", sha=sha, nbytes=nbytes, text=text,
                   pages=pages, error=None)
    except Exception as e:
        out.update(status="failed", sha=None, nbytes=None, text=None,
                   pages=None, error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        try:
            dest.unlink()
        except Exception:
            pass
        time.sleep(PER_WORKER_THROTTLE)
    return out


def ingest(limit_per_company: int = 8, since: str = "2024-01-01",
           workers: int = DEFAULT_WORKERS) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    failed  = 0
    db_lock = Lock()

    with connect() as conn:
        candidates = _candidate_rows(conn, limit_per_company, since)
        print(f"  pdf: {len(candidates)} candidates, {workers} workers")

        with tempfile.TemporaryDirectory(prefix="pdfdl_") as tmp_root:
            tmp = Path(tmp_root)
            session = _new_session()

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_process_one, session, r, tmp)
                           for r in candidates]
                for fut in as_completed(futures):
                    res = fut.result()
                    with db_lock:
                        if res["status"] == "ok":
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO documents(
                                    company_id, announcement_id, doc_kind, pdf_url,
                                    pdf_sha256, pdf_bytes, page_count, full_text,
                                    extracted_at, extract_status, extract_error,
                                    source, source_url
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (res["company_id"], res["aid"], res["doc_kind"],
                                 res["pdf_url"], res["sha"], res["nbytes"],
                                 res["pages"], res["text"],
                                 now, "ok", None,
                                 "bse_attachment", res["pdf_url"]),
                            )
                            written += 1
                        else:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO documents(
                                    company_id, announcement_id, doc_kind, pdf_url,
                                    extracted_at, extract_status, extract_error,
                                    source, source_url
                                ) VALUES(?,?,?,?,?,?,?,?,?)
                                """,
                                (res["company_id"], res["aid"], res["doc_kind"],
                                 res["pdf_url"],
                                 now, "failed", (res["error"] or "")[:500],
                                 "bse_attachment", res["pdf_url"]),
                            )
                            failed += 1
                        conn.commit()
                    tag = "OK   " if res["status"] == "ok" else "FAIL "
                    print(f"    [{tag}] {res['short']:18s} "
                          f"[{res['doc_kind']:22s}] {res['pdf_url'].rsplit('/',1)[-1][:40]}")
    print(f"  pdf: {written} ok / {failed} failed")
    return written
