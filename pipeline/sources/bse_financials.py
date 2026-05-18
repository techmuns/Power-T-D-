"""BSE quarterly financial results.

The BSE site exposes results under two undocumented endpoints; the
stable one is `/FinancialResults/w` (not `FinancialResultsIP/w`). Column
names vary by industry/template, so we keep the full raw JSON in
`raw_json` and only normalize the handful of fields we need now.

On every run we also save:
  data/raw/bse_fin/<scrip>_<S|C>.json    - the parsed list
  data/raw/bse_fin/_sample.json          - first non-empty response,
                                            for parser audit
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Iterable

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

API = "https://api.bseindia.com/BseIndiaAPI/api/FinancialResults/w"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

# Period-end column name varies across filing templates. Try in order.
PERIOD_KEYS = (
    "RESULT_END", "DT_END", "EndDate", "END_DT", "PERIOD_ENDED", "ResultEnd",
)
REVENUE_KEYS = (
    "INCOME_FROM_OPERATIONS", "REVENUE_FROM_OPERATIONS", "Income",
    "REVENUE_FROM_OP", "TOTAL_REVENUE", "Revenue",
)
EBITDA_KEYS = ("EBITDA", "OPERATING_PROFIT", "Profit_Before_Tax")
PAT_KEYS = (
    "PROFITLOSS", "PROFIT_LOSS", "NETPROFIT", "NetProfit", "NET_PROFIT",
    "PROFIT_AFTER_TAX",
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(scrip: str, consolidated: bool) -> tuple[list[dict], str]:
    """Return (rows, raw_response_text)."""
    r = requests.get(
        API,
        params={"scripcode": scrip, "seg": "0", "Type": "C" if consolidated else "S"},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    text = r.text
    try:
        data = r.json()
    except Exception:
        return [], text
    if isinstance(data, dict):
        return (data.get("Table") or data.get("Data") or []), text
    if isinstance(data, list):
        return data, text
    return [], text


def _pick(row: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in row and row[k] not in (None, "", "-"):
            return row[k]
    # case-insensitive fallback
    lower = {k.lower(): v for k, v in row.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, "", "-"):
            return v
    return None


def _norm_period(row: dict) -> str | None:
    p = _pick(row, PERIOD_KEYS)
    if not p:
        return None
    p = str(p)
    try:
        return datetime.fromisoformat(p.replace("Z", "")).date().isoformat()
    except Exception:
        pass
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d",
                "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(p, fmt).date().isoformat()
        except Exception:
            continue
    return None


def _num(x):
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def ingest(companies: Iterable[dict]) -> int:
    written = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sample_saved = False
    debug_path = RAW_DIR / "bse_fin"
    debug_path.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        for c in companies:
            if not c["bse"]:
                continue
            for cons in (False, True):
                try:
                    rows, raw_text = _get(c["bse"], cons)
                except Exception as e:
                    print(f"  fin {c['short']} cons={cons}: FAILED {e}")
                    continue

                # Always snapshot the first non-empty response we see,
                # for parser audit on the next iteration.
                if rows and not sample_saved:
                    (debug_path / "_sample.json").write_text(
                        json.dumps({"scrip": c["bse"], "cons": cons,
                                    "rows": rows[:3]}, indent=2)
                    )
                    sample_saved = True

                # Per-company snapshot (replaces previous)
                tag = "C" if cons else "S"
                if rows:
                    (debug_path / f"{c['bse']}_{tag}.json").write_text(
                        json.dumps(rows, indent=2)
                    )
                else:
                    # save the raw bytes when API returned empty so we can
                    # see what the server is sending
                    (debug_path / f"{c['bse']}_{tag}.empty.txt").write_text(
                        raw_text[:4000]
                    )

                inserted_for_this = 0
                for r in rows:
                    period = _norm_period(r)
                    if not period:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO financials(
                            company_id, period_end, period_type, consolidated,
                            revenue, ebitda, pat, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            c["id"], period, "quarterly", 1 if cons else 0,
                            _num(_pick(r, REVENUE_KEYS)),
                            _num(_pick(r, EBITDA_KEYS)),
                            _num(_pick(r, PAT_KEYS)),
                            json.dumps(r),
                            "bse_financials",
                            f"https://www.bseindia.com/stock-share-price/_/_/{c['bse']}/financials-results/",
                            now,
                        ),
                    )
                    inserted_for_this += 1
                    written += 1
                print(f"  fin {c['short']} {'cons' if cons else 'standalone'}: "
                      f"{len(rows)} returned, {inserted_for_this} stored")
    return written
