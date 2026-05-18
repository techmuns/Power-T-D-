"""Export a single tidy JSON per company.

This is the contract the dashboard reads from. One file per company at
`data/export/<slug>.json` plus a top-level `data/export/index.json`
with the universe summary. Filenames are URL-safe slugs (no spaces or
ampersands).
"""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR
from ..db import connect


def _slug(name: str) -> str:
    """URL-safe filename from a company short name."""
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "company"


EXPORT_DIR = DATA_DIR / "export"

# Fields used by the dashboard. Coverage = how many of these are populated.
COVERAGE_KEYS = [
    "has_quarterly_financials",
    "has_balance_sheet",
    "has_cash_flow",
    "has_ratios",
    "has_market_data",
    "has_documents_text",
    "has_voltage_features",
    "has_capacity_features",
    "has_order_features",
    "has_customer_features",
    "has_capex_features",
    "has_derived_margins",
    "has_derived_growth",
    "has_derived_balance_sheet",
    "has_peer_ranks",
    "has_scorecard_complete",
]


def _rows(conn, sql, *params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _strip_nulls(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if v is not None} for r in rows]


def _coverage(co_data: dict) -> tuple[int, dict[str, bool]]:
    checks: dict[str, bool] = {}
    fin_q = co_data.get("financials", {}).get("quarterly", [])
    checks["has_quarterly_financials"] = len(fin_q) > 0
    checks["has_balance_sheet"] = len(co_data.get("balance_sheet", [])) > 0
    checks["has_cash_flow"]     = len(co_data.get("cash_flow", [])) > 0
    checks["has_ratios"]        = len(co_data.get("ratios", [])) > 0
    checks["has_market_data"]   = co_data.get("market") is not None
    docs = co_data.get("documents", [])
    checks["has_documents_text"] = any(d.get("has_text") for d in docs)
    feats = co_data.get("features", {}) or {}
    # Feature presence checks are matched permissively against feature
    # names. The heuristic extractor and the manual / LLM extractors use
    # slightly different naming conventions; we count either as present.
    feat_names = list(feats.keys())
    def has_any(*needles: str) -> bool:
        return any(any(n in name for n in needles) for name in feat_names)
    checks["has_voltage_features"]  = has_any("voltage_class", "voltage_capability", "kv")
    checks["has_capacity_features"] = has_any("capacity_", "capacity.", "mva", "gva", "_mt", "ckm")
    checks["has_order_features"]    = has_any("order_book", "order_win", "order_inflow", "tender_pipeline")
    checks["has_customer_features"] = has_any("customer_mention", "customer", "tbcb", "approval")
    checks["has_capex_features"]    = has_any("capex", "regulated_equity")
    derived = co_data.get("derived", [])
    checks["has_derived_margins"]   = any(d["metric"] == "ebitda_margin_pct" for d in derived)
    checks["has_derived_growth"]    = any(d["metric"] == "revenue_growth_yoy_pct" for d in derived)
    checks["has_derived_balance_sheet"] = any(d["metric"] in ("debt_to_equity", "net_worth_inr_cr") for d in derived)
    checks["has_peer_ranks"]        = any(d.get("bucket_rank") for d in derived)
    sc = co_data.get("scorecard", [])
    checks["has_scorecard_complete"] = sum(1 for s in sc if s["score"] is not None) >= 7
    pct = int(round(100 * sum(checks.values()) / max(1, len(checks))))
    return pct, checks


def _company_payload(conn, c: dict) -> dict:
    cid = c["id"]

    fin_q = _rows(conn, """
        SELECT period_end, consolidated, revenue, ebitda, pat
        FROM financials WHERE company_id=? AND period_type='quarterly'
        ORDER BY period_end
    """, cid)

    bs = _rows(conn, """
        SELECT period_end, consolidated,
               equity_capital, reserves, borrowings, other_liab, total_liab,
               fixed_assets, cwip, investments, other_assets, total_assets
        FROM balance_sheet WHERE company_id=? ORDER BY period_end
    """, cid)

    cf = _rows(conn, """
        SELECT period_end, consolidated, cfo, cfi, cff, net_cash_flow
        FROM cash_flow WHERE company_id=? ORDER BY period_end
    """, cid)

    rt = _rows(conn, """
        SELECT period_end, consolidated,
               debtor_days, inventory_days, days_payable, cash_conv_cycle,
               working_cap_days, roce_pct, roe_pct
        FROM ratios WHERE company_id=? ORDER BY period_end
    """, cid)

    derived = _rows(conn, """
        SELECT period_end, period_type, consolidated, metric, value,
               unit, formula, bucket_rank, bucket_n
        FROM derived WHERE company_id=?
        ORDER BY period_end, metric
    """, cid)

    docs = _rows(conn, """
        SELECT doc_kind, page_count, pdf_bytes, pdf_url,
               extract_status, extracted_at,
               (LENGTH(full_text) > 0) AS has_text
        FROM documents WHERE company_id=?
        ORDER BY extracted_at DESC
    """, cid)
    for d in docs:
        d["has_text"] = bool(d.get("has_text"))
        d.pop("pdf_bytes", None)

    feats_raw = _rows(conn, """
        SELECT feature, value_text, value_num, unit, evidence,
               extractor, source_tag, confidence, page_no, document_id
        FROM features WHERE company_id=?
    """, cid)
    features: dict[str, list[dict]] = {}
    for f in feats_raw:
        features.setdefault(f["feature"], []).append(
            {k: v for k, v in f.items() if v is not None and k != "feature"}
        )

    market = conn.execute("""
        SELECT price, pct_change, market_cap_cr, pe_ratio, pb_ratio,
               dividend_yield, week52_high, week52_low, book_value, eps,
               face_value, fetched_at
        FROM market_data WHERE company_id=?
        ORDER BY fetched_at DESC LIMIT 1
    """, (cid,)).fetchone()
    market = dict(market) if market else None

    sc = _rows(conn, """
        SELECT factor, score, evidence, inputs
        FROM scorecard WHERE company_id=?
        ORDER BY factor
    """, cid)
    for s in sc:
        if s.get("inputs"):
            try:
                s["inputs"] = json.loads(s["inputs"])
            except Exception:
                pass

    payload = {
        "company": {
            "name": c["name"], "short": c["short"],
            "bse": c["bse"], "nse": c["nse"],
            "bucket": c["bucket"], "secondary": c["secondary"],
        },
        "financials":     {"quarterly": _strip_nulls(fin_q)},
        "balance_sheet":  _strip_nulls(bs),
        "cash_flow":      _strip_nulls(cf),
        "ratios":         _strip_nulls(rt),
        "derived":        _strip_nulls(derived),
        "market":         market,
        "documents":      docs[:50],
        "features":       features,
        "scorecard":      sc,
    }
    pct, checks = _coverage(payload)
    payload["coverage_pct"]    = pct
    payload["coverage_checks"] = checks
    return payload


def ingest() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    summary = []
    with connect() as conn:
        for c in [dict(r) for r in conn.execute(
                "SELECT * FROM companies ORDER BY short").fetchall()]:
            p = _company_payload(conn, c)
            slug = _slug(c["short"])
            out = EXPORT_DIR / f"{slug}.json"
            out.write_text(json.dumps(p, default=str, indent=2))
            summary.append({
                "short": c["short"],
                "slug":  slug,
                "bucket": c["bucket"],
                "coverage_pct": p["coverage_pct"],
                "score_total": sum(s["score"] for s in p["scorecard"]
                                   if s.get("score") is not None),
                "scored_factors": sum(1 for s in p["scorecard"]
                                      if s.get("score") is not None),
            })
            written += 1

    summary.sort(key=lambda x: (x["bucket"], -(x["coverage_pct"] or 0)))
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "companies": summary,
        "totals": {
            "companies":     len(summary),
            "avg_coverage":  round(sum(s["coverage_pct"] or 0 for s in summary) / max(1, len(summary)), 1),
        },
    }
    (EXPORT_DIR / "index.json").write_text(json.dumps(index, indent=2))
    print(f"  export: {written} company JSONs + index.json")
    print(f"  export: avg coverage {index['totals']['avg_coverage']}%")
    return written
