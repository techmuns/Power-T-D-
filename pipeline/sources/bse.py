"""BSE corporate announcements fetcher.

Public, undocumented-but-stable JSON endpoint that powers
bseindia.com's "Corporate Announcements" page. Returns every filing a
listed company posts: quarterly results, investor presentations,
earnings-call transcripts, press releases, AGM notices, RHP/DRHP
addendums, etc. We store the headline + the underlying PDF URL.
"""

from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
PDF_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(params: dict) -> dict:
    r = requests.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_for_scrip(scrip: str, days_back: int = 365) -> list[dict]:
    """Fetch every announcement for one BSE scrip code over the last N days."""
    to_dt = datetime.now(timezone.utc).date()
    from_dt = to_dt - timedelta(days=days_back)
    params = {
        "pageno": 1,
        "strCat": -1,
        "strPrevDate": from_dt.strftime("%Y%m%d"),
        "strScrip": scrip,
        "strSearch": "P",
        "strToDate": to_dt.strftime("%Y%m%d"),
        "strType": "C",
    }
    rows: list[dict] = []
    while True:
        payload = _get(params)
        page_rows = payload.get("Table") or []
        rows.extend(page_rows)
        # BSE paginates; second table contains pagination info
        meta = (payload.get("Table1") or [{}])[0]
        total = int(meta.get("ROWCNT") or len(rows))
        if len(rows) >= total or not page_rows:
            break
        params["pageno"] += 1
    # snapshot the raw JSON for audit
    out = RAW_DIR / "bse" / f"{scrip}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    return rows


def _ann_record(company_id: int, scrip: str, r: dict) -> tuple:
    pdf = r.get("ATTACHMENTNAME") or ""
    pdf_url = PDF_BASE + pdf if pdf else None
    headline = (r.get("HEADLINE") or r.get("NEWSSUB") or "").strip()
    return (
        company_id,
        headline,
        (r.get("CATEGORYNAME") or "").strip(),
        (r.get("SUBCATNAME") or r.get("NEWSSUB") or "").strip(),
        pdf_url,
        r.get("NEWS_DT") or r.get("DT_TM"),
        "bse",
        f"https://www.bseindia.com/stock-share-price/_/_/{scrip}/corp-announcements/",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def ingest(companies: Iterable[dict], days_back: int = 365) -> int:
    """Fetch + persist for every company that has a BSE scrip code."""
    written = 0
    with connect() as conn:
        for c in companies:
            if not c["bse"]:
                continue
            try:
                rows = fetch_for_scrip(c["bse"], days_back=days_back)
            except Exception as e:
                print(f"  bse {c['short']} ({c['bse']}): FAILED {e}")
                continue
            for r in rows:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO announcements(
                            company_id, headline, category, subject,
                            pdf_url, broadcast_ts, source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        _ann_record(c["id"], c["bse"], r),
                    )
                    written += conn.total_changes and 0  # noqa: keep simple
                except Exception as e:
                    print(f"  bse insert error {c['short']}: {e}")
            print(f"  bse {c['short']}: {len(rows)} announcements")
            written += len(rows)
    return written
