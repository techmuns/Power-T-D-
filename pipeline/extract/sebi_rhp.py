"""Parallelized SEBI RHP / DRHP downloader.

Same architecture as pdf_text.py: ThreadPoolExecutor with 4 workers
(fewer than BSE because RHPs are 30-50MB each - more concurrency would
saturate the runner's bandwidth).
"""

from __future__ import annotations
import hashlib
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
from .pdf_text import _extract_text, _is_pdf

HEADERS = {"User-Agent": UA, "Accept": "application/pdf,*/*"}
MAX_BYTES_RHP = 50 * 1024 * 1024
DEFAULT_WORKERS = 4
THROTTLE = 0.4


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
def _download(session: requests.Session, url: str, dest: Path) -> int:
    with session.get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size and size > MAX_BYTES_RHP:
            raise ValueError(f"too large: {size}")
        written = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=128_000):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_BYTES_RHP:
                    raise ValueError(f"exceeded {MAX_BYTES_RHP}")
                f.write(chunk)
        return written


def _process_one(session, r, tmp_dir: Path) -> dict:
    url = r["pdf_url"]
    dest = tmp_dir / hashlib.sha1(url.encode()).hexdigest()[:16]
    out = dict(r)
    try:
        nbytes = _download(session, url, dest)
        if not _is_pdf(dest):
            raise ValueError("not a PDF")
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        text, pages = _extract_text(dest)
        out.update(status="ok", sha=sha, nbytes=nbytes,
                   text=text, pages=pages, error=None)
    except Exception as e:
        out.update(status="failed", sha=None, nbytes=None,
                   text=None, pages=None,
                   error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        try:
            dest.unlink()
        except Exception:
            pass
        time.sleep(THROTTLE)
    return out


def ingest(workers: int = DEFAULT_WORKERS) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    db_lock = Lock()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT s.id, s.pdf_url, s.title, s.filing_type
            FROM sebi_filings s
            LEFT JOIN documents d ON d.pdf_url = s.pdf_url
                                  AND d.extract_status = 'ok'
            WHERE d.id IS NULL
            """
        ).fetchall()]
        print(f"  rhp: {len(rows)} PDFs queued, {workers} workers")

        with tempfile.TemporaryDirectory(prefix="rhpdl_") as tmp_root:
            tmp = Path(tmp_root)
            session = _new_session()
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_process_one, session, r, tmp) for r in rows]
                for fut in as_completed(futures):
                    res = fut.result()
                    kind = "rhp" if "RHP"  in (res.get("filing_type") or "") else \
                           "drhp" if "DRHP" in (res.get("filing_type") or "") else \
                           "rhp_drhp"
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
                                (None, None, kind, res["pdf_url"],
                                 res["sha"], res["nbytes"], res["pages"], res["text"],
                                 now, "ok", None, "sebi", res["pdf_url"]),
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
                                (None, None, "rhp_drhp", res["pdf_url"],
                                 now, "failed", (res["error"] or "")[:500],
                                 "sebi", res["pdf_url"]),
                            )
                        conn.commit()
                    tag = "OK  " if res["status"] == "ok" else "FAIL"
                    print(f"    [{tag}] {(res.get('title') or '')[:55]}")
    print(f"  rhp: {written} ok")
    return written
