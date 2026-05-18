"""CEA monthly executive summary + transmission reports.

Source of truth for: ckm of transmission lines added, MVA transformation
capacity added, HVDC capacity, DISCOM AT&C losses. Maps directly to
sections 13, 14 and 22 of the framework doc.
"""

from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA
from ..db import connect

PAGES = {
    "exec_summary":  "https://cea.nic.in/executive-summary-report/?lang=en",
    "transmission":  "https://cea.nic.in/transmission-reports/?lang=en",
}
HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_pdfs(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf"):
            continue
        title = a.get_text(strip=True) or href.rsplit("/", 1)[-1]
        out.append({
            "title": title,
            "pdf_url": urljoin(base, href),
        })
    return out


def ingest() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total = 0
    with connect() as conn:
        for report_type, url in PAGES.items():
            try:
                html = _fetch(url)
            except Exception as e:
                print(f"  cea {report_type}: FAILED {e}")
                continue
            rows = parse_pdfs(html, url)
            for r in rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cea_reports(
                        title, report_type, period, pdf_url,
                        source, source_url, fetched_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (r["title"], report_type, None, r["pdf_url"],
                     "cea", url, now),
                )
            print(f"  cea {report_type}: {len(rows)} reports")
            total += len(rows)
    return total
