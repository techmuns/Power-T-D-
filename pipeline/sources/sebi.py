"""SEBI public-issue filings (RHP / DRHP / Addendum).

We scrape the public-issues listing page. The framework doc relies
heavily on RHPs (Atlanta, OPTL, Transrail, Quality Power) - these are
the only place voltage-class capability, executed MVA ratings, and
component-level capacity are disclosed in detail for new listings.
"""

from __future__ import annotations
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA
from ..db import connect

LISTING = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=10"
HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_list() -> str:
    r = requests.get(LISTING, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("table tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        link = tr.find("a", href=True)
        if not link or len(cells) < 2:
            continue
        href = link["href"]
        if not href.lower().endswith(".pdf"):
            continue
        if not href.startswith("http"):
            href = "https://www.sebi.gov.in" + href
        title = link.get_text(strip=True) or cells[0]
        date_str = next((c for c in cells if any(ch.isdigit() for ch in c)), "")
        filing_type = "RHP" if "RHP" in title.upper() else (
            "DRHP" if "DRHP" in title.upper() else "PUBLIC_ISSUE"
        )
        out.append({
            "title": title,
            "filing_type": filing_type,
            "filed_on": date_str,
            "pdf_url": href,
        })
    return out


def ingest() -> int:
    html = _fetch_list()
    rows = parse_rows(html)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO sebi_filings(
                    title, filing_type, filed_on, pdf_url,
                    source, source_url, fetched_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (r["title"], r["filing_type"], r["filed_on"], r["pdf_url"],
                 "sebi", LISTING, now),
            )
            written += 1
    print(f"  sebi: {written} filings indexed")
    return written
