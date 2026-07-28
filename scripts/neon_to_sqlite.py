#!/usr/bin/env python3
"""
Migrate data from Neon PostgreSQL to a local SQLite file.

Usage:
    DATABASE_URL=postgresql://... python scripts/neon_to_sqlite.py [output.db]

Tables copied: athletes, activities, activity_best_efforts, activity_splits,
               activity_streams, activity_laps, athlete_zones, shoes, bikes,
               sync_meta, vo2max_history, athlete_stats
"""
from __future__ import annotations
import json
import os
import sys
import sqlite3
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _pg_conn():
    import pg8000.dbapi
    import ssl
    from urllib.parse import urlparse, parse_qs
    url = os.environ["DATABASE_URL"]
    p = urlparse(url)
    params = {
        "host": p.hostname, "port": p.port or 5432,
        "database": p.path.lstrip("/"),
        "user": p.username, "password": p.password,
        "timeout": 15,
    }
    qs = parse_qs(p.query)
    if qs.get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    return pg8000.dbapi.connect(**params)


TABLES = [
    "athletes", "activities", "activity_best_efforts", "activity_splits",
    "activity_streams", "activity_laps", "athlete_zones", "shoes", "bikes",
    "sync_meta", "vo2max_history", "athlete_stats",
]


def copy_table(pg_cur, sl: sqlite3.Connection, table: str):
    try:
        pg_cur.execute(f"SELECT * FROM {table}")
        rows = pg_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            return
        cols = [d[0] for d in pg_cur.description]
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        # Convert rows: booleans → int, others → native Python types
        def _coerce(v):
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False)
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return v
        coerced = [tuple(_coerce(c) for c in row) for row in rows]
        sl.executemany(
            f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
            coerced,
        )
        sl.commit()
        print(f"  {table}: {len(rows)} rows")
    except Exception as e:
        print(f"  {table}: SKIP ({type(e).__name__}: {e})")
        sl.rollback()


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else ".runtime/strava.db"
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to Neon…")
    pg = _pg_conn()
    pg_cur = pg.cursor()

    print(f"Opening SQLite: {db_path}")
    sl = sqlite3.connect(db_path, timeout=30)
    sl.execute("PRAGMA journal_mode=WAL")
    sl.execute("PRAGMA foreign_keys=OFF")

    schema = (ROOT / "schema_sqlite.sql").read_text()
    sl.executescript(schema)
    sl.commit()

    print("Copying tables…")
    for table in TABLES:
        copy_table(pg_cur, sl, table)

    sl.close()
    pg.close()
    print(f"\nDone → {db_path}")


if __name__ == "__main__":
    main()
