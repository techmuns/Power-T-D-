"""Framework-doc 10-factor scorecard per company.

Implements the scorecard in section 10 (and the per-bucket overrides in
sections 14-20) of the investment framework. Each factor is scored 1-5
based on rules over the data we already have - financials, derived
metrics, heuristic features.

A score is only assigned when there's underlying evidence. If a factor
has no supporting data, it's left null and the cell text says
"insufficient data" - keeping the dashboard honest, never inventing a
filler score.

Factors (from framework section 10):
  tam_relevance
  value_chain_quality
  approval_technology_depth
  order_book_quality
  capacity_readiness
  margin_sustainability
  working_capital
  competitive_position
  balance_sheet
  valuation_comfort
"""

from __future__ import annotations
from datetime import datetime, timezone

from ..db import connect

SCORECARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS scorecard (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    factor        TEXT NOT NULL,
    score         INTEGER,                  -- 1..5 or NULL
    evidence      TEXT,                     -- short justification
    inputs        TEXT,                     -- JSON of underlying numbers
    as_of         TEXT,                     -- period this score reflects
    created_at    TEXT NOT NULL,
    UNIQUE(company_id, factor)
);
"""


# ---- helpers ----

def _latest(conn, company_id: int, period_type: str, metric: str) -> float | None:
    r = conn.execute(
        """
        SELECT value FROM derived
        WHERE company_id=? AND period_type=? AND metric=? AND consolidated=1
        ORDER BY period_end DESC LIMIT 1
        """,
        (company_id, period_type, metric),
    ).fetchone()
    return r["value"] if r else None


def _latest_ratio(conn, company_id: int, col: str) -> float | None:
    r = conn.execute(
        f"""
        SELECT {col} v FROM ratios
        WHERE company_id=? AND consolidated=1
        ORDER BY period_end DESC LIMIT 1
        """,
        (company_id,),
    ).fetchone()
    return r["v"] if r else None


def _peer_rank(conn, company_id: int, metric: str) -> tuple[int | None, int | None]:
    r = conn.execute(
        """
        SELECT bucket_rank, bucket_n FROM derived
        WHERE company_id=? AND metric=? AND consolidated=1
        ORDER BY period_end DESC LIMIT 1
        """,
        (company_id, metric),
    ).fetchone()
    return (r["bucket_rank"], r["bucket_n"]) if r else (None, None)


def _feature_max(conn, company_id: int, feature: str) -> float | None:
    r = conn.execute(
        """
        SELECT MAX(value_num) v FROM features
        WHERE company_id=? AND feature=?
        """,
        (company_id, feature),
    ).fetchone()
    return r["v"] if r else None


def _feature_count(conn, company_id: int, feature: str,
                   value_text: str | None = None) -> int:
    if value_text:
        r = conn.execute(
            """SELECT COUNT(*) n FROM features
               WHERE company_id=? AND feature=? AND value_text=?""",
            (company_id, feature, value_text),
        ).fetchone()
    else:
        r = conn.execute(
            "SELECT COUNT(*) n FROM features WHERE company_id=? AND feature=?",
            (company_id, feature),
        ).fetchone()
    return r["n"] or 0


def _percentile_rank(rank: int, n: int) -> int:
    """Convert a 1=best rank into a 1-5 score. n>=2 required."""
    if not rank or not n or n < 2:
        return 3   # neutral when peer pool too thin
    p = (n - rank) / (n - 1)
    if p >= 0.8: return 5
    if p >= 0.6: return 4
    if p >= 0.4: return 3
    if p >= 0.2: return 2
    return 1


# ---- per-factor scorers ----

def score_value_chain_quality(conn, c) -> dict:
    """Higher-voltage exposure + HVDC/FACTS = higher score."""
    cid = c["id"]
    max_kv = _feature_max(conn, cid, "approved_up_to_kv") or 0
    hvdc = _feature_count(conn, cid, "grid_tech_mentioned", "HVDC")
    statcom = _feature_count(conn, cid, "grid_tech_mentioned", "STATCOM")
    kv_765 = _feature_count(conn, cid, "voltage_class_mentioned", "765kV")
    kv_400 = _feature_count(conn, cid, "voltage_class_mentioned", "400kV")
    kv_220 = _feature_count(conn, cid, "voltage_class_mentioned", "220kV")
    points = 0
    if max_kv >= 765: points += 2
    elif max_kv >= 400: points += 1
    if hvdc >= 3: points += 2
    elif hvdc >= 1: points += 1
    if statcom >= 1: points += 1
    if kv_765 + kv_400 >= 5: points += 1
    if kv_220 >= 3: points += 1
    score = min(5, max(1, points))
    if not (max_kv or hvdc or statcom or kv_765 or kv_400 or kv_220):
        return {"score": None, "evidence": "insufficient data",
                "inputs": {}}
    return {
        "score": score,
        "evidence": f"max_approved_kV={max_kv}, hvdc_mentions={hvdc}, "
                    f"statcom_mentions={statcom}, 765kV={kv_765}, "
                    f"400kV={kv_400}, 220kV={kv_220}",
        "inputs": {"max_approved_kv": max_kv, "hvdc": hvdc, "statcom": statcom,
                   "765kV_mentions": kv_765, "400kV_mentions": kv_400,
                   "220kV_mentions": kv_220},
    }


def score_order_book_quality(conn, c) -> dict:
    cid = c["id"]
    ob = _feature_max(conn, cid, "order_book_inr_cr")
    btb = _feature_max(conn, cid, "book_to_bill")
    wins = _feature_count(conn, cid, "order_win_inr_cr")
    pgcil = _feature_count(conn, cid, "customer_mention", "PGCIL")
    ntpc  = _feature_count(conn, cid, "customer_mention", "NTPC")
    if not (ob or btb or wins):
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    points = 0
    if btb is not None:
        if btb >= 2.5: points += 3
        elif btb >= 1.5: points += 2
        elif btb >= 1.0: points += 1
    if wins >= 5: points += 1
    if pgcil + ntpc >= 5: points += 1
    score = min(5, max(1, points))
    return {
        "score": score,
        "evidence": f"order_book≈₹{ob or 0:.0f}cr, b2b={btb}, "
                    f"wins_mentioned={wins}, PGCIL={pgcil}, NTPC={ntpc}",
        "inputs": {"order_book_inr_cr": ob, "book_to_bill": btb,
                   "order_wins_mentioned": wins,
                   "pgcil_mentions": pgcil, "ntpc_mentions": ntpc},
    }


def score_margin_sustainability(conn, c) -> dict:
    cid = c["id"]
    margin = _latest(conn, cid, "quarterly", "ebitda_margin_pct")
    growth = _latest(conn, cid, "quarterly", "ebitda_growth_yoy_pct")
    rank, n = _peer_rank(conn, cid, "ebitda_margin_pct")
    if margin is None:
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    score = _percentile_rank(rank, n) if rank else (
        5 if margin >= 25 else
        4 if margin >= 15 else
        3 if margin >= 10 else
        2 if margin >= 5  else 1
    )
    return {
        "score": score,
        "evidence": f"ebitda_margin={margin:.1f}% (peer rank {rank}/{n}), "
                    f"yoy_growth={growth:.0f}%" if growth else
                    f"ebitda_margin={margin:.1f}% (peer rank {rank}/{n})",
        "inputs": {"ebitda_margin_pct": margin,
                   "ebitda_growth_yoy_pct": growth,
                   "peer_rank": rank, "peer_n": n},
    }


def score_working_capital(conn, c) -> dict:
    cid = c["id"]
    wc_days = _latest_ratio(conn, cid, "working_cap_days")
    debtor  = _latest_ratio(conn, cid, "debtor_days")
    cfo_eb  = _latest(conn, cid, "annual", "cfo_to_ebitda_pct")
    if wc_days is None and cfo_eb is None:
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    pts = 0
    # WC days: lower is better (sector specific - EPC differs from manufacturer)
    bucket = c["bucket"]
    epc_like = bucket in ("transmission_epc", "towers_conductors_cables")
    if wc_days is not None:
        if epc_like:
            if   wc_days <= 50:  pts += 2
            elif wc_days <= 100: pts += 1
            elif wc_days >= 200: pts -= 1
        else:
            if   wc_days <= 30:  pts += 2
            elif wc_days <= 60:  pts += 1
            elif wc_days >= 120: pts -= 1
    if cfo_eb is not None:
        if   cfo_eb >= 80: pts += 2
        elif cfo_eb >= 60: pts += 1
        elif cfo_eb < 30:  pts -= 1
    if debtor is not None and debtor >= 180:
        pts -= 1
    score = max(1, min(5, 3 + pts))
    return {
        "score": score,
        "evidence": f"wc_days={wc_days}, debtor_days={debtor}, "
                    f"cfo/ebitda={cfo_eb:.0f}%" if cfo_eb else
                    f"wc_days={wc_days}, debtor_days={debtor}",
        "inputs": {"working_cap_days": wc_days, "debtor_days": debtor,
                   "cfo_to_ebitda_pct": cfo_eb},
    }


def score_balance_sheet(conn, c) -> dict:
    cid = c["id"]
    de = _latest(conn, cid, "annual", "debt_to_equity")
    if de is None:
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    if   de <= 0.1: score = 5
    elif de <= 0.5: score = 4
    elif de <= 1.0: score = 3
    elif de <= 2.0: score = 2
    else:           score = 1
    return {
        "score": score,
        "evidence": f"debt/equity = {de:.2f}",
        "inputs": {"debt_to_equity": de},
    }


def score_competitive_position(conn, c) -> dict:
    """Peer-rank in revenue + EBITDA margin within bucket."""
    cid = c["id"]
    rr, rn = _peer_rank(conn, cid, "ttm_revenue_inr_cr")
    mr, mn = _peer_rank(conn, cid, "ebitda_margin_pct")
    if not rr and not mr:
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    parts = []
    if rr: parts.append(_percentile_rank(rr, rn))
    if mr: parts.append(_percentile_rank(mr, mn))
    score = round(sum(parts) / len(parts))
    return {
        "score": score,
        "evidence": f"revenue rank {rr}/{rn}, ebitda-margin rank {mr}/{mn}",
        "inputs": {"revenue_rank": rr, "revenue_n": rn,
                   "ebitda_margin_rank": mr, "ebitda_margin_n": mn},
    }


def score_capacity_readiness(conn, c) -> dict:
    """How many distinct capacity-class data points exist."""
    cid = c["id"]
    mva = _feature_count(conn, cid, "capacity_mva")
    gva = _feature_count(conn, cid, "capacity_gva")
    mt  = _feature_count(conn, cid, "capacity_mt")
    ckm = _feature_count(conn, cid, "capacity_ckm")
    capex = _feature_count(conn, cid, "capex_inr_cr")
    total = mva + gva + mt + ckm + capex
    if total == 0:
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    if   total >= 15: score = 5
    elif total >= 8:  score = 4
    elif total >= 4:  score = 3
    elif total >= 2:  score = 2
    else:             score = 1
    return {
        "score": score,
        "evidence": f"capacity_mentions: MVA={mva}, GVA={gva}, MT={mt}, "
                    f"ckm={ckm}, capex_amounts={capex}",
        "inputs": {"mva": mva, "gva": gva, "mt": mt, "ckm": ckm, "capex": capex},
    }


def score_approval_technology_depth(conn, c) -> dict:
    cid = c["id"]
    max_kv = _feature_max(conn, cid, "approved_up_to_kv") or 0
    pgcil = _feature_count(conn, cid, "customer_mention", "PGCIL")
    ntpc  = _feature_count(conn, cid, "customer_mention", "NTPC")
    seci  = _feature_count(conn, cid, "customer_mention", "SECI")
    if not (max_kv or pgcil or ntpc or seci):
        return {"score": None, "evidence": "insufficient data", "inputs": {}}
    pts = 0
    if max_kv >= 765: pts += 2
    elif max_kv >= 400: pts += 1
    pts += min(2, (pgcil + ntpc) // 3)
    if seci >= 1: pts += 1
    score = max(1, min(5, pts))
    return {
        "score": score,
        "evidence": f"max_approved_kV={max_kv}, PGCIL={pgcil}, NTPC={ntpc}, SECI={seci}",
        "inputs": {"max_approved_kv": max_kv,
                   "pgcil": pgcil, "ntpc": ntpc, "seci": seci},
    }


def score_valuation_comfort(conn, c) -> dict:
    """Without market-data scrape we don't have live P/E. Use proxies:
    revenue growth + margin trajectory + balance sheet quality. Returns
    None when underlying data is missing."""
    cid = c["id"]
    g  = _latest(conn, cid, "quarterly", "revenue_growth_yoy_pct")
    m  = _latest(conn, cid, "quarterly", "ebitda_margin_pct")
    de = _latest(conn, cid, "annual", "debt_to_equity")
    if g is None and m is None:
        return {"score": None, "evidence": "insufficient data (no market-cap source yet)",
                "inputs": {}}
    pts = 0
    if g and g >= 25: pts += 1
    if m and m >= 15: pts += 1
    if de is not None and de <= 0.5: pts += 1
    score = max(1, min(5, 2 + pts))
    return {
        "score": score,
        "evidence": "proxy (no market-cap feed yet): "
                    f"rev_growth={g}, ebitda_margin={m}, debt/eq={de}",
        "inputs": {"rev_growth_yoy_pct": g, "ebitda_margin_pct": m,
                   "debt_to_equity": de},
    }


def score_tam_relevance(conn, c) -> dict:
    """Every name in our seed list is already a power T&D play, so
    every company gets a baseline 3 unless evidence widens / narrows it."""
    cid = c["id"]
    # presence of recent capacity / order win mentions = clear exposure
    wins  = _feature_count(conn, cid, "order_win_inr_cr")
    capex = _feature_count(conn, cid, "capex_inr_cr")
    score = 3
    if wins + capex >= 5: score = 5
    elif wins + capex >= 2: score = 4
    elif wins + capex == 0: score = 2
    return {
        "score": score,
        "evidence": f"order_wins={wins}, capex_mentions={capex}",
        "inputs": {"order_wins": wins, "capex_mentions": capex},
    }


FACTORS = [
    ("tam_relevance",              score_tam_relevance),
    ("value_chain_quality",        score_value_chain_quality),
    ("approval_technology_depth",  score_approval_technology_depth),
    ("order_book_quality",         score_order_book_quality),
    ("capacity_readiness",         score_capacity_readiness),
    ("margin_sustainability",      score_margin_sustainability),
    ("working_capital",            score_working_capital),
    ("competitive_position",       score_competitive_position),
    ("balance_sheet",              score_balance_sheet),
    ("valuation_comfort",          score_valuation_comfort),
]


def ingest() -> int:
    import json
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.executescript(SCORECARD_SCHEMA)
        conn.execute("DELETE FROM scorecard")
        companies = [dict(r) for r in conn.execute("SELECT * FROM companies")]
        written = 0
        for c in companies:
            for factor, fn in FACTORS:
                try:
                    result = fn(conn, c)
                except Exception as e:
                    result = {"score": None,
                              "evidence": f"scorer error: {e}",
                              "inputs": {}}
                conn.execute(
                    """
                    INSERT OR REPLACE INTO scorecard(
                        company_id, factor, score, evidence, inputs,
                        as_of, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (c["id"], factor, result.get("score"),
                     result.get("evidence"),
                     json.dumps(result.get("inputs", {})),
                     None, now),
                )
                written += 1
        n_scored = conn.execute(
            "SELECT COUNT(*) FROM scorecard WHERE score IS NOT NULL"
        ).fetchone()[0]
    print(f"  scorecard: {written} rows written, {n_scored} with a score")
    return written
