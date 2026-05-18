# Power T&D data pipeline

A daily, automated data pipeline that pulls real public filings and
financials for ~30 listed Indian Power Transmission & Distribution
companies, reads the PDFs the companies post, extracts the
framework-relevant facts, and stores everything in a single SQLite
database (`data/power_td.db`) that the dashboard reads from.

## In one paragraph (no jargon)

This is your data plumbing. Every morning a small robot (GitHub
Actions) wakes up and does eight things:

1. Reads the master list of 28 Power T&D companies.
2. Asks BSE for every filing each company has posted in the last year.
3. Asks Screener for each company's quarterly numbers, balance sheet,
   cash flow, and ratios (ROCE, debtor days, etc.).
4. Asks SEBI for any new RHP / DRHP PDFs for new listings.
5. Tries to fetch CEA's monthly transmission reports (CEA blocks cloud
   IPs, so this is currently best-effort; a manual upload path is
   provided in `data/manual/cea/`).
6. Absorbs any regulator PDFs an analyst has dropped into
   `data/manual/`.
7. Downloads every investor presentation, transcript, results PDF and
   relevant press release it has just discovered, and extracts the text.
8. Runs two extractors over that text:
   - a cheap regex pass (voltage classes mentioned, MVA / capacity
     numbers, order book size, AT&C losses, capex amounts)
   - an LLM pass (Claude) per company bucket, which returns structured
     JSON of framework-specific fields - voltage mix, executed MVA,
     approvals, order book by customer, HVDC scope, etc.

It then commits everything it found back to this repo. The next morning
it diffs and only commits what changed.

## What's in the database

Open `data/power_td.db` in any SQLite viewer. Tables:

| Table | What it holds | Powers which framework section |
|---|---|---|
| `companies` | 28 names + bucket mapping + BSE/NSE codes | §1, §5 |
| `announcements` | Every BSE filing's headline + PDF link | §5, §10 |
| `financials` | Quarterly revenue / op profit / PAT | §12, §22 |
| `balance_sheet` | Annual: equity, borrowings, fixed assets, CWIP | §12, §13 |
| `cash_flow` | Annual: CFO, CFI, CFF, net cash flow | §12 |
| `ratios` | Annual: debtor days, inventory days, WC days, ROCE, ROE | §8, §12 |
| `sebi_filings` | New-listing RHP / DRHP PDFs | §15 |
| `cea_reports` | CEA monthly reports (manual or scraped) | §13, §14 |
| `documents` | Downloaded PDFs + extracted full text | feeds the extractors |
| `features` | The structured facts pulled out of PDFs (voltage mix, MVA, order book, approvals, AT&C losses, capex...) with verbatim evidence | §1-§26 |
| `fetch_runs` | Log of every pipeline run, per source | observability |

Every row carries `source`, `source_url`, `fetched_at` - so when the
dashboard shows a number you can click straight to the filing it came
from. Every `features` row also carries the verbatim quote and which
extractor produced it (`heuristic` vs `llm`) - matching the framework
doc's section 3 source-tagging discipline.

## The 28 companies

| Bucket | Companies |
|---|---|
| Transmission asset owners | PGCIL, Adani Energy Solutions, IndiGrid |
| Transformer manufacturers | Hitachi Energy, GE Vernova T&D, CG Power, TARIL, Voltamp, Atlanta |
| HVDC / FACTS / grid-stability OEMs | Siemens Energy, BHEL, Quality Power |
| Transmission EPC | KEC, KPIL (Kalpataru), Transrail, Bajel, Techno Electric |
| Towers / conductors / cables | Skipper, Apar, Polycab, KEI, Diamond Power |
| Distribution / DISCOM | Tata Power, CESC, Torrent Power |
| Smart meters / automation | HPL, Genus, Schneider Infra |

## To enable LLM extraction

The LLM pass only runs if `ANTHROPIC_API_KEY` is configured as a
repository secret. Once-only setup:

1. Get a key at https://console.anthropic.com.
2. GitHub → this repo → Settings → Secrets and variables → Actions →
   New repository secret.
   Name: `ANTHROPIC_API_KEY`, value: your key.
3. Next pipeline run will start using it. Default rate-limit: 4 docs
   per company per run, so a full back-catalogue pass takes a few days
   and stays well within reasonable API spend.

If the key is absent the LLM stage logs "skipping" and the rest of the
pipeline runs unaffected.

## Manual uploads (workaround for blocked sites)

`cea.nic.in` blocks Microsoft Azure IP ranges that GitHub Actions runs
on. Until we route via a residential proxy, the workaround:

1. Download a CEA Executive Summary or Transmission Report PDF.
2. Drop it into `data/manual/cea/` with filename like
   `2026-04_exec_summary.pdf`.
3. Push to main. The next pipeline run absorbs it, parses the text,
   stores it in `cea_reports` and `documents`.

## To run it yourself

You don't normally need to. But if you want to:

**From GitHub UI:** Actions tab → "Fetch Power T&D data" → "Run workflow".

**On your laptop:**

```bash
pip install -r requirements.txt
python -m pipeline.cli init           # creates the DB, seeds companies
python -m pipeline.cli fetch-all      # full pipeline
python -m pipeline.cli status         # row counts per table
```

Sub-commands if you want to run one stage at a time:
`fetch-bse`, `fetch-financials`, `fetch-sebi`, `fetch-cea`,
`ingest-manual`, `pdf`, `heuristics`, `llm`.

## Files

```
seeds/companies.yaml          the 28-company universe
pipeline/
  config.py                   paths + headers
  db.py                       SQLite schema (11 tables)
  seed.py                     loads companies into DB
  cli.py                      command-line entry point
  sources/
    bse.py                    BSE corporate announcements
    screener.py               Screener financials (quarters/BS/CF/ratios)
    sebi.py                   SEBI RHP / DRHP listing
    cea.py                    CEA reports (best-effort, often blocked)
    manual.py                 ingestor for manually-uploaded PDFs
  extract/
    pdf_text.py               download + text-extract high-value PDFs
    heuristics.py             regex extractor (voltage, MVA, order book, ...)
    llm.py                    Claude-based per-bucket extractor
.github/workflows/
  fetch-data.yml              the GitHub Actions robot
data/
  power_td.db                 the SQLite database (committed)
  raw/                        per-source raw JSON / HTML snapshots
  manual/                     drop-zone for analyst-uploaded PDFs
```
