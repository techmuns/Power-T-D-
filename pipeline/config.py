from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "power_td.db"
SEEDS = ROOT / "seeds" / "companies.yaml"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

for d in (DATA_DIR, RAW_DIR):
    d.mkdir(parents=True, exist_ok=True)
