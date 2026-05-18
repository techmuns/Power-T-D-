Drop regulator PDFs that the daily Actions run cannot fetch here.
Naming convention: `<YYYY-MM>_<kind>.pdf`.
Examples: `2026-04_exec_summary.pdf`, `2026-04_transmission.pdf`.
Files are absorbed by the next pipeline run, parsed, and stored
in the `cea_reports` and `documents` tables. The PDF itself is
kept in this folder for audit.
