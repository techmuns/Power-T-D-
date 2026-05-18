"""BSE market-data fetcher: live price + market cap + 52-week range + P/E.

Without this, the framework's section 22 'valuation comfort' has no
multiples to anchor on. We use BSE's undocumented Comheader endpoint
(same one bseindia.com uses to render the right-hand panel of a stock
page) and the StockReachGraph endpoint for last close.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Iterable

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

COMHEADER = "https://api.bseindia.com/BseIndiaAPI/api/ComHeaderNew/w"
PRICE     = "https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w"


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    fetched_at    TEXT NOT NULL,
    price         REAL,
    pct_change    REAL,
    market_cap_cr REAL,
    pe_ratio      REAL,
    pb_ratio      REAL,
    dividend_yield REAL,
    week52_high   REAL,
    week52_low    REAL,
    book_value    REAL,
    eps           REAL,
    face_value    REAL,
    raw_json      TEXT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_data_company
    ON market_data(company_id, fetched_at);
"""


def _ensure_schema(conn):
    conn.executescript(SCHEMA)


def _num(x):
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except Exception:
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _comheader(scrip: str) -> dict:
    r = requests.get(
        COMHEADER,
        params={"quotetype": "EQ", "scripcode": scrip, "seg": "EQUITY"},
        headers=HEADERS, timeout=30,
    )
    r.raise_for_status()
    return r.json() if r.text.strip().startswith("{") else {}


def ingest(companies: Iterable[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    debug = RAW_DIR / "bse_market"
    debug.mkdir(parents=True, exist_ok=True)
    written = 0
    with connect() as conn:
        _ensure_schema(conn)
        for c in companies:
            if not c.get("bse"):
                continue
            try:
                data = _comheader(c["bse"])
            except Exception as e:
                print(f"  mkt {c['short']} ({c['bse']}): FAILED {e}")
                continue
            if not data:
                continue
            (debug / f"{c['bse']}.json").write_text(json.dumps(data, indent=2))

            # BSE's Comheader returns nested arrays; flatten the bits we
            # care about by key-name search.
            def pick(*keys):
                # walk dict + list of dicts looking for first non-empty hit
                stack = [data]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        for k in keys:
                            if k in cur and cur[k] not in (None, "", "-"):
                                return cur[k]
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                return None

            conn.execute(
                """
                INSERT INTO market_data(
                    company_id, fetched_at, price, pct_change, market_cap_cr,
                    pe_ratio, pb_ratio, dividend_yield,
                    week52_high, week52_low, book_value, eps, face_value,
                    raw_json, source, source_url
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (c["id"], now,
                 _num(pick("LTradedPrice", "CurrRate", "Price")),
                 _num(pick("Chg_Per", "ChangePct", "PerCh")),
                 _num(pick("MktCap", "MarketCap", "FFMCAP")),
                 _num(pick("PE", "P_E", "P/E")),
                 _num(pick("PB", "P_B", "P/B")),
                 _num(pick("DivYield", "DividendYield", "DY")),
                 _num(pick("WeekHighPrice", "Wk52High", "WkHigh")),
                 _num(pick("WeekLowPrice",  "Wk52Low",  "WkLow")),
                 _num(pick("BV", "BookValue", "Book_Value")),
                 _num(pick("EPS", "EPS_TTM")),
                 _num(pick("FaceValue", "FV")),
                 json.dumps(data),
                 "bse_comheader",
                 f"https://www.bseindia.com/stock-share-price/_/_/{c['bse']}/"),
            )
            written += 1
            print(f"  mkt {c['short']}: ok")
    return written
