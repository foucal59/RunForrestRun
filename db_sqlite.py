"""
SQLite adapter for local development.

Patches database_pg to use sqlite3 instead of pg8000 when SQLITE_PATH is set.
All SQL is adapted automatically:
  %s          →  ?
  NOW()       →  datetime('now')
  NOW()-INTERVAL 'N unit'  →  datetime('now', '-N unit')

Usage:
  SQLITE_PATH=.runtime/strava.db python server.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

SQLITE_PATH = os.environ.get("SQLITE_PATH", "strava.db")
_SCHEMA_FILE = Path(__file__).parent / "schema_sqlite.sql"

_local = threading.local()

# ── SQL adapter ──

_INTERVAL_RE = re.compile(
    r"NOW\(\)\s*-\s*INTERVAL\s*'(\d+)\s+(\w+)'", re.IGNORECASE
)
_NOW_RE = re.compile(r"\bNOW\(\)", re.IGNORECASE)
_PG_CAST_RE = re.compile(r"CAST\(\?\s+AS\s+(?:JSONB|TIMESTAMPTZ)\)", re.IGNORECASE)


def _adapt_sql(sql: str) -> str:
    sql = sql.replace("%s", "?")
    sql = _PG_CAST_RE.sub("?", sql)
    sql = _INTERVAL_RE.sub(lambda m: f"datetime('now', '-{m.group(1)} {m.group(2).lower()}')", sql)
    sql = _NOW_RE.sub("datetime('now')", sql)
    return sql


class _Cursor:
    def __init__(self, raw: sqlite3.Cursor):
        self._c = raw

    @property
    def description(self):
        return self._c.description

    @property
    def rowcount(self):
        return self._c.rowcount

    def execute(self, sql: str, params=()):
        self._c.execute(_adapt_sql(sql), list(params) if params else [])

    def executemany(self, sql: str, seq):
        self._c.executemany(_adapt_sql(sql), [list(r) for r in seq])

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def close(self):
        self._c.close()


class _Conn:
    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor())

    def commit(self):
        self._raw.commit()

    def rollback(self):
        try:
            self._raw.rollback()
        except Exception:
            pass

    def close(self):
        self._raw.close()


def _new_raw_conn() -> _Conn:
    raw = sqlite3.connect(SQLITE_PATH, check_same_thread=False, timeout=30)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    return _Conn(raw)


def _get_conn() -> _Conn:
    if not getattr(_local, "conn", None):
        _local.conn = _new_raw_conn()
    return _local.conn


def _safe_conn() -> _Conn:
    try:
        conn = _get_conn()
        c = conn.cursor()
        c.execute("SELECT 1")
        c.fetchone()
        return conn
    except Exception as e:
        print(f"[DB-SQLite] reconnecting: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if getattr(_local, "conn", None):
                _local.conn.close()
        except Exception:
            pass
        _local.conn = _new_raw_conn()
        return _local.conn


# ── Patch database_pg to use our SQLite connections ──
import database_pg as _pg

_pg._local = _local
_pg._get_conn = _get_conn
_pg._safe_conn = _safe_conn
# SQLite always has a primary key (defined in schema) — stub out the PK repair.
_pg._ensure_activities_pk = lambda conn: None
# SQLite uses a composite key for splits and does not need the Postgres sequence
# repair performed before inserting detail rows.
_pg._ensure_activity_splits_id_default = lambda cur: None
# No secondary-DB replication in SQLite mode: blank both candidate URLs so
# _pg._secondary_url() resolves to "" (database_pg reads these live).
_pg.NEON_DATABASE_URL = ""
_pg.LOCAL_DATABASE_URL = ""


# Re-export everything from database_pg (our patches are already applied).
from database_pg import *  # noqa: F401, F403, E402

# These must be defined AFTER the wildcard import to override the database_pg versions.

_SQLITE_CORE_COLUMNS = {
    "activities": {
        "sync_complete_at": "TEXT",
        "sync_status": "TEXT DEFAULT 'partial'",
        "source": "TEXT DEFAULT 'garmin'",
        "garmin_activity_id": "INTEGER",
    },
    "sync_meta": {"updated_at": "TEXT"},
}


def _sqlite_type(postgres_type: str) -> str:
    normalized = postgres_type.upper()
    if normalized in ("INTEGER", "BIGINT"):
        return "INTEGER"
    if normalized in ("DOUBLE PRECISION", "REAL"):
        return "REAL"
    return "TEXT"


def _ensure_sqlite_columns(raw_conn: sqlite3.Connection) -> dict[str, list[str]]:
    definitions = {
        table: {name: _sqlite_type(ddl) for name, ddl in columns.items()}
        for table, columns in _pg.RUN_METRIC_COLUMN_DEFINITIONS.items()
    }
    for table, columns in _SQLITE_CORE_COLUMNS.items():
        definitions.setdefault(table, {}).update(columns)

    added: dict[str, list[str]] = {}
    for table, columns in definitions.items():
        existing = {
            str(row[1]) for row in raw_conn.execute(f'PRAGMA table_info("{table}")')
        }
        if not existing:
            continue
        for name, ddl in columns.items():
            if name in existing:
                continue
            raw_conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}')
            added.setdefault(table, []).append(name)
    if "updated_at" in {
        str(row[1]) for row in raw_conn.execute('PRAGMA table_info("sync_meta")')
    }:
        raw_conn.execute(
            "UPDATE sync_meta SET updated_at = COALESCE(updated_at, datetime('now'))"
        )
    raw_conn.commit()
    return added

def init_db():
    """Initialize SQLite DB from schema_sqlite.sql, then verify connectivity."""
    conn = _safe_conn()
    schema = _SCHEMA_FILE.read_text()
    raw_conn = conn._raw
    raw_conn.executescript(schema)
    added = _ensure_sqlite_columns(raw_conn)
    count = conn.cursor()
    count.execute("SELECT COUNT(*) FROM activities WHERE type='Run'")
    n = count.fetchone()[0]
    print(
        f"[DB] SQLite ready: {SQLITE_PATH} ({n} activities; added={added})",
        file=sys.stderr,
    )


def init_db_migrations():
    """Bring an existing SQLite development database up to schema parity."""
    _ensure_sqlite_columns(_safe_conn()._raw)

# get_db_readiness is inherited as-is: it only calls get_activity_count(),
# which goes through the patched _safe_conn above.
