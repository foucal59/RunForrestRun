#!/usr/bin/env python3
"""Mirror the Neon PostgreSQL database to a local PostgreSQL instance.

Flow on every app launch (see scripts/start.sh):
    1. `/api/data/freshness-check` fills Neon with the delta from Strava.
    2. This script copies every table from Neon -> local Postgres.
    3. The backend then flips its `DATABASE_URL` to the local instance so
       the app never touches Neon again during that session.

Env:
    DATABASE_URL_NEON   source (Neon, postgres://...)
    LOCAL_DATABASE_URL  target (local, postgres://localhost/strava)
    MIRROR_TABLES       optional CSV whitelist (default = all non-system)

Safety:
    A populated local database is never fully overwritten unless --full is
    passed explicitly. Normal self-hosted operation writes to local first and
    replicates new writes to Neon, so a full Neon -> local copy is only needed
    for the initial bootstrap.

Strategy: introspect Neon's information_schema, recreate schema + data on
local via TRUNCATE + bulk INSERT in dependency order. Idempotent: running
twice in a row is a no-op relative to data, and any schema drift is healed
by re-reading column lists from the source on each run.
"""
from __future__ import annotations

import json
import os
import sys
import ssl
import time
from urllib.parse import urlparse, parse_qs
import pg8000.dbapi


def _parse_db_url(url: str) -> dict:
    parsed = urlparse(url)
    params: dict = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }
    qs = parse_qs(parsed.query)
    if qs.get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    return params


def _to_direct_url(url: str) -> str:
    """Convert Neon pooler URL to direct endpoint.

    Neon's PgBouncer pooler drops connections during long queries (full table
    dumps). The direct endpoint has no timeout and supports server-side cursors.
    ep-name-pooler.region.aws.neon.tech -> ep-name.region.aws.neon.tech
    """
    parsed = urlparse(url)
    if parsed.hostname and "-pooler." in parsed.hostname:
        direct_host = parsed.hostname.replace("-pooler.", ".", 1)
        url = url.replace(parsed.hostname, direct_host, 1)
    # channel_binding is not supported by pg8000 — strip it
    url = url.replace("&channel_binding=require", "").replace("?channel_binding=require&", "?").replace("?channel_binding=require", "")
    return url


def _connect(url: str, label: str):
    print(f"[mirror] connecting to {label} ({urlparse(url).hostname})", file=sys.stderr)
    return pg8000.dbapi.connect(**_parse_db_url(url))


def _tables(cur) -> list:
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [r[0] for r in cur.fetchall()]


def _columns(cur, table: str) -> list:
    cur.execute("""
        SELECT column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, [table])
    return cur.fetchall()


def _constraints(cur, table: str) -> list:
    """PRIMARY KEY / UNIQUE constraints on `table`, as [(type, [cols...])]."""
    cur.execute("""
        SELECT tc.constraint_type, tc.constraint_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public' AND tc.table_name = %s
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        ORDER BY tc.constraint_name, kcu.ordinal_position
    """, [table])
    by_name: dict[str, list] = {}
    for ctype, cname, col in cur.fetchall():
        by_name.setdefault(cname, [ctype, []])[1].append(col)
    return [(ctype, cols) for ctype, cols in by_name.values()]


def _ensure_constraints(dst, dst_cur, table: str, constraints: list) -> None:
    """Recreate PK/UNIQUE on the local table, idempotently and best-effort."""
    for ctype, cols in constraints:
        cols_sql = ", ".join(f'"{c}"' for c in cols)
        try:
            if ctype == "PRIMARY KEY":
                dst_cur.execute("""
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_schema = 'public' AND table_name = %s
                      AND constraint_type = 'PRIMARY KEY'
                """, [table])
                if dst_cur.fetchone():
                    continue
                dst_cur.execute(f'ALTER TABLE public."{table}" ADD PRIMARY KEY ({cols_sql})')
            else:
                cname = f'{table}_{"_".join(cols)}_uq'
                dst_cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", [cname])
                if dst_cur.fetchone():
                    continue
                dst_cur.execute(
                    f'ALTER TABLE public."{table}" ADD CONSTRAINT "{cname}" UNIQUE ({cols_sql})')
            dst.commit()
            print(f"[mirror] {table}: +{ctype} ({', '.join(cols)})", file=sys.stderr)
        except Exception as e:
            dst.rollback()
            print(f"[mirror] {table}: {ctype} ({', '.join(cols)}) skipped — {e}", file=sys.stderr)


def _create_table_stmt(table: str, cols: list) -> str:
    parts = []
    for name, data_type, udt, nullable, default in cols:
        # Map pg8000/information_schema types back to SQL.
        if data_type == "ARRAY":
            col_type = f"{udt.lstrip('_')}[]"
        elif data_type == "USER-DEFINED":
            col_type = udt
        elif data_type == "jsonb":
            col_type = "jsonb"
        elif data_type == "timestamp without time zone":
            col_type = "timestamp"
        elif data_type == "timestamp with time zone":
            col_type = "timestamptz"
        elif data_type == "double precision":
            col_type = "double precision"
        elif data_type == "character varying":
            col_type = "text"
        elif data_type == "character":
            col_type = "text"
        else:
            col_type = data_type
        null_kw = "" if nullable == "YES" else " NOT NULL"
        # Skip nextval() defaults — sequences don't exist locally and we
        # insert explicit values via TRUNCATE+INSERT anyway.
        default_kw = f" DEFAULT {default}" if default and "nextval" not in str(default) else ""
        parts.append(f'"{name}" {col_type}{null_kw}{default_kw}')
    return f'CREATE TABLE IF NOT EXISTS public."{table}" (\n  ' + ",\n  ".join(parts) + "\n)"


def _coerce_row(row: tuple, col_rows: list) -> tuple:
    """Re-encode jsonb/json Python objects to JSON strings for the target driver.

    pg8000 deserializes jsonb columns into Python dicts/lists. When passed
    back to pg8000 for INSERT, newer versions serialize them as Python repr
    (e.g. {…}) instead of valid JSON — causing 'invalid input syntax for
    type json'. Explicit json.dumps() fixes this.
    """
    result = []
    for val, (_, data_type, udt, *_rest) in zip(row, col_rows):
        if val is not None and isinstance(val, (dict, list)) and data_type in ("jsonb", "json", "USER-DEFINED"):
            val = json.dumps(val, ensure_ascii=False)
        result.append(val)
    return tuple(result)


def _copy_table(src_cur, dst_cur, table: str) -> int:
    col_rows = _columns(src_cur, table)
    if not col_rows:
        return 0
    col_names = [c[0] for c in col_rows]
    dst_cur.execute(_create_table_stmt(table, col_rows))
    dst_cur.execute(f'TRUNCATE public."{table}" RESTART IDENTITY CASCADE')

    cols_sql = ", ".join(f'"{c}"' for c in col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f'INSERT INTO public."{table}" ({cols_sql}) VALUES ({placeholders})'

    src_cur.execute(f'SELECT {cols_sql} FROM public."{table}"')
    batch = []
    total = 0
    for row in src_cur:
        batch.append(_coerce_row(row, col_rows))
        if len(batch) >= 500:
            dst_cur.executemany(insert_sql, batch)
            total += len(batch)
            batch = []
    if batch:
        dst_cur.executemany(insert_sql, batch)
        total += len(batch)
    return total


# Copy order matters. Neon's PgBouncer / serverless gateway frequently drops
# the source socket right after the huge activity_streams dump; with a single
# shared connection that killed every table that came after it (alphabetically
# athletes, athlete_zones, bikes, shoes, sync_meta…), leaving local a partial
# copy. We now (a) copy the small critical tables first, (b) copy
# activity_streams last, and (c) open a *fresh* source connection per table so
# one dropped socket only costs that table.
PRIORITY_TABLES = [
    "sync_meta", "athletes", "athlete_zones", "athlete_stats", "import_state",
    "shoes", "bikes", "vo2max_history",
    "activities", "activity_best_efforts", "activity_laps", "activity_splits",
    "local_legends", "segment_efforts", "segments",
]
LAST_TABLES = ["activity_streams"]


def _ordered_tables(all_tables: list) -> list:
    """Critical small tables first, activity_streams last, everything else in
    between (preserving discovery order)."""
    present = set(all_tables)
    ordered: list = []
    seen: set = set()
    for t in PRIORITY_TABLES:
        if t in present and t not in seen:
            ordered.append(t)
            seen.add(t)
    for t in all_tables:
        if t not in seen and t not in LAST_TABLES:
            ordered.append(t)
            seen.add(t)
    for t in LAST_TABLES:
        if t in present and t not in seen:
            ordered.append(t)
            seen.add(t)
    return ordered


def _dest_activity_count(dst_cur) -> int | None:
    """Row count of activities on the destination, or None if the table is absent."""
    try:
        dst_cur.execute("SELECT to_regclass('public.activities')")
        if dst_cur.fetchone()[0] is None:
            return None
        dst_cur.execute("SELECT COUNT(*) FROM public.activities")
        return dst_cur.fetchone()[0]
    except Exception:
        return None


def main():
    only_if_empty = ("--if-empty" in sys.argv[1:]
                     or os.environ.get("MIRROR_ONLY_IF_EMPTY") == "1")
    allow_full_overwrite = "--full" in sys.argv[1:]
    src_url = os.environ.get("DATABASE_URL_NEON") or os.environ.get("DATABASE_URL")
    dst_url = os.environ.get("LOCAL_DATABASE_URL")
    if not src_url:
        print("[mirror] DATABASE_URL_NEON (or DATABASE_URL) is required", file=sys.stderr)
        sys.exit(1)
    if not dst_url:
        print("[mirror] LOCAL_DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)
    # Use direct Neon endpoint (not pooler) — pooler cuts long-running queries
    src_url = _to_direct_url(src_url)
    if src_url == dst_url:
        print("[mirror] refusing to mirror a URL onto itself", file=sys.stderr)
        sys.exit(1)

    whitelist = None
    raw = os.environ.get("MIRROR_TABLES", "").strip()
    if raw:
        whitelist = {t.strip() for t in raw.split(",") if t.strip()}

    start = time.time()
    tables: list = []
    grand_total = 0
    # The destination (local) connection is stable, so keep it for the whole run.
    with _connect(dst_url, "local (target)") as dst:
        dst_cur = dst.cursor()

        if only_if_empty:
            n = _dest_activity_count(dst_cur)
            if n:
                print(f"[mirror] local already populated ({n} activities) — "
                      "skipping bootstrap", file=sys.stderr)
                return
            print("[mirror] local is empty — bootstrapping from Neon", file=sys.stderr)
        elif not whitelist and not allow_full_overwrite:
            n = _dest_activity_count(dst_cur)
            if n:
                print(
                    f"[mirror] local already populated ({n} activities) — refusing "
                    "a full overwrite. Local is the self-hosted primary; use "
                    "MIRROR_TABLES for an explicit targeted copy or --full to "
                    "force a complete rebuild.",
                    file=sys.stderr,
                )
                return

        # Discover the table list with a short-lived source connection.
        with _connect(src_url, "neon (discovery)") as probe:
            tables = _tables(probe.cursor())
        if whitelist:
            tables = [t for t in tables if t in whitelist]
        tables = _ordered_tables(tables)
        print(f"[mirror] found {len(tables)} tables in neon", file=sys.stderr)

        errors = []
        for t in tables:
            try:
                # Fresh source connection per table: a dropped Neon socket after
                # a large table no longer aborts the tables that follow.
                with _connect(src_url, f"neon:{t}") as src:
                    src_cur = src.cursor()
                    # Disable FK triggers so we can TRUNCATE+INSERT out of order.
                    dst_cur.execute("SET session_replication_role = 'replica'")
                    n = _copy_table(src_cur, dst_cur, t)
                    dst.commit()
                    cons = _constraints(src_cur, t)
                # Restore PK/UNIQUE for this table now, before moving on, so the
                # app's ON CONFLICT upserts never hit a 42P10 missing-constraint
                # error even if a later table aborts the run.
                if cons:
                    _ensure_constraints(dst, dst_cur, t, cons)
                grand_total += n
                print(f"[mirror] {t}: {n} rows", file=sys.stderr)
            except Exception as e:
                print(f"[mirror] {t}: SKIPPED — {e}", file=sys.stderr)
                errors.append(t)
                try:
                    dst.rollback()
                except Exception:
                    pass

        try:
            dst_cur.execute("SET session_replication_role = 'origin'")
            dst.commit()
        except Exception:
            pass

        if errors:
            print(f"[mirror] WARNING: {len(errors)} table(s) skipped: {', '.join(errors)}", file=sys.stderr)

    elapsed = time.time() - start
    print(f"[mirror] done: {grand_total} rows across {len(tables)} tables in {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
