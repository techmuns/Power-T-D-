"""CEA executive-summary + transmission reports listing.

cea.nic.in is a WordPress site; report-listing pages mostly hold direct
PDF anchors, but a chunk of pages render the table via JS. We:
  1. fetch the listing page,
  2. pull every PDF link (anchor-first, like the SEBI fetcher),
  3. dump the raw HTML to data/raw/cea/ for audit.

Period (YYYY-MM or YYYY) is parsed best-effort from anchor/filename.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

PAGES = {
    "exec_summary":  "https://cea.nic.in/executive-summary-report/?lang=en",
    "transmission":  "https://cea.nic.in/transmission-reports/?lang=en",
}
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MONTH_RE = re.compile(
    r"\b("
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"january|february|march|april|june|july|august|september|"
    r"october|november|december"
    r")[a-z]*[-\s_]*(\d{2,4})\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _period(text: str) -> str | None:
    m = MONTH_RE.search(text)
    if m:
        month, year = m.group(1).lower()[:3], m.group(2)
        year = "20" + year if len(year) == 2 else year
        months = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05",
                  "jun":"06","jul":"07","aug":"08","sep":"09","oct":"10",
                  "nov":"11","dec":"12"}
        return f"{year}-{months[month]}"
    m = YEAR_RE.search(text)
    if m:
        return m.group(1)
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return r.text


def parse_pdfs(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" not in href.lower():
            continue
        full = urljoin(base, href)
        if full in seen:
            continue
        seen.add(full)
        title = a.get_text(strip=True) or full.rsplit("/", 1)[-1]
        # period from title; if missing try the filename
        period = _period(title) or _period(href)
        out.append({"title": title[:500], "pdf_url": full, "period": period})
    return out


def ingest() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    debug_path = RAW_DIR / "cea"
    debug_path.mkdir(parents=True, exist_ok=True)

    total = 0
    with connect() as conn:
        for report_type, url in PAGES.items():
            try:
                html = _fetch(url)
            except Exception as e:
                print(f"  cea {report_type}: FAILED {e}")
                continue
            (debug_path / f"{report_type}.html").write_text(html[:2_000_000])

            rows = parse_pdfs(html, url)
            written = 0
            for r in rows:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO cea_reports(
                        title, report_type, period, pdf_url,
                        source, source_url, fetched_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (r["title"], report_type, r["period"], r["pdf_url"],
                     "cea", url, now),
                )
                written += cur.rowcount or 0
            print(f"  cea {report_type}: {len(rows)} pdf links, {written} new rows")
            total += written
    return total
