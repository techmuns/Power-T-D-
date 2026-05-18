"""Load the company universe from seeds/companies.yaml into the DB."""

import yaml
from .config import SEEDS
from .db import connect, init


def load_companies():
    init()
    with SEEDS.open() as f:
        data = yaml.safe_load(f)
    rows = data["companies"]
    with connect() as conn:
        for c in rows:
            conn.execute(
                """
                INSERT INTO companies(name, short, bse, nse, bucket, secondary)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(bse) DO UPDATE SET
                    name=excluded.name,
                    short=excluded.short,
                    nse=excluded.nse,
                    bucket=excluded.bucket,
                    secondary=excluded.secondary
                """,
                (
                    c["name"], c["short"], c.get("bse"), c.get("nse"),
                    c["bucket"], c.get("secondary"),
                ),
            )
    return len(rows)


if __name__ == "__main__":
    n = load_companies()
    print(f"seeded {n} companies")
