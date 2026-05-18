# Power T&D data pipeline

A daily, automated data pipeline that pulls real public filings and
financials for ~30 listed Indian Power Transmission & Distribution
companies, and stores them in a single SQLite database that the
dashboard (built later) will read from.

## In one paragraph (no jargon)

You hired this pipeline to read every official filing the listed Power
T&D companies put out — quarterly results, investor presentations,
earnings-call transcripts, RHPs, regulator reports — and keep them in
one tidy file (`data/power_td.db`). It does this automatically every
morning using GitHub Actions, which is a free robot inside GitHub that
runs our code on a schedule. The robot has open internet access, so it
can talk to the BSE, SEBI and CEA websites that this sandbox can't.

## What it actually fetches

| Source | What we get | Why it matters |
|---|---|---|
| **BSE corporate announcements** | Every filing each company posts: results, IPs, transcripts, press releases, AGM/EGM notices, board-meeting outcomes | This is where order-book, voltage-mix, capacity-expansion disclosures live |
| **BSE financial results** | Quarterly revenue / EBITDA / PAT, standalone + consolidated | Backbone for ROCE, CFO/EBITDA, working-capital checks |
| **SEBI public-issues page** | RHP / DRHP PDFs for new and recent listings | RHPs for Atlanta, Transrail, Quality Power, OPTL have voltage-class and capacity disclosures unavailable anywhere else |
| **CEA executive summary + transmission reports** | Monthly ckm / MVA additions, HVDC pipeline, DISCOM AT&C losses | Maps to the framework doc's sections on sector capex, asset owners, DISCOMs |

## The 28 companies, bucketed per the framework doc

| Bucket | Companies |
|---|---|
| Transmission asset owners | PGCIL, Adani Energy Solutions, IndiGrid |
| Transformer manufacturers | Hitachi Energy, GE Vernova T&D, CG Power, TARIL, Voltamp, Atlanta |
| HVDC / FACTS / grid-stability OEMs | Siemens Energy, BHEL, Quality Power |
| Transmission EPC | KEC, KPIL (Kalpataru), Transrail, Bajel, Techno Electric |
| Towers / conductors / cables | Skipper, Apar, Polycab, KEI, Diamond Power |
| Distribution / DISCOM | Tata Power, CESC, Torrent Power |
| Smart meters / automation | HPL, Genus, Schneider Infra |

Full mapping with ticker codes lives in [`seeds/companies.yaml`](seeds/companies.yaml).

## How the robot works (the pipeline in plain English)

1. Every morning at 8 am IST, GitHub Actions wakes up.
2. It runs `python -m pipeline.cli fetch-all`.
3. That command, in order:
   - Reads the 28-company list from `seeds/companies.yaml`.
   - Calls the BSE corporate-announcements API for each company and
     stores every announcement of the last 365 days.
   - Calls the BSE financial-results API for each company and stores
     standalone + consolidated quarterly numbers.
   - Scrapes SEBI's public-issues listing for any new RHP / DRHP PDFs.
   - Scrapes CEA's executive-summary + transmission-reports pages for
     the latest PDFs.
   - Every row is tagged with `source`, `source_url`, `fetched_at` —
     so when the dashboard shows a number you can click through to the
     exact filing it came from. (This is the framework doc's section 3
     source-tagging discipline, enforced at the schema level.)
4. The robot commits the updated `data/` folder back to this repo.
   The next day, it diffs against what's there and only commits what
   changed.

If anything fails — a network blip, a website redesign — the run is
logged in the `fetch_runs` table with status `error` plus the message,
so we can see exactly what broke without reading workflow logs.

## How to run it yourself

You don't normally need to. But if you want to:

**From GitHub UI:** Actions tab → "Fetch Power T&D data" → "Run workflow".

**On your laptop:**

```bash
pip install -r requirements.txt
python -m pipeline.cli init          # creates the DB, seeds companies
python -m pipeline.cli fetch-all     # fetches everything
python -m pipeline.cli status        # shows row counts per table
```

## What it doesn't do yet (intentionally)

- No PDF parsing. The pipeline stores the PDF URL for every IP /
  transcript / RHP — extracting voltage-mix percentages and approval
  lists from them is the next stage (LLM extractor).
- No NSE scraper. BSE coverage is identical for these names; NSE's
  session-cookie handshake is brittle. We'll add it only if BSE goes
  down.
- No broker reports. Those are paywalled; the plan is a watched folder
  the analyst drops PDFs into.
- No UI. Per your instruction: prove the data layer first.

## Files

```
seeds/companies.yaml          the 28-company universe
pipeline/
  config.py                   paths + headers
  db.py                       SQLite schema
  seed.py                     loads companies into DB
  sources/
    bse.py                    BSE corporate announcements
    bse_financials.py         BSE quarterly results
    sebi.py                   SEBI RHP / DRHP listing
    cea.py                    CEA reports
  cli.py                      command-line entry point
.github/workflows/
  fetch-data.yml              the GitHub Actions robot
data/
  power_td.db                 the SQLite database (committed)
  raw/                        per-source raw JSON snapshots
```
