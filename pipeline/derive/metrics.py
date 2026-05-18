"""Derived metrics: things the dashboard needs that we don't fetch directly
but can compute from rows we already have.

Computed per (company, period_end, consolidated):
  - ebitda_margin_pct      = ebitda / revenue * 100
  - net_margin_pct         = pat / revenue * 100
  - revenue_growth_yoy_pct = current_revenue / same_quarter_last_year - 1
  - ebitda_growth_yoy_pct
  - pat_growth_yoy_pct
  - cfo_to_ebitda_pct      = cfo / sum(quarterly_ebitda for the year) * 100
  - fcf_inr_cr             = cfo - (proxy capex from delta in fixed_assets+CWIP)
  - debt_to_equity         = borrowings / (equity_capital + reserves)
  - net_worth_inr_cr       = equity_capital + reserves
  - asset_turnover         = revenue / total_assets

Also peer-relative percentiles within the company's own bucket
(framework rule 14: 'compare only with relevant peers').
"""

from __future__ import annotations
import json
from datetime import datetime, timezone

from ..db import connect

DERIVED_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS derived (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    period_end    TEXT NOT NULL,
    period_type   TEXT NOT NULL,         -- quarterly | annual
    consolidated  INTEGER NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL,
    unit          TEXT,
    formula       TEXT,
    bucket_rank   INTEGER,               -- 1 = best in bucket for the metric
    bucket_n      INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE(company_id, period_end, period_type, consolidated, metric)
);
CREATE INDEX IF NOT EXISTS idx_derived_company_metric
    ON derived(company_id, metric);
CREATE INDEX IF NOT EXISTS idx_derived_period
    ON derived(period_end);
"""


def _ensure_schema(conn):
    conn.executescript(DERIVED_TABLE_SCHEMA)


def _upsert(conn, company_id, period_end, period_type, cons, metric,
            value, unit, formula, now):
    if value is None:
        return
    try:
        v = float(value)
    except Exception:
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO derived(
            company_id, period_end, period_type, consolidated,
            metric, value, unit, formula, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (company_id, period_end, period_type, 1 if cons else 0,
         metric, v, unit, formula, now),
    )


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _compute_quarterly(conn, now):
    """Quarterly margin %, growth %."""
    rows = conn.execute(
        """
        SELECT id, company_id, period_end, consolidated,
               revenue, ebitda, pat
        FROM financials
        WHERE period_type='quarterly'
        ORDER BY company_id, consolidated, period_end
        """
    ).fetchall()

    # build a lookup so we can find same-quarter-last-year
    by_key: dict[tuple, dict] = {}
    for r in rows:
        by_key[(r["company_id"], r["consolidated"], r["period_end"])] = dict(r)

    for r in rows:
        cid = r["company_id"]
        cons = bool(r["consolidated"])
        pe = r["period_end"]
        rev, ebitda, pat = r["revenue"], r["ebitda"], r["pat"]

        # Margins
        _upsert(conn, cid, pe, "quarterly", cons, "ebitda_margin_pct",
                _div(ebitda, rev) * 100 if rev and ebitda else None,
                "%", "ebitda/revenue*100", now)
        _upsert(conn, cid, pe, "quarterly", cons, "net_margin_pct",
                _div(pat, rev) * 100 if rev and pat else None,
                "%", "pat/revenue*100", now)

        # YoY growth: same period one year earlier
        yr, mo, day = pe.split("-")
        prev_pe = f"{int(yr)-1:04d}-{mo}-{day}"
        prev = by_key.get((cid, r["consolidated"], prev_pe))
        if prev:
            _upsert(conn, cid, pe, "quarterly", cons, "revenue_growth_yoy_pct",
                    (rev / prev["revenue"] - 1) * 100
                    if rev and prev.get("revenue") else None,
                    "%", "rev/rev_yr_ago - 1", now)
            _upsert(conn, cid, pe, "quarterly", cons, "ebitda_growth_yoy_pct",
                    (ebitda / prev["ebitda"] - 1) * 100
                    if ebitda and prev.get("ebitda") else None,
                    "%", "ebitda/ebitda_yr_ago - 1", now)
            _upsert(conn, cid, pe, "quarterly", cons, "pat_growth_yoy_pct",
                    (pat / prev["pat"] - 1) * 100
                    if pat and prev.get("pat") else None,
                    "%", "pat/pat_yr_ago - 1", now)


def _compute_annual(conn, now):
    """Annual: debt/equity, asset turnover, FCF, CFO/EBITDA."""
    bs_rows = conn.execute(
        """
        SELECT company_id, period_end, consolidated,
               equity_capital, reserves, borrowings,
               total_assets, fixed_assets, cwip
        FROM balance_sheet
        """
    ).fetchall()
    cf_rows = conn.execute(
        "SELECT company_id, period_end, consolidated, cfo FROM cash_flow"
    ).fetchall()

    cf_by_key = {(r["company_id"], r["consolidated"], r["period_end"]): r["cfo"]
                 for r in cf_rows}

    # for asset turnover we need TTM revenue per annual close - approximate
    # by FYE quarter revenue * 4 (rough); better to sum 4 quarters
    fin_rows = conn.execute(
        """SELECT company_id, period_end, consolidated, revenue, ebitda
           FROM financials WHERE period_type='quarterly'"""
    ).fetchall()
    # group quarterly into ttm by period_end (FYE March):
    ttm_revenue: dict[tuple, float] = {}
    ttm_ebitda:  dict[tuple, float] = {}
    by_co_cons: dict[tuple, list] = {}
    for f in fin_rows:
        by_co_cons.setdefault((f["company_id"], f["consolidated"]), []).append(f)
    for key, lst in by_co_cons.items():
        lst.sort(key=lambda x: x["period_end"])
        for i, q in enumerate(lst):
            past = [x for x in lst[: i + 1] if (x["period_end"][:7] != q["period_end"][:7] or x is q)][-4:]
            if len(past) < 4:
                continue
            rev_sum = sum(p["revenue"] for p in past if p["revenue"] is not None)
            eb_sum  = sum(p["ebitda"]  for p in past if p["ebitda"]  is not None)
            ttm_revenue[(key[0], key[1], q["period_end"])] = rev_sum
            ttm_ebitda[(key[0], key[1], q["period_end"])]  = eb_sum

    # Annual capex proxy: delta(fixed_assets + cwip) between consecutive AR ends
    bs_sorted: dict[tuple, list] = {}
    for r in bs_rows:
        bs_sorted.setdefault((r["company_id"], r["consolidated"]), []).append(dict(r))
    for k, lst in bs_sorted.items():
        lst.sort(key=lambda x: x["period_end"])

    for k, lst in bs_sorted.items():
        for i, r in enumerate(lst):
            cid = r["company_id"]; cons = bool(r["consolidated"])
            pe = r["period_end"]
            equity = (r.get("equity_capital") or 0) + (r.get("reserves") or 0)
            _upsert(conn, cid, pe, "annual", cons, "net_worth_inr_cr",
                    equity if equity else None, "INR crore",
                    "equity_capital + reserves", now)
            _upsert(conn, cid, pe, "annual", cons, "debt_to_equity",
                    _div(r.get("borrowings"), equity), "ratio",
                    "borrowings / net_worth", now)
            ttm_r = ttm_revenue.get((cid, r["consolidated"], pe))
            ttm_e = ttm_ebitda.get((cid, r["consolidated"], pe))
            _upsert(conn, cid, pe, "annual", cons, "ttm_revenue_inr_cr",
                    ttm_r, "INR crore", "sum(quarter.revenue, 4q)", now)
            _upsert(conn, cid, pe, "annual", cons, "ttm_ebitda_inr_cr",
                    ttm_e, "INR crore", "sum(quarter.ebitda, 4q)", now)
            _upsert(conn, cid, pe, "annual", cons, "asset_turnover",
                    _div(ttm_r, r.get("total_assets")), "x",
                    "ttm_revenue / total_assets", now)

            cfo = cf_by_key.get((cid, r["consolidated"], pe))
            _upsert(conn, cid, pe, "annual", cons, "cfo_to_ebitda_pct",
                    _div(cfo, ttm_e) * 100 if cfo and ttm_e else None, "%",
                    "cfo / ttm_ebitda * 100", now)

            if i > 0:
                prev = lst[i - 1]
                cur_gb  = (r.get("fixed_assets")  or 0) + (r.get("cwip") or 0)
                prev_gb = (prev.get("fixed_assets") or 0) + (prev.get("cwip") or 0)
                capex = cur_gb - prev_gb if cur_gb and prev_gb else None
                if capex is not None:
                    _upsert(conn, cid, pe, "annual", cons, "capex_proxy_inr_cr",
                            capex, "INR crore",
                            "delta(fixed_assets + cwip)", now)
                    if cfo is not None:
                        _upsert(conn, cid, pe, "annual", cons, "fcf_proxy_inr_cr",
                                cfo - capex, "INR crore",
                                "cfo - capex_proxy", now)


def _compute_peer_ranks(conn, now):
    """For each (period_end, metric), rank companies inside their bucket.
    rank=1 means best in bucket for that metric."""
    BETTER_HIGH = {
        "ebitda_margin_pct", "net_margin_pct",
        "revenue_growth_yoy_pct", "ebitda_growth_yoy_pct", "pat_growth_yoy_pct",
        "asset_turnover", "cfo_to_ebitda_pct", "fcf_proxy_inr_cr",
        "ttm_revenue_inr_cr", "ttm_ebitda_inr_cr", "net_worth_inr_cr",
    }
    BETTER_LOW = {"debt_to_equity"}

    rows = conn.execute(
        """
        SELECT d.id, d.company_id, d.period_end, d.metric, d.value,
               co.bucket
        FROM derived d JOIN companies co ON co.id = d.company_id
        WHERE d.consolidated = 1
        """
    ).fetchall()

    # group by (period_end, metric, bucket)
    groups: dict[tuple, list] = {}
    for r in rows:
        if r["value"] is None:
            continue
        groups.setdefault((r["period_end"], r["metric"], r["bucket"]), []).append(r)

    for (pe, metric, bucket), lst in groups.items():
        if metric in BETTER_HIGH:
            lst.sort(key=lambda x: x["value"], reverse=True)
        elif metric in BETTER_LOW:
            lst.sort(key=lambda x: x["value"])
        else:
            continue
        n = len(lst)
        for rank, r in enumerate(lst, start=1):
            conn.execute(
                "UPDATE derived SET bucket_rank=?, bucket_n=? WHERE id=?",
                (rank, n, r["id"]),
            )


def ingest() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        _ensure_schema(conn)
        # wipe before recompute - it's cheap and ensures fresh ranks
        conn.execute("DELETE FROM derived")
        _compute_quarterly(conn, now)
        _compute_annual(conn, now)
        _compute_peer_ranks(conn, now)
        n = conn.execute("SELECT COUNT(*) FROM derived").fetchone()[0]
    print(f"  derive: {n} derived metric rows")
    return n
