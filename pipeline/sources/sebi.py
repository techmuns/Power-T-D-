"""SEBI public-issue filings (RHP / DRHP / Addendum).

SEBI's public-issues page is server-rendered HTML but the table
structure has shifted; rows now live in nested divs under
`.statistics-data-listing tbody tr`, with PDF links inside `.contentLnk`
anchors. We also keep a debug snapshot of the raw HTML so a future
layout change is immediately visible.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

# Public-issue offer documents listing
LISTING = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
    "?doListing=yes&sid=3&ssid=15&smid=10"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_list() -> str:
    r = requests.get(LISTING, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return r.text


DATE_RE = re.compile(r"\b\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")


def parse_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen_urls: set[str] = set()

    # Strategy 1: anchor-first - any PDF link anywhere on the page,
    # with title from the link text and date from the nearest text node.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" not in href.lower():
            continue
        if not href.startswith("http"):
            href = "https://www.sebi.gov.in" + (href if href.startswith("/") else "/" + href)
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = a.get_text(strip=True) or href.rsplit("/", 1)[-1]
        # Find a date near the link - walk up the tree looking at sibling text
        date_str = ""
        node = a
        for _ in range(4):
            node = node.parent if node and node.parent else None
            if not node:
                break
            txt = node.get_text(" ", strip=True)
            m = DATE_RE.search(txt)
            if m:
                date_str = m.group(0)
                break

        upper = title.upper()
        if "ADDENDUM" in upper or "CORRIGENDUM" in upper:
            ft = "ADDENDUM"
        elif "RHP" in upper or "RED HERRING" in upper:
            ft = "RHP"
        elif "DRHP" in upper or "DRAFT RED HERRING" in upper or "DRAFT OFFER" in upper:
            ft = "DRHP"
        elif "PROSPECTUS" in upper:
            ft = "PROSPECTUS"
        else:
            ft = "PUBLIC_ISSUE"

        out.append({
            "title": title[:500],
            "filing_type": ft,
            "filed_on": date_str,
            "pdf_url": href,
        })
    return out


def ingest() -> int:
    html = _fetch_list()
    debug_path = RAW_DIR / "sebi"
    debug_path.mkdir(parents=True, exist_ok=True)
    (debug_path / "listing.html").write_text(html[:2_000_000])

    rows = parse_rows(html)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO sebi_filings(
                    title, filing_type, filed_on, pdf_url,
                    source, source_url, fetched_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (r["title"], r["filing_type"], r["filed_on"], r["pdf_url"],
                 "sebi", LISTING, now),
            )
            written += cur.rowcount or 0
    print(f"  sebi: {len(rows)} pdf links parsed, {written} new rows")
    return written
