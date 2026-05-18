"""Screener.in financial backbone.

The framework doc deprioritizes Screener for "core facts" - but for the
financial backbone (quarterly revenue / operating-profit / PAT) it's the
most stable scrapeable source we can hit from a CI runner. Every row is
tagged source='screener' so analysts know to cross-check against the
company's own filed results before using a number in a memo.

URL form:
  https://www.screener.in/company/POWERGRID/consolidated/
  https://www.screener.in/company/POWERGRID/                # standalone

Quarterly table lives under <section id="quarters"> in a single
<table>, with month-year headers ("Mar 2024", "Jun 2024", ...) and
rows for Sales, Operating Profit, Net Profit, etc.
"""

from __future__ import annotations
import calendar
import json
import re
from datetime import datetime, timezone
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import UA, RAW_DIR
from ..db import connect

BASE = "https://www.screener.in/company"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# All comparisons happen after _norm_label() normalization, so list the
# labels here in lower-case-ascii form without trailing "+" markers.
# Screener varies the label by industry template (utility / manufacturer
# / NBFC / bank), hence the union.
SALES_LABELS = (
    "sales",
    "revenue",
    "net sales",
    "income from operations",
    "revenue from operations",
    "interest earned",
)
OPPROFIT_LABELS = (
    "operating profit",
    "financing profit",
    "operating income",
)
NET_LABELS = (
    "net profit",
    "profit after tax",
    "profit for the period",
)

MONTH_MAP = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
PERIOD_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")


def _norm_label(s: str) -> str:
    """Fold non-breaking spaces, drop the screener trailing '+' marker,
    collapse whitespace, lowercase."""
    return " ".join(
        s.replace(" ", " ").replace("+", " ").split()
    ).lower()


def _period_end(label: str) -> str | None:
    m = PERIOD_RE.match(label.strip())
    if not m:
        return None
    mon = MONTH_MAP.get(m.group(1).lower())
    if not mon:
        return None
    year = int(m.group(2))
    last_day = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-{last_day:02d}"


def _num(x):
    if x in (None, "", "-"):
        return None
    s = str(x).strip().replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except Exception:
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch(symbol: str, consolidated: bool) -> str | None:
    url = f"{BASE}/{symbol}/" + ("consolidated/" if consolidated else "")
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def _parse_quarters(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sec = soup.find("section", id="quarters")
    if not sec:
        return []
    table = sec.find("table")
    if not table:
        return []

    headers = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    periods = [(_period_end(h), h) for h in headers[1:]]

    # series is keyed by NORMALIZED label
    series: dict[str, list[str]] = {}
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = _norm_label(cells[0].get_text(" ", strip=True))
        vals = [c.get_text(strip=True) for c in cells[1:]]
        series[label] = vals

    def row_for(labels: tuple[str, ...]) -> list[str] | None:
        for lab in labels:
            if lab in series:
                return series[lab]
        return None

    sales = row_for(SALES_LABELS) or []
    opp = row_for(OPPROFIT_LABELS) or []
    netp = row_for(NET_LABELS) or []

    out = []
    for i, (period_end, raw_label) in enumerate(periods):
        if not period_end:
            continue
        out.append({
            "period_end": period_end,
            "period_label": raw_label,
            "revenue": _num(sales[i]) if i < len(sales) else None,
            "op_profit": _num(opp[i]) if i < len(opp) else None,
            "net_profit": _num(netp[i]) if i < len(netp) else None,
        })
    return out


def ingest(companies: Iterable[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    debug = RAW_DIR / "screener"
    debug.mkdir(parents=True, exist_ok=True)
    written = 0
    with connect() as conn:
        for c in companies:
            sym = (c.get("nse") or "").strip()
            if not sym:
                print(f"  scr {c['short']}: no NSE symbol, skip")
                continue
            for cons in (True, False):
                try:
                    html = _fetch(sym, cons)
                except Exception as e:
                    print(f"  scr {c['short']} cons={cons}: FAILED {e}")
                    continue
                if html is None:
                    continue

                rows = _parse_quarters(html)
                tag = "C" if cons else "S"
                if rows:
                    (debug / f"{sym}_{tag}.json").write_text(
                        json.dumps(rows, indent=2)
                    )
                else:
                    (debug / f"{sym}_{tag}.empty.html").write_text(
                        html[:200_000]
                    )

                inserted = 0
                for r in rows:
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO financials(
                            company_id, period_end, period_type, consolidated,
                            revenue, ebitda, pat, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            c["id"], r["period_end"], "quarterly", 1 if cons else 0,
                            r["revenue"], r["op_profit"], r["net_profit"],
                            json.dumps(r),
                            "screener",
                            f"{BASE}/{sym}/" + ("consolidated/" if cons else ""),
                            now,
                        ),
                    )
                    inserted += cur.rowcount or 0
                    written += cur.rowcount or 0
                print(f"  scr {c['short']} {'cons' if cons else 'standalone'}: "
                      f"{len(rows)} quarters, {inserted} new/updated")
                if rows:
                    break
    return written
