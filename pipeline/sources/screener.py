"""Screener.in financial backbone (quarters, balance sheet, cash flow, ratios).

The framework doc deprioritizes Screener for "core facts" - but for the
financial backbone it's the most stable scrapeable source we can hit
from a CI runner. Every row is tagged source='screener' so analysts
know to cross-check against the company's own filings.

We parse four sections per company:
  <section id="quarters">       -> financials (revenue, op profit, PAT)
  <section id="balance-sheet">  -> balance_sheet
  <section id="cash-flow">      -> cash_flow
  <section id="ratios">         -> ratios (debtor days, ROCE, etc.)
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

# ---- label aliases (all matched after _norm_label() normalization) ----
SALES_LABELS = (
    "sales", "revenue", "net sales",
    "income from operations", "revenue from operations", "interest earned",
)
OPPROFIT_LABELS = ("operating profit", "financing profit", "operating income")
NET_LABELS = ("net profit", "profit after tax", "profit for the period")

BS_LABELS = {
    "equity_capital":  ("equity capital", "share capital"),
    "reserves":        ("reserves",),
    "borrowings":      ("borrowings",),
    "other_liab":      ("other liabilities",),
    "total_liab":      ("total liabilities",),
    "fixed_assets":    ("fixed assets",),
    "cwip":            ("cwip", "capital work in progress"),
    "investments":     ("investments",),
    "other_assets":    ("other assets",),
    "total_assets":    ("total assets",),
}

CF_LABELS = {
    "cfo":            ("cash from operating activity", "cash from operating activities"),
    "cfi":            ("cash from investing activity", "cash from investing activities"),
    "cff":            ("cash from financing activity", "cash from financing activities"),
    "net_cash_flow":  ("net cash flow",),
}

# Ratios are annual rows; metric per row.
RATIO_LABELS = {
    "debtor_days":      ("debtor days",),
    "inventory_days":   ("inventory days",),
    "days_payable":     ("days payable",),
    "cash_conv_cycle":  ("cash conversion cycle",),
    "working_cap_days": ("working capital days",),
    "roce_pct":         ("roce %", "roce"),
    "roe_pct":          ("return on equity %", "return on equity", "roe"),
}

MONTH_MAP = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
PERIOD_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")


def _norm_label(s: str) -> str:
    return " ".join(s.replace(" ", " ").replace("+", " ").split()).lower()


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
    s = str(x).strip().replace(",", "").replace("%", "")
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


def _parse_table(html: str, section_id: str) -> tuple[list[tuple[str | None, str]], dict[str, list[str]]]:
    """Return (period_headers, label->row_values) for a given <section>."""
    soup = BeautifulSoup(html, "lxml")
    sec = soup.find("section", id=section_id)
    if not sec:
        return [], {}
    table = sec.find("table")
    if not table:
        return [], {}
    headers = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    periods = [(_period_end(h), h) for h in headers[1:]]
    series: dict[str, list[str]] = {}
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = _norm_label(cells[0].get_text(" ", strip=True))
        if not label:
            continue
        series[label] = [c.get_text(strip=True) for c in cells[1:]]
    return periods, series


def _row_for(series: dict[str, list[str]], aliases: tuple[str, ...]) -> list[str] | None:
    for a in aliases:
        if a in series:
            return series[a]
    return None


def _parse_quarters(html: str) -> list[dict]:
    periods, series = _parse_table(html, "quarters")
    sales = _row_for(series, SALES_LABELS) or []
    opp = _row_for(series, OPPROFIT_LABELS) or []
    netp = _row_for(series, NET_LABELS) or []
    out = []
    for i, (period_end, raw) in enumerate(periods):
        if not period_end:
            continue
        out.append({
            "period_end": period_end,
            "period_label": raw,
            "revenue":    _num(sales[i]) if i < len(sales) else None,
            "op_profit":  _num(opp[i])   if i < len(opp)   else None,
            "net_profit": _num(netp[i])  if i < len(netp)  else None,
        })
    return out


def _parse_yearly(html: str, section: str, label_map: dict) -> list[dict]:
    """Generic parser for the annual balance-sheet / cash-flow / ratios tables."""
    periods, series = _parse_table(html, section)
    rows = []
    for i, (period_end, raw) in enumerate(periods):
        if not period_end:
            continue
        rec: dict = {"period_end": period_end, "period_label": raw}
        for key, aliases in label_map.items():
            r = _row_for(series, aliases)
            rec[key] = _num(r[i]) if r and i < len(r) else None
        rows.append(rec)
    return rows


def ingest(companies: Iterable[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    debug = RAW_DIR / "screener"
    debug.mkdir(parents=True, exist_ok=True)
    total_rows = 0

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

                tag = "C" if cons else "S"
                url = f"{BASE}/{sym}/" + ("consolidated/" if cons else "")

                # 1. Quarterly P&L
                qrows = _parse_quarters(html)
                for r in qrows:
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO financials(
                            company_id, period_end, period_type, consolidated,
                            revenue, ebitda, pat, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (c["id"], r["period_end"], "quarterly", 1 if cons else 0,
                         r["revenue"], r["op_profit"], r["net_profit"],
                         json.dumps(r), "screener", url, now),
                    )
                    total_rows += cur.rowcount or 0

                # 2. Balance sheet
                bs = _parse_yearly(html, "balance-sheet", BS_LABELS)
                for r in bs:
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO balance_sheet(
                            company_id, period_end, consolidated,
                            equity_capital, reserves, borrowings, other_liab,
                            total_liab, fixed_assets, cwip, investments,
                            other_assets, total_assets, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (c["id"], r["period_end"], 1 if cons else 0,
                         r["equity_capital"], r["reserves"], r["borrowings"],
                         r["other_liab"], r["total_liab"], r["fixed_assets"],
                         r["cwip"], r["investments"], r["other_assets"],
                         r["total_assets"], json.dumps(r),
                         "screener", url, now),
                    )
                    total_rows += cur.rowcount or 0

                # 3. Cash flow
                cf = _parse_yearly(html, "cash-flow", CF_LABELS)
                for r in cf:
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO cash_flow(
                            company_id, period_end, consolidated,
                            cfo, cfi, cff, net_cash_flow, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (c["id"], r["period_end"], 1 if cons else 0,
                         r["cfo"], r["cfi"], r["cff"], r["net_cash_flow"],
                         json.dumps(r), "screener", url, now),
                    )
                    total_rows += cur.rowcount or 0

                # 4. Ratios
                rt = _parse_yearly(html, "ratios", RATIO_LABELS)
                for r in rt:
                    cur = conn.execute(
                        """
                        INSERT OR REPLACE INTO ratios(
                            company_id, period_end, consolidated,
                            debtor_days, inventory_days, days_payable,
                            cash_conv_cycle, working_cap_days,
                            roce_pct, roe_pct, raw_json,
                            source, source_url, fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (c["id"], r["period_end"], 1 if cons else 0,
                         r["debtor_days"], r["inventory_days"], r["days_payable"],
                         r["cash_conv_cycle"], r["working_cap_days"],
                         r["roce_pct"], r["roe_pct"], json.dumps(r),
                         "screener", url, now),
                    )
                    total_rows += cur.rowcount or 0

                (debug / f"{sym}_{tag}.summary.json").write_text(json.dumps({
                    "quarters": len(qrows), "balance_sheet": len(bs),
                    "cash_flow": len(cf), "ratios": len(rt),
                }, indent=2))

                print(f"  scr {c['short']} {'cons' if cons else 'standalone'}: "
                      f"Q={len(qrows)} BS={len(bs)} CF={len(cf)} Ratios={len(rt)}")
                if qrows:
                    break
    return total_rows
