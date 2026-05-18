"""Download + text-extract SEBI RHP / DRHP PDFs.

For new and recent listings (Atlanta, Transrail, Quality Power, OPTL,
etc.) the RHP is by far the richest single source of capacity, voltage
class, customer list and competitive positioning data. Each RHP is
200-500 pages so this is heavier than BSE attachments - we cap at 600
pages, 50MB.
"""

from __future__ import annotations
import hashlib
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA
from ..db import connect
from .pdf_text import _extract_text, _is_pdf

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/pdf,*/*",
}
MAX_BYTES_RHP = 50 * 1024 * 1024   # RHPs commonly 30-45 MB
THROTTLE = 1.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
def _download(url: str, dest: Path) -> int:
    with requests.get(url, headers=HEADERS, timeout=120, stream=True) as r:
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


def ingest() -> int:
    """Download every SEBI filing PDF whose `pdf_url` isn't already in
    `documents`. Stores text in `documents` with doc_kind='rhp_drhp'."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.pdf_url, s.title, s.filing_type
            FROM sebi_filings s
            LEFT JOIN documents d ON d.pdf_url = s.pdf_url
                                  AND d.extract_status = 'ok'
            WHERE d.id IS NULL
            """
        ).fetchall()
        print(f"  rhp: {len(rows)} RHP/DRHP PDFs queued")

        with tempfile.TemporaryDirectory(prefix="rhpdl_") as tmp:
            for r in rows:
                url = r["pdf_url"]
                dest = Path(tmp) / hashlib.sha1(url.encode()).hexdigest()[:16]
                try:
                    nbytes = _download(url, dest)
                    if not _is_pdf(dest):
                        raise ValueError("not a PDF")
                    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                    text, pages = _extract_text(dest)
                    kind = "rhp" if "RHP" in (r["filing_type"] or "") else \
                           "drhp" if "DRHP" in (r["filing_type"] or "") else \
                           "rhp_drhp"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO documents(
                            company_id, announcement_id, doc_kind, pdf_url,
                            pdf_sha256, pdf_bytes, page_count, full_text,
                            extracted_at, extract_status, extract_error,
                            source, source_url
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (None, None, kind, url,
                         sha, nbytes, pages, text,
                         now, "ok", None,
                         "sebi", url),
                    )
                    written += 1
                    print(f"    [{kind}] {pages}p {nbytes//1024}KB  "
                          f"{(r['title'] or '')[:60]}")
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
                        (None, None, "rhp_drhp", url,
                         now, "failed", err[:500],
                         "sebi", url),
                    )
                    print(f"    FAIL  {err[:100]}")
                finally:
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                    time.sleep(THROTTLE)
    return written
