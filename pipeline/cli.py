"""One-shot CLI for the pipeline. Invoked by GitHub Actions."""

from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone

from .db import connect, init
from .seed import load_companies
from .sources import bse, screener, sebi, cea, manual
from .extract import pdf_text, heuristics, llm
from .derive import metrics


def _all_companies() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM companies ORDER BY short").fetchall()
    return [dict(r) for r in rows]


def _log_run(source: str, status: str, rows: int, notes: str = ""):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO fetch_runs(source, started_at, finished_at, status, rows_written, notes) "
            "VALUES(?,?,?,?,?,?)",
            (source, now, now, status, rows, notes),
        )


def _run(name: str, fn):
    try:
        n = fn()
        _log_run(name, "ok", n)
    except Exception as e:
        _log_run(name, "error", 0, str(e)[:500])
        print(f"FAIL {name}: {e}", file=sys.stderr)


def cmd_init(_args):
    init()
    n = load_companies()
    print(f"db initialized; {n} companies seeded")


def cmd_fetch_bse(args):
    companies = _all_companies()
    _run("bse_announcements",
         lambda: bse.ingest(companies, days_back=args.days))


def cmd_fetch_financials(_args):
    companies = _all_companies()
    _run("screener_financials", lambda: screener.ingest(companies))


def cmd_fetch_sebi(_args):
    _run("sebi_filings", sebi.ingest)


def cmd_fetch_cea(_args):
    try:
        n = cea.ingest()
        _log_run("cea_reports", "ok", n)
    except cea.CEABlocked as e:
        _log_run("cea_reports", "error", 0, f"BLOCKED: {e}"[:500])
        print(f"  cea: SKIP - {e}")
    except Exception as e:
        _log_run("cea_reports", "error", 0, str(e)[:500])


def cmd_ingest_manual(_args):
    _run("manual_uploads", manual.ingest)


def cmd_pdf(args):
    _run(
        "pdf_text",
        lambda: pdf_text.ingest(
            limit_per_company=args.per_company, since=args.since
        ),
    )


def cmd_heuristics(_args):
    # Wipe heuristic features first so re-runs pick up new patterns
    with connect() as conn:
        conn.execute("DELETE FROM features WHERE extractor='heuristic'")
    _run("heuristics", heuristics.ingest)


def cmd_llm(args):
    _run("llm", lambda: llm.ingest(per_company_cap=args.per_company))


def cmd_derive(_args):
    _run("derive", metrics.ingest)


def cmd_fetch_all(args):
    cmd_init(args)
    cmd_fetch_bse(args)
    cmd_fetch_financials(args)
    cmd_fetch_sebi(args)
    cmd_fetch_cea(args)
    cmd_ingest_manual(args)
    cmd_pdf(args)
    cmd_heuristics(args)
    cmd_llm(args)
    cmd_derive(args)


def cmd_status(_args):
    with connect() as conn:
        for table in ("companies", "announcements", "financials",
                      "balance_sheet", "cash_flow", "ratios",
                      "sebi_filings", "cea_reports",
                      "documents", "features", "derived",
                      "fetch_runs"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table:20s} {n}")
            except Exception:
                print(f"  {table:20s} (not yet created)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    bp = sub.add_parser("fetch-bse")
    bp.add_argument("--days", type=int, default=365)
    bp.set_defaults(func=cmd_fetch_bse)

    sub.add_parser("fetch-financials").set_defaults(func=cmd_fetch_financials)
    sub.add_parser("fetch-sebi").set_defaults(func=cmd_fetch_sebi)
    sub.add_parser("fetch-cea").set_defaults(func=cmd_fetch_cea)
    sub.add_parser("ingest-manual").set_defaults(func=cmd_ingest_manual)

    pp = sub.add_parser("pdf")
    pp.add_argument("--per-company", type=int, default=12)
    pp.add_argument("--since", default="2024-01-01")
    pp.set_defaults(func=cmd_pdf)

    sub.add_parser("heuristics").set_defaults(func=cmd_heuristics)
    sub.add_parser("derive").set_defaults(func=cmd_derive)

    lp = sub.add_parser("llm")
    lp.add_argument("--per-company", type=int, default=4)
    lp.set_defaults(func=cmd_llm)

    ap = sub.add_parser("fetch-all")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--per-company", type=int, default=12)
    ap.add_argument("--since", default="2024-01-01")
    ap.set_defaults(func=cmd_fetch_all)

    sub.add_parser("status").set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
