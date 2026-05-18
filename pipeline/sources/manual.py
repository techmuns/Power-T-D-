"""Manual-upload ingestor for CEA / regulator PDFs.

cea.nic.in blocks GitHub Actions runner IP ranges. Until we route via a
residential proxy, the workaround is: an analyst drops the CEA monthly
Executive Summary PDF (or any regulator PDF) into data/manual/cea/ and
this fetcher absorbs it on the next run, parses the text, and stores
it in the same `cea_reports` + `documents` tables the network path
would have used.

Convention:
  data/manual/cea/<YYYY-MM>_exec_summary.pdf
  data/manual/cea/<YYYY-MM>_transmission.pdf
  data/manual/<source>/<filename>.pdf      (generic)
"""

from __future__ import annotations
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from ..config import DATA_DIR
from ..db import connect

MANUAL_ROOT = DATA_DIR / "manual"
PERIOD_RE = re.compile(r"(\d{4})-(\d{2})")


def _kind(name: str) -> str:
    n = name.lower()
    if "exec" in n or "executive" in n:
        return "exec_summary"
    if "transmission" in n:
        return "transmission"
    if "discom" in n or "atc" in n:
        return "discom"
    return "other"


def _extract(path: Path) -> tuple[str, int]:
    try:
        reader = PdfReader(str(path))
        pages = []
        n = min(len(reader.pages), 400)
        for i in range(n):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                t = ""
            if t:
                pages.append(t)
        return ("\n\n".join(pages), len(reader.pages))
    except Exception as e:
        return (f"[extract failed: {e}]", 0)


def ingest() -> int:
    MANUAL_ROOT.mkdir(parents=True, exist_ok=True)
    cea_dir = MANUAL_ROOT / "cea"
    cea_dir.mkdir(exist_ok=True)
    (MANUAL_ROOT / "README.md").write_text(
        "Drop regulator PDFs that the daily Actions run cannot fetch here.\n"
        "Naming convention: `<YYYY-MM>_<kind>.pdf`.\n"
        "Examples: `2026-04_exec_summary.pdf`, `2026-04_transmission.pdf`.\n"
        "Files are absorbed by the next pipeline run, parsed, and stored\n"
        "in the `cea_reports` and `documents` tables. The PDF itself is\n"
        "kept in this folder for audit.\n"
    )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        for path in sorted(cea_dir.glob("*.pdf")):
            rel = f"manual://cea/{path.name}"
            already = conn.execute(
                "SELECT 1 FROM cea_reports WHERE pdf_url=?", (rel,)
            ).fetchone()
            if already:
                continue

            kind = _kind(path.name)
            m = PERIOD_RE.search(path.name)
            period = f"{m.group(1)}-{m.group(2)}" if m else None
            text, pages = _extract(path)
            size = path.stat().st_size
            sha = hashlib.sha256(path.read_bytes()).hexdigest()

            conn.execute(
                """
                INSERT INTO cea_reports(
                    title, report_type, period, pdf_url,
                    source, source_url, fetched_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (path.stem, kind, period, rel,
                 "manual_upload", rel, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO documents(
                    company_id, announcement_id, doc_kind, pdf_url,
                    pdf_sha256, pdf_bytes, page_count, full_text,
                    extracted_at, extract_status, source, source_url
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (None, None, f"cea_{kind}", rel,
                 sha, size, pages, text,
                 now, "ok", "manual_upload", rel),
            )
            written += 1
            print(f"  manual: ingested {path.name} ({pages}p, {size//1024}KB)")
    if not written:
        print("  manual: no new files in data/manual/cea/")
    return written
