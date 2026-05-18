"""BSE quarterly financial results.

Public endpoint that returns the same numbers the BSE website renders
on a company's "Financials > Results" tab. Standalone + consolidated,
quarter-by-quarter, going back several years.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Iterable

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

API = "https://api.bseindia.com/BseIndiaAPI/api/FinancialResultsIP/w"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(scrip: str, consolidated: bool) -> list[dict]:
    r = requests.get(
        API,
        params={"scripcode": scrip, "seg": "0", "Type": "C" if consolidated else "S"},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        return data.get("Table") or []
    return data or []


def _norm_period(row: dict) -> str | None:
    p = row.get("DT_END") or row.get("EndDate") or row.get("END_DT")
    if not p:
        return None
    # BSE returns "2025-06-30T00:00:00" or "30 Jun 2025"
    try:
        return datetime.fromisoformat(p.replace("Z", "")).date().isoformat()
    except Exception:
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(p, fmt).date().isoformat()
            except Exception:
                pass
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
    with connect() as conn:
        for c in companies:
            if not c["bse"]:
                continue
            for cons in (False, True):
                try:
                    rows = _get(c["bse"], cons)
                except Exception as e:
                    print(f"  fin {c['short']} cons={cons}: FAILED {e}")
                    continue
                if not rows:
                    continue
                out = RAW_DIR / "bse_fin" / f"{c['bse']}_{'C' if cons else 'S'}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(rows, indent=2))
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
                            _num(r.get("REVENUE_FROM_OP") or r.get("Income")),
                            _num(r.get("EBITDA")),
                            _num(r.get("PROFIT_LOSS") or r.get("NetProfit") or r.get("PROFITLOSS")),
                            json.dumps(r),
                            "bse_financials",
                            f"https://www.bseindia.com/stock-share-price/_/_/{c['bse']}/financials-results/",
                            now,
                        ),
                    )
                    written += 1
                print(f"  fin {c['short']} {'cons' if cons else 'standalone'}: {len(rows)} periods")
    return written
