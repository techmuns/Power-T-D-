"""One-shot CLI for the pipeline. Invoked by GitHub Actions."""

from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone

from .db import connect, init
from .seed import load_companies
from .sources import bse, screener, sebi, cea


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


def cmd_init(_args):
    init()
    n = load_companies()
    print(f"db initialized; {n} companies seeded")


def cmd_fetch_bse(args):
    companies = _all_companies()
    try:
        n = bse.ingest(companies, days_back=args.days)
        _log_run("bse_announcements", "ok", n)
    except Exception as e:
        _log_run("bse_announcements", "error", 0, str(e))
        raise


def cmd_fetch_financials(_args):
    companies = _all_companies()
    try:
        n = screener.ingest(companies)
        _log_run("screener_financials", "ok", n)
    except Exception as e:
        _log_run("screener_financials", "error", 0, str(e))
        raise


def cmd_fetch_sebi(_args):
    try:
        n = sebi.ingest()
        _log_run("sebi_filings", "ok", n)
    except Exception as e:
        _log_run("sebi_filings", "error", 0, str(e))
        raise


def cmd_fetch_cea(_args):
    try:
        n = cea.ingest()
        _log_run("cea_reports", "ok", n)
    except cea.CEABlocked as e:
        # known issue, not a pipeline bug - log it and continue
        _log_run("cea_reports", "error", 0, f"BLOCKED: {e}")
        print(f"  cea: SKIP - {e}")
    except Exception as e:
        _log_run("cea_reports", "error", 0, str(e))
        raise


def cmd_fetch_all(args):
    cmd_init(args)
    for fn in (cmd_fetch_bse, cmd_fetch_financials, cmd_fetch_sebi, cmd_fetch_cea):
        try:
            fn(args)
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e}", file=sys.stderr)


def cmd_status(_args):
    with connect() as conn:
        for table in ("companies", "announcements", "financials",
                      "sebi_filings", "cea_reports", "fetch_runs"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:20s} {n}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    bse_p = sub.add_parser("fetch-bse")
    bse_p.add_argument("--days", type=int, default=365)
    bse_p.set_defaults(func=cmd_fetch_bse)

    sub.add_parser("fetch-financials").set_defaults(func=cmd_fetch_financials)
    sub.add_parser("fetch-sebi").set_defaults(func=cmd_fetch_sebi)
    sub.add_parser("fetch-cea").set_defaults(func=cmd_fetch_cea)

    all_p = sub.add_parser("fetch-all")
    all_p.add_argument("--days", type=int, default=365)
    all_p.set_defaults(func=cmd_fetch_all)

    sub.add_parser("status").set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
