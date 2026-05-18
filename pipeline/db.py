"""SQLite schema for the Power T&D pipeline.

Every row that holds a fact carries its provenance:
  source        - which website / API
  source_url    - exact URL we hit
  fetched_at    - UTC timestamp
This matches the framework doc's section 3 source-tagging discipline.
"""

import sqlite3
from contextlib import contextmanager
from .config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    short         TEXT NOT NULL,
    bse           TEXT,
    nse           TEXT,
    bucket        TEXT NOT NULL,
    secondary     TEXT,
    UNIQUE(bse), UNIQUE(nse)
);

CREATE TABLE IF NOT EXISTS announcements (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    headline      TEXT,
    category      TEXT,
    subject       TEXT,
    pdf_url       TEXT,
    broadcast_ts  TEXT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    UNIQUE(company_id, headline, broadcast_ts)
);

CREATE TABLE IF NOT EXISTS financials (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    period_end      TEXT NOT NULL,           -- YYYY-MM-DD
    period_type     TEXT NOT NULL,           -- quarterly | annual
    consolidated    INTEGER NOT NULL,        -- 1/0
    revenue         REAL,
    ebitda          REAL,
    pat             REAL,
    raw_json        TEXT,                    -- full payload as returned
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    UNIQUE(company_id, period_end, period_type, consolidated)
);

CREATE TABLE IF NOT EXISTS sebi_filings (
    id            INTEGER PRIMARY KEY,
    title         TEXT,
    filing_type   TEXT,                      -- RHP, DRHP, etc.
    filed_on      TEXT,
    pdf_url       TEXT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    UNIQUE(pdf_url)
);

CREATE TABLE IF NOT EXISTS cea_reports (
    id            INTEGER PRIMARY KEY,
    title         TEXT,
    report_type   TEXT,                      -- exec_summary | transmission | discom
    period        TEXT,                      -- YYYY-MM or YYYY
    pdf_url       TEXT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    UNIQUE(pdf_url)
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,             -- ok | error | partial
    rows_written  INTEGER DEFAULT 0,
    notes         TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
