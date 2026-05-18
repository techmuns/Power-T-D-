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

CREATE TABLE IF NOT EXISTS balance_sheet (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    period_end      TEXT NOT NULL,
    consolidated    INTEGER NOT NULL,
    equity_capital  REAL,
    reserves        REAL,
    borrowings      REAL,
    other_liab      REAL,
    total_liab      REAL,
    fixed_assets    REAL,
    cwip            REAL,
    investments     REAL,
    other_assets    REAL,
    total_assets    REAL,
    raw_json        TEXT,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    UNIQUE(company_id, period_end, consolidated)
);

CREATE TABLE IF NOT EXISTS cash_flow (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    period_end      TEXT NOT NULL,
    consolidated    INTEGER NOT NULL,
    cfo             REAL,            -- cash from operating activities
    cfi             REAL,            -- cash from investing activities
    cff             REAL,            -- cash from financing activities
    net_cash_flow   REAL,
    raw_json        TEXT,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    UNIQUE(company_id, period_end, consolidated)
);

CREATE TABLE IF NOT EXISTS ratios (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    period_end      TEXT NOT NULL,
    consolidated    INTEGER NOT NULL,
    debtor_days     REAL,
    inventory_days  REAL,
    days_payable    REAL,
    cash_conv_cycle REAL,
    working_cap_days REAL,
    roce_pct        REAL,
    roe_pct         REAL,
    raw_json        TEXT,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    UNIQUE(company_id, period_end, consolidated)
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id),
    announcement_id INTEGER REFERENCES announcements(id),
    doc_kind        TEXT,             -- investor_presentation | transcript | results | rhp | press_release
    pdf_url         TEXT NOT NULL,
    pdf_sha256      TEXT,
    pdf_bytes       INTEGER,
    page_count      INTEGER,
    full_text       TEXT,             -- extracted text body
    extracted_at    TEXT,
    extract_status  TEXT,              -- ok | failed | skipped
    extract_error   TEXT,
    source          TEXT,
    source_url      TEXT,
    UNIQUE(pdf_url)
);

CREATE TABLE IF NOT EXISTS features (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    document_id     INTEGER REFERENCES documents(id),
    feature         TEXT NOT NULL,        -- e.g. voltage_class, mva_capacity, order_book_inr_cr
    value_text      TEXT,
    value_num       REAL,
    unit            TEXT,
    evidence        TEXT,                  -- verbatim quote
    page_no         INTEGER,
    extractor       TEXT NOT NULL,         -- heuristic | llm
    source_tag      TEXT NOT NULL,         -- fact_company | fact_govt | inference | unknown
    confidence      REAL,
    as_of           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_features_company_feature ON features(company_id, feature);

CREATE TABLE IF NOT EXISTS derived (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    period_end    TEXT NOT NULL,
    period_type   TEXT NOT NULL,
    consolidated  INTEGER NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL,
    unit          TEXT,
    formula       TEXT,
    bucket_rank   INTEGER,
    bucket_n      INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE(company_id, period_end, period_type, consolidated, metric)
);
CREATE INDEX IF NOT EXISTS idx_derived_company_metric ON derived(company_id, metric);
CREATE INDEX IF NOT EXISTS idx_derived_period ON derived(period_end);

CREATE TABLE IF NOT EXISTS scorecard (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    factor        TEXT NOT NULL,
    score         INTEGER,
    evidence      TEXT,
    inputs        TEXT,
    as_of         TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(company_id, factor)
);

CREATE TABLE IF NOT EXISTS market_data (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    fetched_at    TEXT NOT NULL,
    price         REAL,
    pct_change    REAL,
    market_cap_cr REAL,
    pe_ratio      REAL,
    pb_ratio      REAL,
    dividend_yield REAL,
    week52_high   REAL,
    week52_low    REAL,
    book_value    REAL,
    eps           REAL,
    face_value    REAL,
    raw_json      TEXT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_data_company ON market_data(company_id, fetched_at);
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
