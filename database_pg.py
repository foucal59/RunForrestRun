"""
PostgreSQL database adapter for the new normalized Neon schema.
The DB is the single source of truth — Garmin Connect only fills gaps.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import json
import os
import re
import ssl
import sys
import time
import threading
from urllib.parse import urlparse, parse_qs

import pg8000.dbapi

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Neon URL. In self-hosted mode the primary is the local Postgres and Neon is
# the secondary replica; this also backs get_sync_meta_from_neon()'s Garmin
# token-recovery path. db_sqlite blanks it at runtime to disable replication.
NEON_DATABASE_URL = os.environ.get("DATABASE_URL_NEON", "")
# Local Postgres URL. On Vercel (primary = Neon) this is the *optional*
# secondary replica target, used only when it is actually reachable from the
# runtime (localhost on Vercel is the container, not the Mac).
LOCAL_DATABASE_URL = os.environ.get("LOCAL_DATABASE_URL", "")

_local = threading.local()
_secondary_local = threading.local()

# Best-effort replication circuit breaker. After the secondary DB refuses a
# write we skip it for a cooldown window so an unreachable replica (e.g. a
# Vercel runtime that cannot reach the Mac's Postgres) never adds its
# connect-timeout budget to every subsequent request.
_SECONDARY_COOLDOWN = float(os.environ.get("DB_SECONDARY_COOLDOWN", "120"))
_secondary_breaker = {"open_until": 0.0}


def _secondary_url() -> str:
    """Resolve the secondary (replica) DB URL from the live module globals.

    Asymmetric by design, and read fresh on each call so db_sqlite and the
    helper scripts can toggle replication at runtime:
      - self-hosted (DATABASE_URL = local): secondary = Neon (DATABASE_URL_NEON)
      - Vercel      (DATABASE_URL = Neon):  secondary = local (LOCAL_DATABASE_URL)
    The secondary must differ from the primary, else there is nothing to do.
    """
    if NEON_DATABASE_URL and NEON_DATABASE_URL != DATABASE_URL:
        return NEON_DATABASE_URL
    if LOCAL_DATABASE_URL and LOCAL_DATABASE_URL != DATABASE_URL:
        return LOCAL_DATABASE_URL
    return ""


def _is_local_primary() -> bool:
    return bool(LOCAL_DATABASE_URL and DATABASE_URL == LOCAL_DATABASE_URL)


def _primary_complete_status() -> str:
    """Global sync status to publish after the active primary finishes a run."""
    if _is_local_primary():
        return "ok_local"
    return "ok_neon"


def _parse_db_url(url: str) -> dict:
    parsed = urlparse(url)
    params: dict = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
        # Bound the socket connect so a Neon cold start fails fast instead of
        # hanging until the serverless gateway returns 504. _safe_conn() then
        # reconnects with backoff — the first probe wakes the compute, a later
        # one lands once Neon is up.
        # 5s default: 5 attempts × 5s + (2+4+6+8)s sleep ≈ 45s < Vercel's 60s limit.
        "timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
    }
    qs = parse_qs(parsed.query)
    if qs.get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    return params


def _get_conn():
    """Get thread-local connection. Reconnects lazily on failure."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = pg8000.dbapi.connect(**_parse_db_url(DATABASE_URL))
        if _is_local_primary():
            cur = _local.conn.cursor()
            cur.execute("SET idle_in_transaction_session_timeout = '30s'")
            _local.conn.commit()
    return _local.conn


def _probe_conn(holder, connect_fn, attempts: int, sleep_for, label: str):
    """Shared probe/reconnect loop behind _safe_conn and _safe_secondary_conn.

    Two failure modes, one probe. (1) Neon idles out client connections
    silently: a half-closed TCP socket accepts rollback() bytes without raising
    (the write lands in the OS send buffer), but the next execute() fails
    "network error" mid-query. (2) Neon scales the compute to zero when idle, so
    the first query after suspend hangs (bounded by the connect timeout) or
    errors while it wakes. We probe with a cheap SELECT 1 and, on failure,
    reconnect with short backoff. Because the probe runs *before* the caller's
    statement, this wakes the compute for reads and writes alike — and never
    retries a half-applied write.
    """
    last_err = None
    for attempt in range(attempts):
        try:
            conn = connect_fn()
            conn.rollback()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return conn
        except Exception as e:
            last_err = e
            print(f"[{label}] conn probe failed ({attempt + 1}/{attempts}): {type(e).__name__}: {e}; reconnecting", file=sys.stderr)
            try:
                if getattr(holder, "conn", None) is not None:
                    holder.conn.close()
            except Exception:
                pass
            holder.conn = None
            if attempt < attempts - 1:
                time.sleep(sleep_for(attempt))
    raise last_err


def _safe_conn():
    """Get a healthy connection, reconnecting on a stale or cold-started DB."""
    # Neon cold start takes 5-15s: the 2s, 4s backoff gives it room to wake up.
    return _probe_conn(_local, _get_conn, 3, lambda a: 2 * (a + 1), "DB")


def _get_secondary_conn():
    if not hasattr(_secondary_local, "conn") or _secondary_local.conn is None:
        _secondary_local.conn = pg8000.dbapi.connect(**_parse_db_url(_secondary_url()))
    return _secondary_local.conn


def _safe_secondary_conn():
    """Healthy connection to the secondary (replica) DB. Best-effort: fewer
    retries than the primary, because the data is already committed to the
    primary by the time we replicate, and _replicate()'s circuit breaker bounds
    the cost of a dead replica."""
    return _probe_conn(_secondary_local, _get_secondary_conn, 2, lambda a: 1, "DB-REPL")


def _replicate(label: str, run) -> None:
    """Best-effort dual-write to the secondary DB. Never raises.

    `run(cursor)` performs the writes; commit/rollback is handled here. Called
    *after* the primary has committed, so the primary stays the source of truth
    and a replica failure is non-fatal. A circuit breaker skips the secondary
    for _SECONDARY_COOLDOWN seconds after a failure so a dead replica doesn't
    add latency to every following write.
    """
    if not _secondary_url():
        return
    if time.time() < _secondary_breaker["open_until"]:
        return
    try:
        conn = _safe_secondary_conn()
        cur = conn.cursor()
        run(cur)
        conn.commit()
    except Exception as e:
        _secondary_breaker["open_until"] = time.time() + _SECONDARY_COOLDOWN
        print(f"[DB-REPL] {label} -> secondary failed (non-fatal, cooling down "
              f"{_SECONDARY_COOLDOWN:.0f}s): {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if getattr(_secondary_local, "conn", None) is not None:
                _secondary_local.conn.rollback()
        except Exception:
            pass


def _executemany_values(
    cur, sql: str, rows: list, chunk: int = 500, row_template: str | None = None
):
    """Multi-row INSERT in one round-trip per chunk.

    pg8000's executemany sends one network round-trip per row — on Neon that
    dominates stream hydration (~2000 rows) and every sync. `sql` must contain
    a single `{values}` placeholder; every row must have the same width.
    """
    if not rows:
        return
    row_tpl = row_template or ("(" + ",".join(["%s"] * len(rows[0])) + ")")
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        params = [p for row in batch for p in row]
        cur.execute(sql.format(values=",".join([row_tpl] * len(batch))), params)


def _iso_notz(dt):
    """Convert datetime to ISO string without timezone suffix."""
    if not dt:
        return ""
    if hasattr(dt, "isoformat"):
        return dt.replace(tzinfo=None).isoformat()
    # SQLite renvoie des TEXT 'YYYY-MM-DD HH:MM:SS' — normaliser en ISO.
    return str(dt).replace(" ", "T")


def init_db():
    """Warm the Neon connection. No heavy queries — _safe_conn() already pings."""
    _safe_conn()
    print(f"[DB] PostgreSQL connected.")


# DDL shared between init_db_migrations() and the replication closures that
# bootstrap the same tables on the secondary DB — one definition per table so
# the two paths can never diverge.
_CREATE_SYNC_META_SQL = """CREATE TABLE IF NOT EXISTS sync_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

_CREATE_VO2MAX_SQL = """CREATE TABLE IF NOT EXISTS vo2max_history (
        date TEXT PRIMARY KEY,
        vo2max DOUBLE PRECISION,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

_CREATE_SLEEP_SQL = """CREATE TABLE IF NOT EXISTS sleep_history (
        date TEXT PRIMARY KEY,
        sleep_score INTEGER,
        sleep_quality TEXT,
        sleep_duration_seconds INTEGER,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

_CREATE_SHOES_SQL = """CREATE TABLE IF NOT EXISTS shoes (
        id TEXT PRIMARY KEY,
        athlete_id BIGINT,
        name TEXT,
        nickname TEXT,
        brand_name TEXT,
        model_name TEXT,
        distance DOUBLE PRECISION,
        primary_shoe BOOLEAN DEFAULT FALSE,
        retired BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

_CREATE_BIKES_SQL = """CREATE TABLE IF NOT EXISTS bikes (
        id TEXT PRIMARY KEY,
        athlete_id BIGINT,
        name TEXT,
        brand_name TEXT,
        model_name TEXT,
        distance DOUBLE PRECISION,
        retired BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

_CREATE_TOMBSTONES_SQL = """CREATE TABLE IF NOT EXISTS sync_tombstones (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (entity_type, entity_id)
    )"""

# Ajustements du plan decides par le coach. Le calendrier marathon vit dans le
# code (daily_training_plan._build_calendar) : cette table est le seul canal par
# lequel une decision du coach atteint le site sans redeploiement.
_CREATE_PLAN_OVERRIDES_SQL = """CREATE TABLE IF NOT EXISTS plan_overrides (
        day TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        note TEXT,
        source TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )"""

SMALL_TABLE_DDL = {
    "vo2max_history": _CREATE_VO2MAX_SQL,
    "sleep_history": _CREATE_SLEEP_SQL,
    "shoes": _CREATE_SHOES_SQL,
    "bikes": _CREATE_BIKES_SQL,
    "sync_meta": _CREATE_SYNC_META_SQL,
    "plan_overrides": _CREATE_PLAN_OVERRIDES_SQL,
}


RUN_METRIC_COLUMN_DEFINITIONS = {
    "activities": {
        "garmin_activity_uuid": "TEXT",
        "garmin_timezone_id": "INTEGER",
        "garmin_device_id": "BIGINT",
        "lap_count": "INTEGER",
        "elevation_loss": "DOUBLE PRECISION",
        "max_cadence": "DOUBLE PRECISION",
        "aerobic_training_effect": "DOUBLE PRECISION",
        "anaerobic_training_effect": "DOUBLE PRECISION",
        "activity_training_load": "DOUBLE PRECISION",
        "vo2max": "DOUBLE PRECISION",
        "training_effect_label": "TEXT",
        "avg_stride_length": "DOUBLE PRECISION",
        "avg_ground_contact_time": "DOUBLE PRECISION",
        "avg_vertical_oscillation": "DOUBLE PRECISION",
        "avg_vertical_ratio": "DOUBLE PRECISION",
        "avg_grade_adjusted_speed": "DOUBLE PRECISION",
        "body_battery_delta": "INTEGER",
        "steps": "INTEGER",
        "moderate_intensity_minutes": "INTEGER",
        "vigorous_intensity_minutes": "INTEGER",
        "min_temperature": "DOUBLE PRECISION",
        "max_temperature": "DOUBLE PRECISION",
        "avg_respiration_rate": "DOUBLE PRECISION",
        "min_respiration_rate": "DOUBLE PRECISION",
        "max_respiration_rate": "DOUBLE PRECISION",
        "water_estimated": "DOUBLE PRECISION",
        "garmin_workout_id": "BIGINT",
        "garmin_course_id": "BIGINT",
        # typeKey Garmin brut ('running', 'trail_running', 'hiking', 'cycling'…).
        # `type`/`sport_type` restent une catégorie grossière ('Run', 'Hike'…) sur
        # laquelle filtre tout le site ; cette colonne porte la fidélité Garmin.
        "garmin_type_key": "TEXT",
        "hr_time_in_zones": "JSONB",
        "power_time_in_zones": "JSONB",
        "garmin_fastest_splits": "JSONB",
        "garmin_summary": "JSONB",
        "run_metrics_updated_at": "TIMESTAMPTZ",
        "run_summary_updated_at": "TIMESTAMPTZ",
        "run_zones_updated_at": "TIMESTAMPTZ",
        "run_details_updated_at": "TIMESTAMPTZ",
        "run_laps_updated_at": "TIMESTAMPTZ",
        "run_streams_updated_at": "TIMESTAMPTZ",
        # Snapshot Garmin sante/fatigue rattache a la fin du run.
        "health_snapshot_at": "TEXT",
        "health_sleep_date": "TEXT",
        "health_sleep_score": "INTEGER",
        "health_sleep_quality": "TEXT",
        "health_sleep_duration_seconds": "INTEGER",
        "health_sleep_start_local": "TEXT",
        "health_sleep_end_local": "TEXT",
        "health_hrv_date": "TEXT",
        "health_hrv_last_night_avg_ms": "DOUBLE PRECISION",
        "health_hrv_weekly_avg_ms": "DOUBLE PRECISION",
        "health_hrv_status": "TEXT",
        "health_hrv_baseline_low_ms": "DOUBLE PRECISION",
        "health_hrv_baseline_high_ms": "DOUBLE PRECISION",
        "health_resting_hr_date": "TEXT",
        "health_resting_hr_bpm": "INTEGER",
        "health_resting_hr_7d_avg_bpm": "DOUBLE PRECISION",
        "run_health_updated_at": "TIMESTAMPTZ",
        # Météo Open-Meteo au point/heure de départ (voir scripts/weather_for_run.py
        # et scripts/backfill_weather.py). Source de vérité = base ; écrit en
        # primaire puis répliqué comme composant « weather » (run_weather_updated_at).
        "weather_temperature": "DOUBLE PRECISION",
        "weather_apparent_temperature": "DOUBLE PRECISION",
        "weather_humidity": "DOUBLE PRECISION",
        "weather_precipitation": "DOUBLE PRECISION",
        "weather_wind_speed": "DOUBLE PRECISION",
        "weather_wind_gusts": "DOUBLE PRECISION",
        "weather_code": "INTEGER",
        "weather_source": "TEXT",
        "run_weather_updated_at": "TIMESTAMPTZ",
    },
    "activity_streams": {
        "vertical_speed": "DOUBLE PRECISION",
        "body_battery": "DOUBLE PRECISION",
        "fractional_cadence": "DOUBLE PRECISION",
        "grade_adjusted_speed": "DOUBLE PRECISION",
        "ground_contact_time": "DOUBLE PRECISION",
        "performance_condition": "DOUBLE PRECISION",
        "stride_length": "DOUBLE PRECISION",
        "vertical_oscillation": "DOUBLE PRECISION",
        "vertical_ratio": "DOUBLE PRECISION",
        "accumulated_power": "DOUBLE PRECISION",
        "corrected_altitude": "DOUBLE PRECISION",
        "uncorrected_altitude": "DOUBLE PRECISION",
        "garmin_metrics": "JSONB",
    },
    "activity_best_efforts": {
        # Dénivelé net (m) sur la fenêtre du record : positif = ça monte.
        # NULL = inconnu (ligne historique, ou run sans stream d'altitude).
        "elevation_delta": "DOUBLE PRECISION",
    },
    "activity_laps": {
        "elevation_loss": "DOUBLE PRECISION",
        "elev_high": "DOUBLE PRECISION",
        "elev_low": "DOUBLE PRECISION",
        "max_vertical_speed": "DOUBLE PRECISION",
        "start_lat": "DOUBLE PRECISION",
        "start_lng": "DOUBLE PRECISION",
        "end_lat": "DOUBLE PRECISION",
        "end_lng": "DOUBLE PRECISION",
        "max_cadence": "DOUBLE PRECISION",
        "max_watts": "DOUBLE PRECISION",
        "min_watts": "DOUBLE PRECISION",
        "weighted_average_watts": "DOUBLE PRECISION",
        "total_work": "DOUBLE PRECISION",
        "grade_adjusted_speed": "DOUBLE PRECISION",
        "ground_contact_time": "DOUBLE PRECISION",
        "stride_length": "DOUBLE PRECISION",
        "vertical_oscillation": "DOUBLE PRECISION",
        "vertical_ratio": "DOUBLE PRECISION",
        "calories": "DOUBLE PRECISION",
        "bmr_calories": "DOUBLE PRECISION",
        "intensity_type": "TEXT",
        "workout_step_index": "INTEGER",
        "workout_compliance_score": "DOUBLE PRECISION",
        "garmin_data": "JSONB",
    },
}


def ensure_run_metric_schema(conn) -> dict[str, list[str]]:
    """Add Garmin run-only columns to an existing PostgreSQL schema.

    The same helper is used by normal startup migrations and by the
    Neon/local convergence script, so a replica always has the columns before
    a dual-write attempts to populate them. It intentionally does not commit;
    callers keep transaction and lock handling under their control.
    """
    added: dict[str, list[str]] = {}
    cur = conn.cursor()
    for table, definitions in RUN_METRIC_COLUMN_DEFINITIONS.items():
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
        """, [table])
        existing = {row[0] for row in cur.fetchall()}
        missing = [(name, ddl) for name, ddl in definitions.items() if name not in existing]
        if not missing:
            continue
        clauses = ", ".join(f'ADD COLUMN "{name}" {ddl}' for name, ddl in missing)
        cur.execute(f'ALTER TABLE public."{table}" {clauses}')
        added[table] = [name for name, _ in missing]
    return added


def init_db_migrations():
    """Idempotent schema migrations. Safe to run in background after server starts."""
    conn = _safe_conn()
    cur = conn.cursor()
    # Never let a background migration queue an ACCESS EXCLUSIVE lock behind a
    # long-running read. PostgreSQL would then queue ordinary reads behind the
    # migration too, making every API endpoint appear down.
    cur.execute("SET LOCAL lock_timeout = '3s'")
    cur.execute("SELECT pg_try_advisory_xact_lock(735724681)")
    if not cur.fetchone()[0]:
        conn.rollback()
        print("[DB] Migrations already running; skipping duplicate pass")
        return

    # Index for fast date-range queries (progressive loading)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_activities_run_date
        ON activities (start_date_local DESC)
        WHERE type = 'Run'
    """)
    # Ensure sync_meta table exists for token persistence
    cur.execute(_CREATE_SYNC_META_SQL)
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sync_meta'
    """)
    sync_meta_columns = {row[0] for row in cur.fetchall()}
    if "updated_at" not in sync_meta_columns:
        cur.execute(
            "ALTER TABLE sync_meta ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW()"
        )

    # VO2max history (Garmin) — one point per day for the evolution chart
    cur.execute(_CREATE_VO2MAX_SQL)
    cur.execute(_CREATE_SLEEP_SQL)
    cur.execute(_CREATE_SHOES_SQL)
    cur.execute(_CREATE_BIKES_SQL)
    cur.execute(_CREATE_TOMBSTONES_SQL)
    cur.execute(_CREATE_PLAN_OVERRIDES_SQL)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_laps_activity_id ON activity_laps (activity_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_splits_activity_id ON activity_splits (activity_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_best_efforts_activity_id ON activity_best_efforts (activity_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_streams_activity_id ON activity_streams (activity_id)")

    # Best efforts from Garmin splits (or legacy Strava) — populated by freshness check
    cur.execute("""CREATE TABLE IF NOT EXISTS activity_best_efforts (
        id BIGINT PRIMARY KEY,
        activity_id BIGINT NOT NULL,
        name TEXT,
        distance DOUBLE PRECISION,
        moving_time INTEGER,
        elapsed_time INTEGER,
        elevation_delta DOUBLE PRECISION
    )""")

    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'activities'
    """)
    activity_columns = {row[0] for row in cur.fetchall()}
    backfilled = 0

    # ALTER TABLE ... IF NOT EXISTS still requests an ACCESS EXCLUSIVE lock.
    # Check metadata first so normal startups never lock the live table.
    if "details_fetched_at" not in activity_columns:
        cur.execute("ALTER TABLE activities ADD COLUMN details_fetched_at TIMESTAMPTZ")
        cur.execute("UPDATE activities SET details_fetched_at = NOW()")
        backfilled = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if "sync_complete_at" not in activity_columns:
        cur.execute("ALTER TABLE activities ADD COLUMN sync_complete_at TIMESTAMPTZ")
    if "sync_status" not in activity_columns:
        cur.execute(
            "ALTER TABLE activities ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'partial'"
        )
    if "source" not in activity_columns:
        cur.execute("ALTER TABLE activities ADD COLUMN source TEXT DEFAULT 'garmin'")
    if "garmin_activity_id" not in activity_columns:
        cur.execute("ALTER TABLE activities ADD COLUMN garmin_activity_id BIGINT")
    run_metric_columns = ensure_run_metric_schema(conn)
    conn.commit()

    print(
        f"[DB] Migrations done. details_fetched_at backfilled: {backfilled}; "
        f"run metric columns added: {run_metric_columns}"
    )


# ── Activities ──

_ACTIVITY_COLUMNS = """
    id, name, start_date_local, distance, moving_time, elapsed_time,
    total_elevation_gain, average_speed, max_speed, average_heartrate,
    max_heartrate, map_summary_polyline, gear_id, sport_type, type,
    start_lat, start_lng, end_lat, end_lng, pr_count, suffer_score,
    calories, workout_type, has_heartrate, average_cadence, kudos_count,
    achievement_count, average_temp, elev_high, elev_low, description,
    average_watts, weighted_average_watts, max_watts, device_name,
    garmin_activity_uuid, garmin_timezone_id, garmin_device_id, lap_count,
    elevation_loss, max_cadence, aerobic_training_effect,
    anaerobic_training_effect, activity_training_load, vo2max,
    training_effect_label, avg_stride_length, avg_ground_contact_time,
    avg_vertical_oscillation, avg_vertical_ratio, avg_grade_adjusted_speed,
    body_battery_delta, steps, moderate_intensity_minutes,
    vigorous_intensity_minutes, min_temperature, max_temperature,
    avg_respiration_rate, min_respiration_rate, max_respiration_rate,
    water_estimated, garmin_workout_id, garmin_course_id, garmin_type_key,
    hr_time_in_zones, power_time_in_zones, garmin_fastest_splits,
    health_snapshot_at, health_sleep_date, health_sleep_score,
    health_sleep_quality, health_sleep_duration_seconds,
    health_sleep_start_local, health_sleep_end_local, health_hrv_date,
    health_hrv_last_night_avg_ms, health_hrv_weekly_avg_ms,
    health_hrv_status, health_hrv_baseline_low_ms,
    health_hrv_baseline_high_ms, health_resting_hr_date,
    health_resting_hr_bpm, health_resting_hr_7d_avg_bpm,
    weather_temperature, weather_apparent_temperature, weather_humidity,
    weather_precipitation, weather_wind_speed, weather_wind_gusts,
    weather_code, weather_source
"""

# Same check as isLikelyEncodedPolyline in src/lib/runMaps.js — regex runs at
# C speed, vs a per-character Python loop over every polyline on every read.
# fullmatch, pas `$`: en Python `$` matche avant un '\n' final, qui doit rester rejeté.
_POLYLINE_RE = re.compile(r"[\x3f-\x7e]+")


def _sane_polyline(value) -> str:
    """Return value only if it looks like a Google-encoded polyline.

    Some legacy Garmin syncs dumped a raw JSON blob of GPS samples
    (`{"lat": .., "lon": .., "time": ..}`, up to ~1 MB) into the
    map_summary_polyline column. Encoded polylines are pure ASCII in the
    0x3F–0x7E range; the JSON blob carries `"`, `:`, spaces, dots and digits
    (all < 0x3F), so reject any string with a char outside that range. This
    keeps the corrupt blob out of the API payload (it bloats
    /api/data/activities and decodes to garbage coords that break the Leaflet
    fitBounds on the cockpit map).
    """
    if not value or not isinstance(value, str):
        return ""
    if not _POLYLINE_RE.fullmatch(value):
        return ""
    return value


def _row_to_activity(row, cols) -> dict:
    """Convert a DB row to the flat dict the frontend expects."""
    d = dict(zip(cols, row))
    # Map DB column names to frontend expected names
    d["summary_polyline"] = _sane_polyline(d.pop("map_summary_polyline", ""))
    for key in (
        "hr_time_in_zones", "power_time_in_zones", "garmin_fastest_splits"
    ):
        if key in d:
            d[key] = _canonical_json(d[key])
    # Build start_latlng array from separate lat/lng columns
    lat = d.pop("start_lat", None)
    lng = d.pop("start_lng", None)
    d["start_latlng"] = [lat, lng] if lat and lng else None
    end_lat = d.pop("end_lat", None)
    end_lng = d.pop("end_lng", None)
    d["end_latlng"] = [end_lat, end_lng] if end_lat and end_lng else None
    if d.get("start_date_local"):
        d["start_date_local"] = _iso_notz(d["start_date_local"])
    # Ensure numeric defaults
    d["distance"] = d.get("distance") or 0
    d["moving_time"] = d.get("moving_time") or 0
    d["elapsed_time"] = d.get("elapsed_time") or 0
    d["total_elevation_gain"] = d.get("total_elevation_gain") or 0
    d["average_speed"] = d.get("average_speed") or 0
    d["max_speed"] = d.get("max_speed") or 0
    d["pr_count"] = d.get("pr_count") or 0
    return d


def _normalized_activity_timestamp(value) -> str:
    return str(value or "")[:19]


def _numeric_value(record: dict, *keys: str) -> float:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                continue
    return 0.0


def _has_latlng_pair(value) -> bool:
    return (
        isinstance(value, (list, tuple)) and
        len(value) >= 2 and
        value[0] is not None and
        value[1] is not None
    )


def _is_missing_value(value) -> bool:
    return value in (None, "", [], (), 0, 0.0, False)


def _pick_longest_string(left, right):
    left = left or ""
    right = right or ""
    return left if len(left) >= len(right) else right


def _pick_preferred_name(left, right):
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    generic = {"run", "course"}
    if left.lower() in generic and right.lower() not in generic:
        return right
    if right.lower() in generic and left.lower() not in generic:
        return left
    return left if len(left) >= len(right) else right


def _merged_ids(record: dict) -> list[int]:
    ids = record.get("merged_ids")
    if isinstance(ids, list) and ids:
        out = []
        for value in ids:
            try:
                out.append(int(value))
            except Exception:
                continue
        if out:
            return out
    value = record.get("id")
    if value is None:
        return []
    try:
        return [int(value)]
    except Exception:
        return []


def _activity_quality_score(activity: dict) -> int:
    score = 0
    if activity.get("summary_polyline"):
        score += 500
    if _has_latlng_pair(activity.get("start_latlng")):
        score += 120
    if _has_latlng_pair(activity.get("end_latlng")):
        score += 80
    if activity.get("average_heartrate"):
        score += 60
    if activity.get("max_heartrate"):
        score += 40
    if activity.get("average_speed"):
        score += 30
    if activity.get("total_elevation_gain"):
        score += 20
    if activity.get("pr_count"):
        score += 12
    if activity.get("achievement_count"):
        score += 10
    if activity.get("description"):
        score += 6
    score += min(int(_numeric_value(activity, "moving_time")) // 600, 8)
    return score


def _runs_look_like_duplicate(left: dict, right: dict) -> bool:
    left_ts = _normalized_activity_timestamp(left.get("start_date_local"))
    right_ts = _normalized_activity_timestamp(right.get("start_date_local"))
    if not left_ts or left_ts != right_ts:
        return False
    left_dist = _numeric_value(left, "distance", "distance_m")
    right_dist = _numeric_value(right, "distance", "distance_m")
    if left_dist and right_dist and abs(left_dist - right_dist) > 250:
        return False
    left_time = int(round(_numeric_value(left, "moving_time")))
    right_time = int(round(_numeric_value(right, "moving_time")))
    if left_time and right_time and abs(left_time - right_time) > 180:
        return False
    return True


def _merge_base(existing: dict, incoming: dict) -> tuple[dict, dict, list[int]]:
    """Shared merge skeleton: pick the richer record as primary, prefer the
    better name, union the merged ids. Returns (out, secondary, merged_ids)."""
    primary = existing if _activity_quality_score(existing) >= _activity_quality_score(incoming) else incoming
    secondary = incoming if primary is existing else existing
    out = dict(primary)
    out["name"] = _pick_preferred_name(primary.get("name"), secondary.get("name"))
    merged_ids = sorted(set(_merged_ids(existing) + _merged_ids(incoming)))
    out["merged_ids"] = merged_ids
    return out, secondary, merged_ids


def _merge_max_fields(out: dict, existing: dict, incoming: dict,
                      float_keys: tuple, int_keys: tuple) -> None:
    """Keep the larger of each numeric field across the two duplicate records."""
    for key in float_keys:
        out[key] = max(_numeric_value(existing, key), _numeric_value(incoming, key))
    for key in int_keys:
        out[key] = max(int(round(_numeric_value(existing, key))), int(round(_numeric_value(incoming, key))))


def _merge_activity_records(existing: dict, incoming: dict) -> dict:
    out, secondary, merged_ids = _merge_base(existing, incoming)

    out["summary_polyline"] = _pick_longest_string(existing.get("summary_polyline"), incoming.get("summary_polyline"))

    if not _has_latlng_pair(out.get("start_latlng")) and _has_latlng_pair(secondary.get("start_latlng")):
        out["start_latlng"] = secondary.get("start_latlng")
    if not _has_latlng_pair(out.get("end_latlng")) and _has_latlng_pair(secondary.get("end_latlng")):
        out["end_latlng"] = secondary.get("end_latlng")

    for key in (
        "gear_id", "sport_type", "type", "workout_type", "average_heartrate",
        "average_cadence", "average_temp", "suffer_score",
    ):
        if _is_missing_value(out.get(key)) and not _is_missing_value(secondary.get(key)):
            out[key] = secondary.get(key)

    _merge_max_fields(
        out, existing, incoming,
        float_keys=("distance", "total_elevation_gain", "average_speed",
                    "max_speed", "max_heartrate", "elev_high", "elev_low"),
        int_keys=("moving_time", "elapsed_time", "pr_count",
                  "achievement_count", "kudos_count", "calories"),
    )
    out["description"] = _pick_longest_string(existing.get("description"), incoming.get("description"))

    out["duplicate_ids"] = [value for value in merged_ids if value != out.get("id")]
    out["merged_count"] = len(merged_ids)
    return out


def _dedupe_records(records: list[dict], merge_fn) -> list[dict]:
    """Bucket by normalized start timestamp and merge look-alike duplicates."""
    if len(records) < 2:
        return records
    buckets: dict[str, list[int]] = {}
    merged: list[dict] = []

    for record in records:
        timestamp = _normalized_activity_timestamp(record.get("start_date_local"))
        if not timestamp:
            merged.append(dict(record))
            continue

        match_idx = None
        for idx in buckets.get(timestamp, []):
            if _runs_look_like_duplicate(record, merged[idx]):
                match_idx = idx
                break

        if match_idx is None:
            merged.append(dict(record))
            buckets.setdefault(timestamp, []).append(len(merged) - 1)
        else:
            merged[match_idx] = merge_fn(merged[match_idx], record)

    merged.sort(key=lambda record: record.get("start_date_local") or "", reverse=True)
    return merged


def _dedupe_activity_records(activities: list[dict]) -> list[dict]:
    return _dedupe_records(activities, _merge_activity_records)


def _merge_recent_plan_runs(existing: dict, incoming: dict) -> dict:
    out, _secondary, _merged = _merge_base(existing, incoming)

    _merge_max_fields(out, existing, incoming,
                      float_keys=("distance_m",), int_keys=("moving_time",))
    out["distance_km"] = round(out["distance_m"] / 1000.0, 2) if out["distance_m"] else 0.0
    if out["distance_m"] > 0 and out["moving_time"] > 0:
        out["pace_sec_per_km"] = out["moving_time"] / (out["distance_m"] / 1000.0)
    out["average_heartrate"] = max(_numeric_value(existing, "average_heartrate"), _numeric_value(incoming, "average_heartrate")) or None
    out["max_heartrate"] = max(_numeric_value(existing, "max_heartrate"), _numeric_value(incoming, "max_heartrate")) or None
    return out


def _dedupe_recent_plan_runs(runs: list[dict]) -> list[dict]:
    return _dedupe_records(runs, _merge_recent_plan_runs)


def attach_plan_run_structure(runs: list[dict]) -> list[dict]:
    """Attache les laps aux runs lus par le planificateur.

    Les moyennes d'une activite ne voient pas un fractionne courru en montagne
    (allure moyenne lente, FC moyenne basse) : les laps, elles, gardent
    l'alternance effort/recup qui signe la seance. Lecture best-effort — une
    table indisponible ne doit pas casser le plan.
    """
    ids = [int(run["id"]) for run in runs if run.get("id") is not None]
    if not ids:
        return runs
    try:
        conn = _safe_conn()
        cur = conn.cursor()
        placeholders = ", ".join(["%s"] * len(ids))
        cur.execute(f"""
            SELECT activity_id, lap_index, moving_time, elapsed_time, distance,
                   average_heartrate, max_heartrate
            FROM activity_laps
            WHERE activity_id IN ({placeholders})
            ORDER BY activity_id, lap_index
        """, ids)
        by_activity: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            by_activity.setdefault(int(row[0]), []).append({
                "lap_index": int(row[1] or 0),
                "moving_time": int(row[2] or 0) or int(row[3] or 0),
                "distance_m": float(row[4] or 0),
                "average_heartrate": float(row[5]) if row[5] is not None else None,
                "max_heartrate": float(row[6]) if row[6] is not None else None,
            })
    except Exception as e:
        print(f"[DB] attach_plan_run_structure failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            _safe_conn().rollback()
        except Exception:
            pass
        return runs

    for run in runs:
        laps = by_activity.get(int(run["id"])) if run.get("id") is not None else None
        if laps:
            run["laps"] = laps
    print(
        f"[plan-structure] laps attachees sur "
        f"{sum(1 for run in runs if run.get('laps'))}/{len(runs)} runs",
        file=sys.stderr,
    )
    return runs


def get_all_activities() -> list:
    """Return all Run activities as flat dicts (unbounded range query)."""
    return get_activities_range()


def get_activities_range(since: str = "", before: str = "") -> list:
    """Return Run activities in the half-open window [since, before).

    Both bounds are optional ISO date strings:
      - since only  → newer than `since`
      - before only → older than `before`
      - both        → a bounded segment (used by progressive windowed loading)
      - neither     → all runs
    No COUNT — fast. Backed by idx_activities_run_date.
    """
    clauses = ["type = 'Run'"]
    params: list = []
    if since:
        clauses.append("start_date_local >= %s")
        params.append(since)
    if before:
        clauses.append("start_date_local < %s")
        params.append(before)
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {_ACTIVITY_COLUMNS} FROM activities "
        f"WHERE {' AND '.join(clauses)} ORDER BY start_date_local DESC",
        params,
    )
    cols = [desc[0] for desc in cur.description]
    return _dedupe_activity_records([_row_to_activity(row, cols) for row in cur.fetchall()])


def get_activities_page(limit: int, offset: int = 0) -> tuple[list, int]:
    """Return a page of Run activities + total count (single round-trip).

    The total rides along as a window aggregate on every row; a separate
    COUNT(*) query runs only when the page is empty (offset past the end)."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT {_ACTIVITY_COLUMNS}, COUNT(*) OVER () AS __total
        FROM activities
        WHERE type = 'Run'
        ORDER BY start_date_local DESC
        LIMIT %s OFFSET %s
    """, (limit, offset))
    cols = [desc[0] for desc in cur.description][:-1]
    raw = cur.fetchall()
    if raw:
        total = raw[0][-1]
    else:
        cur.execute("SELECT COUNT(*) FROM activities WHERE type = 'Run'")
        total = cur.fetchone()[0]
    rows = _dedupe_activity_records([_row_to_activity(row[:-1], cols) for row in raw])
    return rows, total


def get_activity_count() -> int:
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM activities WHERE type = 'Run'")
    return cur.fetchone()[0]


def get_oldest_activity_date() -> str | None:
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("SELECT MIN(start_date_local) FROM activities WHERE type = 'Run'")
    row = cur.fetchone()
    if row and row[0]:
        return row[0].isoformat()
    return None


def get_latest_activity_date() -> str | None:
    """Most recent Run start_date_local (ISO string). Cheap — single aggregate
    row, used by the freshness probe instead of loading the whole table."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(start_date_local) FROM activities WHERE type = 'Run'")
    row = cur.fetchone()
    if row and row[0]:
        return row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
    return None


def get_all_activity_ids() -> set:
    """All activity IDs as a set (id-only, lightweight). Used for dedup when the
    freshness probe pulls recent activities from Garmin/Strava.

    Volontairement NON filtré sur type='Run' : la base stocke aussi les randos,
    vélos, ski… (fidélité Garmin). Filtrer ici les rendrait invisibles au dédoublon,
    donc « nouvelles » à chaque cycle — re-upsert et compteur `added` faussé à vie.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM activities")
    return {row[0] for row in cur.fetchall()}


def get_activity_tombstone_ids() -> set[str]:
    """Activity ids explicitly deleted by the user.

    Fresh databases may not have the tombstone table until migrations finish;
    returning an empty set keeps Garmin freshness available during that window.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT entity_id FROM sync_tombstones WHERE entity_type = 'activity'"
        )
        return {str(row[0]) for row in cur.fetchall() if row[0] is not None}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(
            f"[DB] get_activity_tombstone_ids unavailable: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return set()


def get_known_device_ids() -> set[int]:
    """Appareils Garmin qui ont déjà alimenté la base sans être rejetés.

    Sert de filet au filtre d'ingestion : Garmin fait tourner l'identifiant
    interne d'une même montre — Garmin la renumérote à chaque réappairage,
    plusieurs fois par an — donc s'en remettre au seul
    `get_devices()` du moment ferait rejeter des courses légitimes le jour où
    l'identifiant change encore. Un appareil déjà présent en base a été accepté
    une fois, il reste digne de confiance.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT DISTINCT garmin_device_id FROM activities "
            "WHERE garmin_device_id IS NOT NULL AND garmin_device_id > 0"
        )
        return {int(row[0]) for row in cur.fetchall() if row[0] is not None}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(
            f"[DB] get_known_device_ids unavailable: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return set()


def get_garmin_run_ids() -> set[int]:
    """Garmin activity ids that are already linked to canonical Run rows."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(garmin_activity_id, id)
        FROM activities
        WHERE type = 'Run' AND source = 'garmin'
    """)
    return {int(row[0]) for row in cur.fetchall() if row[0] is not None}


def get_activities_start_dates_since(since_iso: str) -> list[dict]:
    """Return [{start_date_local, distance, type}] for activities after since_iso.

    Sert au dédoublon dans garmin_freshness, qui a besoin des deux familles :
    `type='Run'` pour la déduplication Strava/Garmin d'une même course, le reste
    pour reconnaître une sortie enregistrée deux fois par Garmin (montre
    « Randonnée » + téléphone « Marche à pied »). D'où l'absence de filtre ici.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT start_date_local, distance, type FROM activities
        WHERE start_date_local >= %s
        ORDER BY start_date_local DESC
        LIMIT 300
    """, [since_iso])
    return [
        {"start_date_local": str(r[0]), "distance": r[1], "type": r[2]}
        for r in cur.fetchall()
    ]


def get_cross_training_activities() -> list[dict]:
    """Toutes les activités hors course (rando, vélo, ski, muscu…).

    Le site ne s'en sert jamais — il ne lit que `type = 'Run'`. Sert au
    rattrapage des doublons (scripts/dedupe_cross_training.py) et à tout outil
    qui veut la charge non-course.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, type, garmin_type_key, name, start_date_local, distance,
               moving_time, elapsed_time, total_elevation_gain
        FROM activities
        WHERE type <> 'Run'
        ORDER BY start_date_local DESC
    """)
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.rollback()
    for row in rows:
        row["start_date_local"] = _iso_notz(row["start_date_local"]) if row["start_date_local"] else ""
        row["distance"] = float(row["distance"] or 0)
        row["moving_time"] = int(row["moving_time"] or 0)
        row["elapsed_time"] = int(row["elapsed_time"] or 0)
    return rows


def _extract_latlng(a, key):
    val = a.get(key)
    if isinstance(val, (list, tuple)) and len(val) >= 2:
        return val[0], val[1]
    return None, None


def _safe_int(v, default=0):
    """Safely cast to int (handles float strings like '159.0')."""
    if v is None:
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _safe_float(v, default=None):
    """Safely cast to float."""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _get_sole_athlete_id() -> int | None:
    """Single-user app: return the one athlete_id stored in the athletes table.

    Activities.athlete_id is NOT NULL, so every INSERT must carry it. The app
    only ever has one athlete (the logged-in user), so we cache the id after
    the first lookup.
    """
    if getattr(_get_sole_athlete_id, "_cached", None) is not None:
        return _get_sole_athlete_id._cached
    try:
        conn = _safe_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM athletes ORDER BY id LIMIT 1")
        row = cur.fetchone()
        _get_sole_athlete_id._cached = row[0] if row else None
    except Exception as e:
        print(f"[DB] _get_sole_athlete_id error: {e}")
        _get_sole_athlete_id._cached = None
    return _get_sole_athlete_id._cached


_activities_pk_checked = False


def _ensure_activities_pk(conn) -> None:
    """Garantit que activities(id) porte une PRIMARY KEY avant tout upsert
    ON CONFLICT (id). Le mirror Postgres local peut recréer la table sans
    contrainte → INSERT ... ON CONFLICT échoue en 42P10. Caché : au plus une
    vérif par process ; Neon a déjà la PK donc c'est un no-op côté Vercel."""
    global _activities_pk_checked
    if _activities_pk_checked:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_schema = 'public' AND table_name = 'activities'
              AND constraint_type = 'PRIMARY KEY'
        """)
        if not cur.fetchone():
            cur.execute('ALTER TABLE activities ADD PRIMARY KEY (id)')
            conn.commit()
            print("[DB] activities: PRIMARY KEY (id) manquante — ajoutée", file=sys.stderr)
        _activities_pk_checked = True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[DB] _ensure_activities_pk: {type(e).__name__}: {e}", file=sys.stderr)


# Marks an activity as needing re-sync toward the replica (see sync_neon_local).
# Single source for the invalidation contract — used by every granular writer.
_INVALIDATE_SYNC_SQL = """
        UPDATE activities
        SET sync_complete_at = NULL, sync_status = 'partial'
        WHERE id = %s
    """

_RUN_COMPONENT_MARKERS = {
    "summary": "run_summary_updated_at",
    "zones": "run_zones_updated_at",
    "details": "run_details_updated_at",
    "laps": "run_laps_updated_at",
    "streams": "run_streams_updated_at",
    "weather": "run_weather_updated_at",
    "health": "run_health_updated_at",
}

# Colonnes météo Open-Meteo (source unique, partagée entre le writer app et le
# backfill local pour ne jamais diverger). Ordre = ordre des paramètres SQL.
WEATHER_COLUMNS = (
    "weather_temperature",
    "weather_apparent_temperature",
    "weather_humidity",
    "weather_precipitation",
    "weather_wind_speed",
    "weather_wind_gusts",
    "weather_code",
    "weather_source",
)

# UPDATE qui pose la météo + bump les marqueurs (composant « weather ») et
# invalide la sync pour que la réplication de l'app propage vers le réplica.
# COALESCE : ne pas écraser une valeur existante par un NULL entrant.
_ACTIVITY_WEATHER_UPDATE_SQL = """
        UPDATE activities SET
            weather_temperature = %s,
            weather_apparent_temperature = %s,
            weather_humidity = %s,
            weather_precipitation = %s,
            weather_wind_speed = %s,
            weather_wind_gusts = %s,
            weather_code = %s,
            weather_source = %s,
            run_weather_updated_at = CAST(%s AS TIMESTAMPTZ),
            run_metrics_updated_at = CAST(%s AS TIMESTAMPTZ),
            sync_complete_at = NULL,
            sync_status = 'partial'
        WHERE id = %s
    """


def _weather_row(activity_id: int, weather: dict, version: str) -> list:
    """Construit la ligne de paramètres pour _ACTIVITY_WEATHER_UPDATE_SQL."""
    return [
        _safe_float(weather.get("temperature_2m")),
        _safe_float(weather.get("apparent_temperature")),
        _safe_float(weather.get("relative_humidity_2m")),
        _safe_float(weather.get("precipitation")),
        _safe_float(weather.get("wind_speed_10m")),
        _safe_float(weather.get("wind_gusts_10m")),
        _safe_int(weather.get("weather_code"), None),
        weather.get("source") or weather.get("endpoint"),
        version,
        version,
        activity_id,
    ]


def upsert_activity_weather(activity_id: int, weather: dict) -> None:
    """Écrit la météo d'une course en primaire puis réplique (best-effort).

    Writer canonique côté app (import de run, futures mises à jour). Le backfill
    hors-ligne écrit lui-même en base LOCALE et n'appelle donc PAS ce writer,
    pour ne jamais ouvrir de connexion vers Neon depuis un script manuel.
    """
    if not weather:
        return
    version = _run_metrics_version()
    params = _weather_row(activity_id, weather, version)
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(_ACTIVITY_WEATHER_UPDATE_SQL, params)
    conn.commit()
    _replicate(
        f"upsert_activity_weather[{activity_id}]",
        lambda c: c.execute(_ACTIVITY_WEATHER_UPDATE_SQL, params),
    )


HEALTH_COLUMNS = (
    "health_snapshot_at",
    "health_sleep_date",
    "health_sleep_score",
    "health_sleep_quality",
    "health_sleep_duration_seconds",
    "health_sleep_start_local",
    "health_sleep_end_local",
    "health_hrv_date",
    "health_hrv_last_night_avg_ms",
    "health_hrv_weekly_avg_ms",
    "health_hrv_status",
    "health_hrv_baseline_low_ms",
    "health_hrv_baseline_high_ms",
    "health_resting_hr_date",
    "health_resting_hr_bpm",
    "health_resting_hr_7d_avg_bpm",
)

_ACTIVITY_HEALTH_UPDATE_SQL = """
        UPDATE activities SET
            health_snapshot_at = COALESCE(%s, health_snapshot_at),
            health_sleep_date = COALESCE(%s, health_sleep_date),
            health_sleep_score = COALESCE(%s, health_sleep_score),
            health_sleep_quality = COALESCE(%s, health_sleep_quality),
            health_sleep_duration_seconds = COALESCE(%s, health_sleep_duration_seconds),
            health_sleep_start_local = COALESCE(%s, health_sleep_start_local),
            health_sleep_end_local = COALESCE(%s, health_sleep_end_local),
            health_hrv_date = COALESCE(%s, health_hrv_date),
            health_hrv_last_night_avg_ms = COALESCE(%s, health_hrv_last_night_avg_ms),
            health_hrv_weekly_avg_ms = COALESCE(%s, health_hrv_weekly_avg_ms),
            health_hrv_status = COALESCE(%s, health_hrv_status),
            health_hrv_baseline_low_ms = COALESCE(%s, health_hrv_baseline_low_ms),
            health_hrv_baseline_high_ms = COALESCE(%s, health_hrv_baseline_high_ms),
            health_resting_hr_date = COALESCE(%s, health_resting_hr_date),
            health_resting_hr_bpm = COALESCE(%s, health_resting_hr_bpm),
            health_resting_hr_7d_avg_bpm = COALESCE(%s, health_resting_hr_7d_avg_bpm),
            run_health_updated_at = CAST(%s AS TIMESTAMPTZ),
            run_metrics_updated_at = CAST(%s AS TIMESTAMPTZ),
            sync_complete_at = NULL,
            sync_status = 'partial'
        WHERE id = %s
    """


def _activity_health_row(activity_id: int, health: dict, version: str) -> list:
    return [
        health.get("health_snapshot_at"),
        health.get("health_sleep_date"),
        _safe_int(health.get("health_sleep_score"), None),
        health.get("health_sleep_quality"),
        _safe_int(health.get("health_sleep_duration_seconds"), None),
        health.get("health_sleep_start_local"),
        health.get("health_sleep_end_local"),
        health.get("health_hrv_date"),
        _safe_float(health.get("health_hrv_last_night_avg_ms")),
        _safe_float(health.get("health_hrv_weekly_avg_ms")),
        health.get("health_hrv_status"),
        _safe_float(health.get("health_hrv_baseline_low_ms")),
        _safe_float(health.get("health_hrv_baseline_high_ms")),
        health.get("health_resting_hr_date"),
        _safe_int(health.get("health_resting_hr_bpm"), None),
        _safe_float(health.get("health_resting_hr_7d_avg_bpm")),
        version,
        version,
        activity_id,
    ]


def _has_health_values(health: dict) -> bool:
    return any(health.get(key) not in (None, "") for key in HEALTH_COLUMNS[1:])


def upsert_activity_health(activity_id: int, health: dict) -> bool:
    """Store the Garmin health/fatigue snapshot attached to one run end."""
    if not health or not _has_health_values(health):
        return False
    version = _run_metrics_version()
    params = _activity_health_row(activity_id, health, version)
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(_ACTIVITY_HEALTH_UPDATE_SQL, params)
    changed = bool(cur.rowcount and cur.rowcount > 0)
    conn.commit()
    if changed:
        _replicate(
            f"upsert_activity_health[{activity_id}]",
            lambda c: c.execute(_ACTIVITY_HEALTH_UPDATE_SQL, params),
        )
    return changed


def _mark_run_component_sql(component: str) -> str:
    marker = _RUN_COMPONENT_MARKERS[component]
    return f"""
        UPDATE activities
        SET {marker} = CAST(%s AS TIMESTAMPTZ),
            run_metrics_updated_at = CAST(%s AS TIMESTAMPTZ),
            sync_complete_at = NULL,
            sync_status = 'partial'
        WHERE id = %s
    """


def _run_metrics_version() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_payload(value) -> str | None:
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _garmin_summary_start_local(summary, fallback="") -> str:
    payload = _canonical_json(summary)
    if isinstance(payload, dict):
        value = payload.get("startTimeLocal")
        if value:
            try:
                return datetime.fromisoformat(str(value).replace(" ", "T")).isoformat()
            except Exception:
                return str(value)
    return _iso_notz(fallback) if fallback else ""


_GARMIN_RUN_SUMMARY_UPDATE_SQL = """
        UPDATE activities SET
            start_date = COALESCE(CAST(%s AS TIMESTAMPTZ), start_date),
            start_lat = COALESCE(%s, start_lat),
            start_lng = COALESCE(%s, start_lng),
            end_lat = COALESCE(%s, end_lat),
            end_lng = COALESCE(%s, end_lng),
            manual = COALESCE(%s, manual),
            private = COALESCE(%s, private),
            average_temp = COALESCE(%s, average_temp),
            average_watts = COALESCE(%s, average_watts),
            weighted_average_watts = COALESCE(%s, weighted_average_watts),
            max_watts = COALESCE(%s, max_watts),
            elev_high = COALESCE(%s, elev_high),
            elev_low = COALESCE(%s, elev_low),
            device_name = COALESCE(%s, device_name),
            garmin_activity_uuid = COALESCE(%s, garmin_activity_uuid),
            garmin_timezone_id = COALESCE(%s, garmin_timezone_id),
            garmin_device_id = COALESCE(%s, garmin_device_id),
            lap_count = COALESCE(%s, lap_count),
            elevation_loss = COALESCE(%s, elevation_loss),
            max_cadence = COALESCE(%s, max_cadence),
            aerobic_training_effect = COALESCE(%s, aerobic_training_effect),
            anaerobic_training_effect = COALESCE(%s, anaerobic_training_effect),
            activity_training_load = COALESCE(%s, activity_training_load),
            vo2max = COALESCE(%s, vo2max),
            training_effect_label = COALESCE(%s, training_effect_label),
            avg_stride_length = COALESCE(%s, avg_stride_length),
            avg_ground_contact_time = COALESCE(%s, avg_ground_contact_time),
            avg_vertical_oscillation = COALESCE(%s, avg_vertical_oscillation),
            avg_vertical_ratio = COALESCE(%s, avg_vertical_ratio),
            avg_grade_adjusted_speed = COALESCE(%s, avg_grade_adjusted_speed),
            body_battery_delta = COALESCE(%s, body_battery_delta),
            steps = COALESCE(%s, steps),
            moderate_intensity_minutes = COALESCE(%s, moderate_intensity_minutes),
            vigorous_intensity_minutes = COALESCE(%s, vigorous_intensity_minutes),
            min_temperature = COALESCE(%s, min_temperature),
            max_temperature = COALESCE(%s, max_temperature),
            avg_respiration_rate = COALESCE(%s, avg_respiration_rate),
            min_respiration_rate = COALESCE(%s, min_respiration_rate),
            max_respiration_rate = COALESCE(%s, max_respiration_rate),
            water_estimated = COALESCE(%s, water_estimated),
            garmin_workout_id = COALESCE(%s, garmin_workout_id),
            garmin_course_id = COALESCE(%s, garmin_course_id),
            hr_time_in_zones = COALESCE(hr_time_in_zones, CAST(%s AS JSONB)),
            power_time_in_zones = COALESCE(power_time_in_zones, CAST(%s AS JSONB)),
            run_zones_updated_at = COALESCE(
                CAST(%s AS TIMESTAMPTZ), run_zones_updated_at
            ),
            garmin_fastest_splits = COALESCE(CAST(%s AS JSONB), garmin_fastest_splits),
            garmin_summary = CAST(%s AS JSONB),
            run_summary_updated_at = CAST(%s AS TIMESTAMPTZ),
            run_metrics_updated_at = CAST(%s AS TIMESTAMPTZ),
            sync_complete_at = NULL,
            sync_status = 'partial'
        WHERE id = %s
    """


def _garmin_summary_row(activity: dict, version: str) -> list:
    slat, slng = _extract_latlng(activity, "start_latlng")
    elat, elng = _extract_latlng(activity, "end_latlng")
    return [
        activity.get("start_date_gmt"),
        _safe_float(slat), _safe_float(slng), _safe_float(elat), _safe_float(elng),
        activity.get("manual"), activity.get("private"),
        _safe_float(activity.get("average_temp")),
        _safe_float(activity.get("average_watts")),
        _safe_float(activity.get("weighted_average_watts")),
        _safe_int(activity.get("max_watts"), None),
        _safe_float(activity.get("elev_high")), _safe_float(activity.get("elev_low")),
        activity.get("device_name"), activity.get("garmin_activity_uuid"),
        _safe_int(activity.get("garmin_timezone_id"), None),
        _safe_int(activity.get("garmin_device_id"), None),
        _safe_int(activity.get("lap_count"), None),
        _safe_float(activity.get("elevation_loss")),
        _safe_float(activity.get("max_cadence")),
        _safe_float(activity.get("aerobic_training_effect")),
        _safe_float(activity.get("anaerobic_training_effect")),
        _safe_float(activity.get("activity_training_load")),
        _safe_float(activity.get("vo2max")), activity.get("training_effect_label"),
        _safe_float(activity.get("avg_stride_length")),
        _safe_float(activity.get("avg_ground_contact_time")),
        _safe_float(activity.get("avg_vertical_oscillation")),
        _safe_float(activity.get("avg_vertical_ratio")),
        _safe_float(activity.get("avg_grade_adjusted_speed")),
        _safe_int(activity.get("body_battery_delta"), None),
        _safe_int(activity.get("steps"), None),
        _safe_int(activity.get("moderate_intensity_minutes"), None),
        _safe_int(activity.get("vigorous_intensity_minutes"), None),
        _safe_float(activity.get("min_temperature")),
        _safe_float(activity.get("max_temperature")),
        _safe_float(activity.get("avg_respiration_rate")),
        _safe_float(activity.get("min_respiration_rate")),
        _safe_float(activity.get("max_respiration_rate")),
        _safe_float(activity.get("water_estimated")),
        _safe_int(activity.get("garmin_workout_id"), None),
        _safe_int(activity.get("garmin_course_id"), None),
        _json_payload(activity.get("hr_time_in_zones")),
        _json_payload(activity.get("power_time_in_zones")),
        version if activity.get("hr_time_in_zones") or activity.get("power_time_in_zones") else None,
        _json_payload(activity.get("garmin_fastest_splits")),
        _json_payload(activity.get("garmin_summary")),
        version,
        version,
        activity["id"],
    ]


def upsert_garmin_run_summaries(activities: list, force: bool = False) -> int:
    """Store run-only Garmin summary fields and replicate changed payloads."""
    candidates = [a for a in activities if a.get("garmin_summary") and a.get("id")]
    if not candidates:
        return 0
    conn = _safe_conn()
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(candidates))
    ids = [a["id"] for a in candidates]
    cur.execute(
        f"SELECT id, garmin_summary FROM activities WHERE id IN ({placeholders})",
        ids,
    )
    existing = {int(row[0]): _canonical_json(row[1]) for row in cur.fetchall()}
    changed = [
        activity for activity in candidates
        if force or existing.get(int(activity["id"])) != activity.get("garmin_summary")
    ]
    if not changed:
        return 0
    version = _run_metrics_version()
    rows = [_garmin_summary_row(activity, version) for activity in changed]
    cur.executemany(_GARMIN_RUN_SUMMARY_UPDATE_SQL, rows)
    conn.commit()

    def _repl(c):
        c.executemany(_GARMIN_RUN_SUMMARY_UPDATE_SQL, rows)
    _replicate(f"upsert_garmin_run_summaries[{len(rows)}]", _repl)
    print(f"[DB] Stored Garmin run summaries for {len(rows)} activities")
    return len(rows)


def upsert_activity_run_zones(activity_id: int, hr_zones: list, power_zones: list) -> None:
    if not hr_zones and not power_zones:
        return
    version = _run_metrics_version()
    sql = """
        UPDATE activities SET
            hr_time_in_zones = COALESCE(CAST(%s AS JSONB), hr_time_in_zones),
            power_time_in_zones = COALESCE(CAST(%s AS JSONB), power_time_in_zones),
            run_zones_updated_at = CAST(%s AS TIMESTAMPTZ),
            run_metrics_updated_at = CAST(%s AS TIMESTAMPTZ),
            sync_complete_at = NULL,
            sync_status = 'partial'
        WHERE id = %s
    """
    params = [
        _json_payload(hr_zones), _json_payload(power_zones),
        version, version, activity_id,
    ]
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    _replicate(f"upsert_activity_run_zones[{activity_id}]", lambda c: c.execute(sql, params))

_ACTIVITIES_UPSERT_SQL = """
        INSERT INTO activities (id, athlete_id, name, start_date_local, distance,
            moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed,
            average_heartrate, max_heartrate, map_summary_polyline, gear_id,
            sport_type, type, garmin_type_key, start_lat, start_lng, end_lat, end_lng,
            pr_count, suffer_score, calories, has_heartrate, average_cadence,
            source, garmin_activity_id, updated_at)
        VALUES {values}
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            distance = EXCLUDED.distance,
            moving_time = EXCLUDED.moving_time,
            elapsed_time = EXCLUDED.elapsed_time,
            total_elevation_gain = EXCLUDED.total_elevation_gain,
            average_speed = EXCLUDED.average_speed,
            max_speed = EXCLUDED.max_speed,
            average_heartrate = EXCLUDED.average_heartrate,
            max_heartrate = EXCLUDED.max_heartrate,
            map_summary_polyline = EXCLUDED.map_summary_polyline,
            gear_id = EXCLUDED.gear_id,
            sport_type = EXCLUDED.sport_type,
            type = EXCLUDED.type,
            garmin_type_key = COALESCE(EXCLUDED.garmin_type_key, activities.garmin_type_key),
            start_lat = COALESCE(EXCLUDED.start_lat, activities.start_lat),
            start_lng = COALESCE(EXCLUDED.start_lng, activities.start_lng),
            end_lat = COALESCE(EXCLUDED.end_lat, activities.end_lat),
            end_lng = COALESCE(EXCLUDED.end_lng, activities.end_lng),
            pr_count = EXCLUDED.pr_count,
            calories = EXCLUDED.calories,
            has_heartrate = EXCLUDED.has_heartrate,
            average_cadence = EXCLUDED.average_cadence,
            source = COALESCE(EXCLUDED.source, activities.source, 'garmin'),
            garmin_activity_id = COALESCE(EXCLUDED.garmin_activity_id, activities.garmin_activity_id),
            updated_at = EXCLUDED.updated_at,
            sync_complete_at = NULL,
            sync_status = 'partial'
"""


def upsert_activities(activities: list):
    """Insert/update activities (Strava or Garmin sync)."""
    if not activities:
        return
    db_athlete_id = _get_sole_athlete_id()
    if db_athlete_id is None:
        # No athlete row yet (Garmin-first setup) — use ID from the activity list
        db_athlete_id = next(
            (a.get("athlete_id") for a in activities if a.get("athlete_id")), 0
        )
        print(f"[DB] upsert_activities: no athlete row, using athlete_id={db_athlete_id}", file=sys.stderr)
    conn = _safe_conn()
    _ensure_activities_pk(conn)
    cur = conn.cursor()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # Keyed by id: a multi-row upsert rejects the same conflict target twice in
    # one statement, and "last row wins" matches the old per-row executemany.
    rows_by_id = {}
    remapped = 0
    for a in activities:
        slat, slng = _extract_latlng(a, "start_latlng")
        elat, elng = _extract_latlng(a, "end_latlng")
        # Mono-utilisateur : toute activité appartient à l'unique athlète en base.
        # Une activité Garmin porte l'ownerId Garmin (absent de la table athletes →
        # violation de fk_activities_athlete) ; on force donc l'athlète local.
        row_athlete_id = db_athlete_id or a.get("athlete_id") or 0
        if a.get("athlete_id") and a.get("athlete_id") != row_athlete_id:
            remapped += 1
        rows_by_id[a["id"]] = (
            a["id"], row_athlete_id, a.get("name", ""), a.get("start_date_local", ""),
            _safe_float(a.get("distance", 0), 0),
            _safe_int(a.get("moving_time", 0)),
            _safe_int(a.get("elapsed_time", 0)),
            _safe_int(a.get("total_elevation_gain", 0)),
            _safe_float(a.get("average_speed", 0), 0),
            _safe_float(a.get("max_speed", 0), 0),
            _safe_float(a.get("average_heartrate")),
            _safe_int(a.get("max_heartrate")),
            a.get("summary_polyline") or (a.get("map") or {}).get("summary_polyline", ""),
            a.get("gear_id"), a.get("sport_type", "Run"), a.get("type", "Run"),
            a.get("garmin_type_key"),
            _safe_float(slat), _safe_float(slng), _safe_float(elat), _safe_float(elng),
            _safe_int(a.get("pr_count", 0)),
            _safe_int(a.get("suffer_score")),
            _safe_float(a.get("calories")),
            bool(a.get("has_heartrate", False)),
            _safe_float(a.get("average_cadence")),
            a.get("source"),
            a.get("garmin_activity_id"),
            now,
        )
    if remapped:
        print(f"[DB] upsert_activities: {remapped} activité(s) ré-assignée(s) à "
              f"l'athlète local id={db_athlete_id}", file=sys.stderr)
    rows = list(rows_by_id.values())
    _executemany_values(cur, _ACTIVITIES_UPSERT_SQL, rows)
    conn.commit()
    print(f"[DB] Upserted {len(activities)} activities")

    # Replicate to the secondary DB (self-hosted: Neon; Vercel: local if set)
    # so the other runtime sees the new runs too.
    _replicate(
        f"upsert_activities[{len(activities)}]",
        lambda c: _executemany_values(c, _ACTIVITIES_UPSERT_SQL, rows),
    )
    upsert_garmin_run_summaries(activities)


# ── Computed Best Times (from activity_best_efforts table) ──

# Le seuil de pente et la table des distances vivent dans best_effort_rules, qui
# n'importe rien : le profil du coach (scripts/coach_journal.py) doit appliquer
# exactement la meme regle que la lecture ci-dessous, sans tirer pg8000 pour ca.
# Reexportes ici pour que db.MAX_NET_DROP_PER_KM et les imports existants
# continuent de fonctionner tels quels.
from best_effort_rules import (  # noqa: E402  (constantes reexportees)
    COMPUTED_EFFORT_NAMES,
    EFFORT_NAME_MAP,
    EFFORT_TARGET_METERS,
    MAX_NET_DROP_PER_KM,
    is_downhill_assisted,
)

def get_computed_bests(distance_type: str) -> list:
    """Get best efforts for a single distance type (thin wrapper on the bulk query)."""
    return get_computed_bests_bulk([distance_type]).get(distance_type, [])


def get_computed_bests_bulk(distance_types: list) -> dict:
    """get_computed_bests for several distance types in a single query.

    /api/data/prs needs all four race distances — one query replaces four
    sequential ones (each with its own _safe_conn round-trips on Neon).
    """
    name_by_type = {dtype: sname for sname, dtype in EFFORT_NAME_MAP.items()}
    wanted = {name_by_type[t]: t for t in distance_types if t in name_by_type}
    results = {t: [] for t in distance_types}
    if not wanted:
        return results
    try:
        conn = _safe_conn()
        cur = conn.cursor()
        # IN + placeholders plutôt que ANY(array): db_sqlite ne sait pas binder
        # une liste, et pg8000 accepte les deux formes.
        placeholders = ",".join(["%s"] * len(wanted))
        cur.execute(f"""
            SELECT be.name, be.activity_id, be.moving_time, be.elapsed_time, be.distance,
                   a.start_date_local, a.name, a.distance AS activity_distance,
                   a.moving_time AS activity_moving_time, be.elevation_delta
            FROM activity_best_efforts be
            JOIN activities a ON a.id = be.activity_id
            WHERE be.name IN ({placeholders})
              AND a.type = 'Run'
              AND (
                be.elevation_delta IS NULL
                OR be.elevation_delta >= 0 - {MAX_NET_DROP_PER_KM} * (COALESCE(be.distance, 0) / 1000.0)
              )
            ORDER BY be.moving_time ASC
        """, list(wanted.keys()))
        for row in cur.fetchall():
            results[wanted[row[0]]].append({
                "activityId": row[1],
                "timeSeconds": row[2] or row[3],
                "source": "best_efforts",
                "startDate": _iso_notz(row[5]),
                "name": row[6] or "",
                "distance": row[7] or 0,
                "movingTime": row[8] or 0,
                "elevationDelta": row[9],
            })
        conn.rollback()
        return results
    except Exception as e:
        print(f"[DB] get_computed_bests_bulk failed (non-fatal): {type(e).__name__}: {e}", file=sys.stderr)
        try:
            _safe_conn().rollback()
        except Exception:
            pass
        return {t: [] for t in distance_types}


# ── Activity Details (from activity_splits + activity_best_efforts) ──

def _ensure_best_effort_elevation_column(cur) -> None:
    """Guarantee activity_best_efforts.elevation_delta before writing efforts.

    Appelé sur la primaire ET dans la closure de réplication : la secondaire ne
    passe pas par init_db_migrations(), et un INSERT sur une colonne absente
    ouvrirait le disjoncteur de _replicate() à chaque run enrichi.
    """
    cur.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'activity_best_efforts'
          AND column_name = 'elevation_delta'
    """)
    if cur.fetchone():
        return
    cur.execute(
        "ALTER TABLE activity_best_efforts ADD COLUMN elevation_delta DOUBLE PRECISION"
    )


def _ensure_activity_splits_id_default(cur) -> None:
    """Repair and advance the split-id sequence before inserting split rows.

    Full-table mirrors used to copy explicit split ids without advancing the
    destination sequence. A default could therefore exist while its next value
    still collided with an old row.
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('activity_splits_id_default'))")
    cur.execute("""
        SELECT column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'activity_splits'
          AND column_name = 'id'
    """)
    row = cur.fetchone()
    if not row or not row[0]:
        cur.execute("CREATE SEQUENCE IF NOT EXISTS activity_splits_id_seq")
        cur.execute("ALTER SEQUENCE activity_splits_id_seq OWNED BY activity_splits.id")
        cur.execute("""
            ALTER TABLE activity_splits
            ALTER COLUMN id SET DEFAULT nextval('activity_splits_id_seq')
        """)

    cur.execute("SELECT pg_get_serial_sequence('public.activity_splits', 'id')")
    sequence = cur.fetchone()[0]
    cur.execute("""
        SELECT setval(
            CAST(%s AS regclass),
            COALESCE((SELECT MAX(id) FROM activity_splits), 0) + 1,
            false
        )
    """, [sequence])


def upsert_activity_details(activity_id: int, splits: list, best_efforts: list, distance: float = 0,
                            mark_fetched: bool = True, replace_splits: bool = False,
                            replace_efforts: bool = False):
    """Store splits and best efforts into normalized tables.

    mark_fetched=False leaves details_fetched_at untouched so the activity can
    be re-fetched later (Garmin returns empty details right after an upload).
    replace_splits=True drops existing metric splits first, so a richer
    re-fetch (stream-derived km splits) overwrites coarse lap-derived ones.
    replace_efforts=True drops the recomputed distances first : un upsert seul
    ne peut pas *retirer* un effort devenu inéligible (fenêtre trop descendante),
    puisqu'il n'écrit que les lignes qu'on lui donne.
    """
    delete_splits_sql = "DELETE FROM activity_splits WHERE activity_id = %s AND split_type = 'metric'"
    # Ne purge que les distances qu'on sait recalculer : un import Strava
    # historique peut porter d'autres noms (15K, 20K, 30K) qu'on ne refait pas.
    delete_efforts_sql = (
        "DELETE FROM activity_best_efforts WHERE activity_id = %s AND name IN ("
        + ",".join(["%s"] * len(COMPUTED_EFFORT_NAMES)) + ")"
    )
    delete_efforts_params = [activity_id, *COMPUTED_EFFORT_NAMES]
    split_sql = """
            INSERT INTO activity_splits (activity_id, split_index, split_type, distance,
                elapsed_time, moving_time, average_speed)
            VALUES {values}
            ON CONFLICT (activity_id, split_index, split_type) DO UPDATE SET
                distance = EXCLUDED.distance,
                elapsed_time = EXCLUDED.elapsed_time,
                moving_time = EXCLUDED.moving_time,
                average_speed = EXCLUDED.average_speed
    """
    effort_sql = """
            INSERT INTO activity_best_efforts (id, activity_id, name, distance, moving_time,
                elapsed_time, elevation_delta)
            VALUES {values}
            ON CONFLICT (id) DO UPDATE SET
                moving_time = EXCLUDED.moving_time,
                elapsed_time = EXCLUDED.elapsed_time,
                elevation_delta = EXCLUDED.elevation_delta
    """
    complete_status = _primary_complete_status()
    metrics_version = _run_metrics_version()
    mark_sql = """
        UPDATE activities
        SET details_fetched_at = NOW(),
            sync_complete_at = NULL,
            sync_status = %s
        WHERE id = %s
    """
    split_rows = []
    for s in splits:
        split_idx = s.get("split", 0)
        if not split_idx:
            continue
        split_rows.append([
            activity_id, split_idx, "metric",
            s.get("distance", 0), s.get("elapsed_time", 0),
            s.get("moving_time", 0), s.get("average_speed", 0),
        ])
    effort_rows = []
    for e in best_efforts:
        eid = e.get("id")
        if not eid:
            continue
        effort_rows.append([
            eid, activity_id, e.get("name", ""), e.get("distance", 0),
            e.get("moving_time", 0), e.get("elapsed_time", 0),
            e.get("elevation_delta"),
        ])
    do_replace = replace_splits and bool(split_rows)
    conn = _safe_conn()
    cur = conn.cursor()
    if split_rows:
        _ensure_activity_splits_id_default(cur)
    if do_replace:
        cur.execute(delete_splits_sql, [activity_id])
    if split_rows:
        _executemany_values(cur, split_sql, split_rows)
    if replace_efforts:
        _ensure_best_effort_elevation_column(cur)
        cur.execute(delete_efforts_sql, delete_efforts_params)
    if effort_rows:
        _ensure_best_effort_elevation_column(cur)
        _executemany_values(cur, effort_sql, effort_rows)
    if mark_fetched:
        cur.execute(mark_sql, [complete_status, activity_id])
    if split_rows or effort_rows or replace_efforts:
        cur.execute(
            _mark_run_component_sql("details"),
            [metrics_version, metrics_version, activity_id],
        )
    conn.commit()
    print(f"[DB] details stored for activity {activity_id} (splits={len(splits)}, efforts={len(best_efforts)}, marked={mark_fetched})")

    def _repl(c):
        if split_rows:
            _ensure_activity_splits_id_default(c)
        if do_replace:
            c.execute(delete_splits_sql, [activity_id])
        if split_rows:
            _executemany_values(c, split_sql, split_rows)
        if replace_efforts:
            _ensure_best_effort_elevation_column(c)
            c.execute(delete_efforts_sql, delete_efforts_params)
        if effort_rows:
            _ensure_best_effort_elevation_column(c)
            _executemany_values(c, effort_sql, effort_rows)
        if mark_fetched:
            c.execute(mark_sql, [complete_status, activity_id])
        if split_rows or effort_rows or replace_efforts:
            c.execute(
                _mark_run_component_sql("details"),
                [metrics_version, metrics_version, activity_id],
            )
    _replicate(f"upsert_activity_details[{activity_id}]", _repl)


def upsert_activity_laps(activity_id: int, laps: list):
    """Replace the laps of an activity (rows normalized upstream, Garmin lapDTOs)."""
    if not laps:
        return
    delete_sql = "DELETE FROM activity_laps WHERE activity_id = %s"
    insert_sql = """
        INSERT INTO activity_laps (id, activity_id, name, lap_index, distance,
            elapsed_time, moving_time, start_date, average_speed, max_speed,
            average_heartrate, max_heartrate, average_cadence, total_elevation_gain,
            elevation_loss, elev_high, elev_low, max_vertical_speed,
            start_lat, start_lng, end_lat, end_lng, max_cadence, average_watts,
            max_watts, min_watts, weighted_average_watts, total_work,
            grade_adjusted_speed, ground_contact_time, stride_length,
            vertical_oscillation, vertical_ratio, calories, bmr_calories,
            intensity_type, workout_step_index, workout_compliance_score, garmin_data)
        VALUES {values}
        ON CONFLICT (id) DO UPDATE SET
            activity_id = EXCLUDED.activity_id,
            name = EXCLUDED.name,
            lap_index = EXCLUDED.lap_index,
            distance = EXCLUDED.distance,
            elapsed_time = EXCLUDED.elapsed_time,
            moving_time = EXCLUDED.moving_time,
            start_date = EXCLUDED.start_date,
            average_speed = EXCLUDED.average_speed,
            max_speed = EXCLUDED.max_speed,
            average_heartrate = EXCLUDED.average_heartrate,
            max_heartrate = EXCLUDED.max_heartrate,
            average_cadence = EXCLUDED.average_cadence,
            total_elevation_gain = EXCLUDED.total_elevation_gain,
            elevation_loss = EXCLUDED.elevation_loss,
            elev_high = EXCLUDED.elev_high,
            elev_low = EXCLUDED.elev_low,
            max_vertical_speed = EXCLUDED.max_vertical_speed,
            start_lat = EXCLUDED.start_lat,
            start_lng = EXCLUDED.start_lng,
            end_lat = EXCLUDED.end_lat,
            end_lng = EXCLUDED.end_lng,
            max_cadence = EXCLUDED.max_cadence,
            average_watts = EXCLUDED.average_watts,
            max_watts = EXCLUDED.max_watts,
            min_watts = EXCLUDED.min_watts,
            weighted_average_watts = EXCLUDED.weighted_average_watts,
            total_work = EXCLUDED.total_work,
            grade_adjusted_speed = EXCLUDED.grade_adjusted_speed,
            ground_contact_time = EXCLUDED.ground_contact_time,
            stride_length = EXCLUDED.stride_length,
            vertical_oscillation = EXCLUDED.vertical_oscillation,
            vertical_ratio = EXCLUDED.vertical_ratio,
            calories = EXCLUDED.calories,
            bmr_calories = EXCLUDED.bmr_calories,
            intensity_type = EXCLUDED.intensity_type,
            workout_step_index = EXCLUDED.workout_step_index,
            workout_compliance_score = EXCLUDED.workout_compliance_score,
            garmin_data = EXCLUDED.garmin_data
    """
    rows = []
    for lap in laps:
        rows.append([
            lap.get("id"), activity_id, lap.get("name", ""), lap.get("lap_index", 0),
            lap.get("distance", 0), lap.get("elapsed_time", 0), lap.get("moving_time", 0),
            lap.get("start_date"), lap.get("average_speed", 0), lap.get("max_speed", 0),
            lap.get("average_heartrate"), lap.get("max_heartrate"),
            lap.get("average_cadence"), lap.get("total_elevation_gain", 0),
            lap.get("elevation_loss"), lap.get("elev_high"), lap.get("elev_low"),
            lap.get("max_vertical_speed"), lap.get("start_lat"), lap.get("start_lng"),
            lap.get("end_lat"), lap.get("end_lng"), lap.get("max_cadence"),
            lap.get("average_watts"), lap.get("max_watts"), lap.get("min_watts"),
            lap.get("weighted_average_watts"), lap.get("total_work"),
            lap.get("grade_adjusted_speed"), lap.get("ground_contact_time"),
            lap.get("stride_length"), lap.get("vertical_oscillation"),
            lap.get("vertical_ratio"), lap.get("calories"), lap.get("bmr_calories"),
            lap.get("intensity_type"), lap.get("workout_step_index"),
            lap.get("workout_compliance_score"), _json_payload(lap.get("garmin_data")),
        ])
    row_template = "(" + ",".join(
        ["%s"] * (len(rows[0]) - 1) + ["CAST(%s AS JSONB)"]
    ) + ")"
    metrics_version = _run_metrics_version()
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(delete_sql, [activity_id])
    _executemany_values(cur, insert_sql, rows, row_template=row_template)
    cur.execute(
        _mark_run_component_sql("laps"),
        [metrics_version, metrics_version, activity_id],
    )
    conn.commit()
    print(f"[DB] Upserted {len(rows)} laps for activity {activity_id}")

    def _repl(c):
        c.execute(delete_sql, [activity_id])
        _executemany_values(c, insert_sql, rows, row_template=row_template)
        c.execute(
            _mark_run_component_sql("laps"),
            [metrics_version, metrics_version, activity_id],
        )
    _replicate(f"upsert_activity_laps[{activity_id}]", _repl)


def get_recent_runs_missing_weather(days: int = 14, limit: int = 8) -> list:
    """Runs GPS récents encore sans météo (run_weather_updated_at NULL).

    Utilisé par le freshness check Garmin pour poser la météo à l'import et
    rattraper les runs arrivés sans elle (réplication depuis un déploiement
    qui ne la posait pas, échec Open-Meteo ponctuel...)."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, start_date_local, start_lat, start_lng
        FROM activities
        WHERE type = 'Run'
          AND start_lat IS NOT NULL AND start_lng IS NOT NULL
          AND run_weather_updated_at IS NULL
          AND start_date_local >= NOW() - make_interval(days => %s)
        ORDER BY start_date_local DESC
        LIMIT %s
    """, [days, limit])
    rows = cur.fetchall()
    conn.rollback()
    return [
        {"id": r[0], "start_date_local": str(r[1] or ""), "start_lat": r[2], "start_lng": r[3]}
        for r in rows
    ]


def get_recent_runs_missing_health(days: int = 14, limit: int = 8) -> list:
    """Recent runs still missing their Garmin health/fatigue snapshot."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, start_date_local, moving_time, elapsed_time, garmin_summary
        FROM activities
        WHERE type = 'Run'
          AND run_health_updated_at IS NULL
          AND start_date_local >= NOW() - make_interval(days => %s)
        ORDER BY start_date_local DESC
        LIMIT %s
    """, [days, limit])
    rows = cur.fetchall()
    conn.rollback()
    return [
        {
            "id": r[0],
            "start_date_local": _garmin_summary_start_local(r[4], r[1]),
            "moving_time": r[2] or 0,
            "elapsed_time": r[3] or 0,
        }
        for r in rows
    ]


def get_recent_garmin_activities_missing_details(days: int = 14) -> list:
    """Recent Garmin runs whose granular data (laps, splits or streams) is missing.

    Garmin can return empty splits/details right after an upload, so a single
    fetch at import time is not enough — the freshness check retries these."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.distance, a.start_date_local
        FROM activities a
        WHERE a.type = 'Run'
          AND a.source = 'garmin'
          AND a.start_date_local >= NOW() - make_interval(days => %s)
          AND (
            NOT EXISTS (SELECT 1 FROM activity_laps    l  WHERE l.activity_id  = a.id)
            OR NOT EXISTS (SELECT 1 FROM activity_splits  s  WHERE s.activity_id  = a.id)
            OR NOT EXISTS (SELECT 1 FROM activity_streams st WHERE st.activity_id = a.id)
          )
        ORDER BY a.start_date_local DESC
    """, [days])
    rows = cur.fetchall()
    conn.rollback()
    print(f"[DB] get_recent_garmin_activities_missing_details: {len(rows)} rows")
    return [{"id": r[0], "distance": r[1] or 0, "start_date_local": str(r[2] or "")} for r in rows]


# ── Gear (from shoes + bikes tables) ──

def upsert_gears(gears: list):
    """Upsert gear records. Only needed for API sync gap-filling."""
    if not gears:
        return
    conn = _safe_conn()
    cur = conn.cursor()
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # Get athlete_id from existing shoes or athletes table
    athlete_id = None
    try:
        cur.execute("SELECT athlete_id FROM shoes WHERE athlete_id IS NOT NULL LIMIT 1")
        row = cur.fetchone()
        if row:
            athlete_id = row[0]
    except Exception:
        conn.rollback()
    if not athlete_id:
        try:
            cur.execute("SELECT id FROM athletes LIMIT 1")
            row = cur.fetchone()
            if row:
                athlete_id = row[0]
        except Exception:
            conn.rollback()

    bike_sql = """
                    INSERT INTO bikes (id, athlete_id, name, brand_name, model_name, distance, retired, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, brand_name = EXCLUDED.brand_name,
                        model_name = EXCLUDED.model_name, distance = EXCLUDED.distance,
                        retired = EXCLUDED.retired, updated_at = EXCLUDED.updated_at
    """
    shoe_sql = """
                    INSERT INTO shoes (id, athlete_id, name, nickname, brand_name, model_name, distance,
                        primary_shoe, retired, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, nickname = EXCLUDED.nickname,
                        brand_name = EXCLUDED.brand_name, model_name = EXCLUDED.model_name,
                        distance = EXCLUDED.distance, primary_shoe = EXCLUDED.primary_shoe,
                        retired = EXCLUDED.retired, updated_at = EXCLUDED.updated_at
    """
    bike_rows = []
    shoe_rows = []
    for g in gears:
        gear_id = str(g.get("id") or "")
        if not gear_id:
            continue
        if gear_id.startswith("b"):
            params = [
                gear_id, athlete_id, g.get("name", ""), g.get("brand_name", ""),
                g.get("model_name", ""), _safe_float(g.get("distance", 0), 0),
                bool(g.get("retired", False)), now,
            ]
            sql = bike_sql
            bucket = bike_rows
        else:
            params = [
                gear_id, athlete_id, g.get("name", ""), g.get("nickname", ""),
                g.get("brand_name", ""), g.get("model_name", ""),
                _safe_float(g.get("distance", 0), 0), bool(g.get("primary", False)),
                bool(g.get("retired", False)), now,
            ]
            sql = shoe_sql
            bucket = shoe_rows
        try:
            cur.execute(sql, params)
            bucket.append(params)
        except Exception as e:
            print(f"[DB] Error upserting gear {gear_id}: {e}")
            conn.rollback()
    conn.commit()
    print(f"[DB] Upserted {len(gears)} gear records")

    def _repl(c):
        if bike_rows:
            c.executemany(bike_sql, bike_rows)
        if shoe_rows:
            c.executemany(shoe_sql, shoe_rows)
    _replicate(f"upsert_gears[{len(gears)}]", _repl)


def get_all_gears() -> list:
    """Return all shoes from the shoes table."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, nickname, brand_name, model_name, distance,
               -- "primary" est un mot reserve : sans guillemets, SQLite refuse
               -- la requete et /api/data/shoes renvoie 500 en mode dev.
               primary_shoe AS "primary", retired
        FROM shoes
        ORDER BY distance DESC
    """)
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_canonical_gear_map() -> dict:
    """Map a normalized shoe name -> the gear id activities should group under.

    The same physical shoe can exist twice in the shoes table: once from Strava
    (id like 'g31045794') and once from Garmin (id like 'g_<uuid>'). Activities
    historically carry the Strava id, so when both exist we prefer the Strava-
    style id (not prefixed 'g_') as the canonical id; otherwise the only id.
    """
    by_name: dict[str, list[str]] = {}
    for s in get_all_gears():
        name = (s.get("name") or "").strip().lower()
        sid = str(s.get("id") or "")
        if not name or not sid:
            continue
        by_name.setdefault(name, []).append(sid)
    out: dict[str, str] = {}
    for name, ids in by_name.items():
        strava_ids = [i for i in ids if not i.startswith("g_")]
        out[name] = strava_ids[0] if strava_ids else ids[0]
    return out


def count_ungeared_runs_since(since: str) -> int:
    """Cheap guard for gear reconciliation: Run rows without gear_id newer than
    `since` ('YYYY-MM-DD'). Lets the freshness cycle skip loading the full run
    timeline when there is nothing to reconcile (the steady-state case)."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM activities
        WHERE type = 'Run' AND (gear_id IS NULL OR gear_id = '')
          AND start_date_local >= %s
    """, [since])
    return cur.fetchone()[0]


def get_run_gear_rows() -> list:
    """Lightweight run rows for gear reconciliation.

    `sdl` is a tz-stable normalized timestamp ('YYYY-MM-DD HH24:MI:SS') so the
    Garmin copy and the Strava copy of one run hash to the same string.
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, garmin_activity_id, gear_id, sport_type, source,
               to_char(start_date_local, 'YYYY-MM-DD HH24:MI:SS') AS sdl
        FROM activities
        WHERE type = 'Run'
        ORDER BY start_date_local ASC
    """)
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_activity_gear(activity_id, gear_id: str) -> None:
    """Set one activity's gear_id (primary + replicated to the secondary DB)."""
    if not gear_id:
        return
    try:
        aid = int(activity_id)  # activities.id is a bigint
    except (TypeError, ValueError):
        return
    sql = "UPDATE activities SET gear_id = %s, updated_at = NOW() WHERE id = %s"
    params = [str(gear_id), aid]
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()

    def _repl(c):
        c.execute(sql, params)
    _replicate(f"update_activity_gear[{activity_id}]", _repl)


def _run_discipline(row) -> str:
    """'trail' for trail runs, else 'road' — used to inherit the right shoe."""
    return "trail" if "trail" in (row.get("sport_type") or "").lower() else "road"


def compute_gear_assignments(rows, max_gap_days: int = 400, since: str = "") -> dict:
    """Infer a gear_id for runs that have none, from the rows already in the DB.

    Pure function (no DB/network) so it is unit-testable. Rules:
      - Skip runs that already have a geared same-timestamp twin: that is the
        Strava copy of a Garmin-imported run, and the read-time dedup already
        merges the two and carries its gear, so we must NOT gear the second copy
        (two geared rows for one run could double-count in a non-deduped path).
      - Otherwise assign the active shoe of the period — the most recent
        *earlier* geared run of the same discipline (road vs trail), within
        `max_gap_days`.
    `since` (a 'YYYY-MM-DD' cutoff) bounds *which runs get a proposal* to recent
    ones: the discipline tag is unreliable on old history, so we only auto-fill
    recent gaps and never reattach ambiguous archive runs. The geared timeline
    used for inheritance still spans all of history.
    Returns {activity_id: gear_id} only for runs whose gear_id is empty.
    """
    geared = [r for r in rows if r.get("gear_id")]

    # Timestamps that already carry a geared run: the Strava copy of a Garmin
    # import. We only need membership, not the gear value (dedup merges them).
    geared_timestamps = {r.get("sdl") for r in geared if r.get("sdl")}

    timelines: dict[str, list] = {"road": [], "trail": []}
    for r in sorted(geared, key=lambda x: x.get("sdl") or ""):
        day = (r.get("sdl") or "")[:10]
        if day:
            timelines[_run_discipline(r)].append((day, r.get("gear_id")))

    def _active_gear(disc: str, day: str):
        best = None
        for d, gid in timelines.get(disc, []):
            if d <= day:
                best = (d, gid)
            else:
                break
        if not best:
            return None
        try:
            if (date.fromisoformat(day) - date.fromisoformat(best[0])).days > max_gap_days:
                return None
        except Exception:
            pass
        return best[1]

    out: dict = {}
    for r in rows:
        if r.get("gear_id"):
            continue
        ts = r.get("sdl") or ""
        day = ts[:10]
        if since and day and day < since:
            continue
        if ts in geared_timestamps:  # a geared twin already represents this run
            continue
        gid = _active_gear(_run_discipline(r), day)
        if gid:
            out[r["id"]] = gid
    return out


# ── Activity Streams (row-per-datapoint in new schema) ──

def upsert_streams(activity_id: int, streams: dict):
    """Store streams from Strava API into the row-per-datapoint table."""
    conn = _safe_conn()
    cur = conn.cursor()
    # Convert Strava key-by-type format to rows
    time_data = (streams.get("time") or {}).get("data", [])
    dist_data = (streams.get("distance") or {}).get("data", [])
    hr_data = (streams.get("heartrate") or {}).get("data", [])
    vel_data = (streams.get("velocity_smooth") or {}).get("data", [])
    alt_data = (streams.get("altitude") or {}).get("data", [])
    cad_data = (streams.get("cadence") or {}).get("data", [])
    watts_data = (streams.get("watts") or {}).get("data", [])
    temp_data = (streams.get("temperature") or {}).get("data", [])
    moving_data = (streams.get("moving") or {}).get("data", [])
    grade_data = (streams.get("grade_smooth") or {}).get("data", [])
    vertical_speed_data = (streams.get("vertical_speed") or {}).get("data", [])
    body_battery_data = (streams.get("body_battery") or {}).get("data", [])
    fractional_cadence_data = (streams.get("fractional_cadence") or {}).get("data", [])
    grade_adjusted_speed_data = (streams.get("grade_adjusted_speed") or {}).get("data", [])
    ground_contact_time_data = (streams.get("ground_contact_time") or {}).get("data", [])
    performance_condition_data = (streams.get("performance_condition") or {}).get("data", [])
    stride_length_data = (streams.get("stride_length") or {}).get("data", [])
    vertical_oscillation_data = (streams.get("vertical_oscillation") or {}).get("data", [])
    vertical_ratio_data = (streams.get("vertical_ratio") or {}).get("data", [])
    accumulated_power_data = (streams.get("accumulated_power") or {}).get("data", [])
    corrected_altitude_data = (streams.get("corrected_altitude") or {}).get("data", [])
    uncorrected_altitude_data = (streams.get("uncorrected_altitude") or {}).get("data", [])
    garmin_metrics_data = (streams.get("garmin_metrics") or {}).get("data", [])
    lat_data = []
    lng_data = []
    latlng = (streams.get("latlng") or {}).get("data", [])
    for ll in latlng:
        if isinstance(ll, (list, tuple)) and len(ll) == 2:
            lat_data.append(ll[0])
            lng_data.append(ll[1])
        else:
            lat_data.append(None)
            lng_data.append(None)

    n = len(time_data)
    if n == 0:
        return

    delete_sql = "DELETE FROM activity_streams WHERE activity_id = %s"
    insert_sql = """
        INSERT INTO activity_streams (activity_id, stream_index, time_sec, distance,
            lat, lng, altitude, velocity_smooth, heartrate, cadence,
            watts, temp, moving, grade_smooth, vertical_speed, body_battery,
            fractional_cadence, grade_adjusted_speed, ground_contact_time,
            performance_condition, stride_length, vertical_oscillation,
            vertical_ratio, accumulated_power, corrected_altitude,
            uncorrected_altitude, garmin_metrics)
        VALUES {values}
    """

    # Replace existing stream data for this activity
    cur.execute(delete_sql, [activity_id])

    # Batch insert. time_sec/heartrate/cadence are integer columns but Garmin
    # serves them as floats ("0.0") — coerce to int or Postgres rejects them.
    def _i(arr, i):
        v = arr[i] if i < len(arr) else None
        return None if v is None else int(round(float(v)))

    def _v(arr, i):
        return arr[i] if i < len(arr) else None

    def _b(arr, i):
        value = _v(arr, i)
        return None if value is None else bool(value)

    rows = []
    for i in range(n):
        rows.append((
            activity_id, i,
            _i(time_data, i),
            dist_data[i] if i < len(dist_data) else None,
            lat_data[i] if i < len(lat_data) else None,
            lng_data[i] if i < len(lng_data) else None,
            alt_data[i] if i < len(alt_data) else None,
            vel_data[i] if i < len(vel_data) else None,
            _i(hr_data, i),
            _i(cad_data, i),
            _i(watts_data, i),
            _v(temp_data, i),
            _b(moving_data, i),
            _v(grade_data, i),
            _v(vertical_speed_data, i),
            _v(body_battery_data, i),
            _v(fractional_cadence_data, i),
            _v(grade_adjusted_speed_data, i),
            _v(ground_contact_time_data, i),
            _v(performance_condition_data, i),
            _v(stride_length_data, i),
            _v(vertical_oscillation_data, i),
            _v(vertical_ratio_data, i),
            _v(accumulated_power_data, i),
            _v(corrected_altitude_data, i),
            _v(uncorrected_altitude_data, i),
            _json_payload(_v(garmin_metrics_data, i)),
        ))
    row_template = "(" + ",".join(
        ["%s"] * (len(rows[0]) - 1) + ["CAST(%s AS JSONB)"]
    ) + ")"
    metrics_version = _run_metrics_version()
    _executemany_values(cur, insert_sql, rows, row_template=row_template)
    cur.execute(
        _mark_run_component_sql("streams"),
        [metrics_version, metrics_version, activity_id],
    )
    conn.commit()
    print(f"[DB] Upserted {n} stream points for activity {activity_id}")

    def _repl(c):
        c.execute(delete_sql, [activity_id])
        _executemany_values(c, insert_sql, rows, row_template=row_template)
        c.execute(
            _mark_run_component_sql("streams"),
            [metrics_version, metrics_version, activity_id],
        )
    _replicate(f"upsert_streams[{activity_id}]", _repl)


def get_streams(activity_id: int) -> dict | None:
    """Reconstruct Strava-format streams from row-per-datapoint table."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT time_sec, distance, lat, lng, altitude, velocity_smooth,
               heartrate, cadence, watts, temp, moving, grade_smooth,
               vertical_speed, body_battery, fractional_cadence,
               grade_adjusted_speed, ground_contact_time,
               performance_condition, stride_length, vertical_oscillation,
               vertical_ratio, accumulated_power, corrected_altitude,
               uncorrected_altitude, garmin_metrics
        FROM activity_streams
        WHERE activity_id = %s
        ORDER BY stream_index ASC
    """, [activity_id])
    rows = cur.fetchall()
    if not rows:
        return None

    keys = (
        "time", "distance", "lat", "lng", "altitude", "velocity_smooth",
        "heartrate", "cadence", "watts", "temperature", "moving",
        "grade_smooth", "vertical_speed", "body_battery",
        "fractional_cadence", "grade_adjusted_speed", "ground_contact_time",
        "performance_condition", "stride_length", "vertical_oscillation",
        "vertical_ratio", "accumulated_power", "corrected_altitude",
        "uncorrected_altitude", "garmin_metrics",
    )
    data = dict(zip(keys, (list(col) for col in zip(*rows))))
    latlng = [
        [lat, lng]
        for lat, lng in zip(data.pop("lat"), data.pop("lng"))
        if lat is not None and lng is not None
    ]

    streams = {}
    for key, values in data.items():
        if key == "garmin_metrics":
            values = [_canonical_json(value) for value in values]
        if any(value is not None for value in values):
            streams[key] = {"data": values}
        if key == "distance" and latlng:  # keep historical payload key order
            streams["latlng"] = {"data": latlng}

    return {"streams": streams, "zones": get_athlete_zones()}


# ── Athlete Zones ──

# Nothing in the app writes athlete_zones (populated externally); a short TTL
# spares every streams read one query while still picking up manual edits.
_ZONES_CACHE_TTL = 600
_zones_cache = {"at": 0.0, "value": None}


def get_athlete_zones() -> list:
    """Get HR zones from athlete_zones table."""
    now = time.time()
    if now - _zones_cache["at"] < _ZONES_CACHE_TTL:
        return _zones_cache["value"]
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT zone_index, min_value, max_value
        FROM athlete_zones
        WHERE zone_type = 'heart_rate'
        ORDER BY zone_index ASC
    """)
    zones = []
    for row in cur.fetchall():
        zones.append({
            "min": row[1],
            "max": row[2] if row[2] > 0 else 999,
        })
    result = zones if zones else None
    _zones_cache["at"] = now
    _zones_cache["value"] = result
    return result


# ── Activity Splits ──

def get_activity_splits(activity_id: int) -> list:
    """Return splits for a single activity."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT split_index, distance, elapsed_time, moving_time, average_speed,
               elevation_difference, pace_zone
        FROM activity_splits
        WHERE activity_id = %s AND split_type = 'metric'
        ORDER BY split_index ASC
    """, [activity_id])
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Activity Laps ──

def get_activity_laps(activity_id: int) -> list:
    """Return laps for a single activity."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT lap_index, name, distance, elapsed_time, moving_time,
               average_speed, max_speed, average_heartrate, max_heartrate,
               average_cadence, total_elevation_gain, elevation_loss,
               elev_high, elev_low, max_vertical_speed,
               start_lat, start_lng, end_lat, end_lng, max_cadence,
               average_watts, max_watts, min_watts, weighted_average_watts,
               total_work, grade_adjusted_speed, ground_contact_time,
               stride_length, vertical_oscillation, vertical_ratio,
               calories, bmr_calories, intensity_type, workout_step_index,
               workout_compliance_score
        FROM activity_laps
        WHERE activity_id = %s
        ORDER BY lap_index ASC
    """, [activity_id])
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Delete ──

def delete_activity(activity_id: int):
    deletes = [
        ("DELETE FROM activities WHERE id = %s", [activity_id]),
        ("DELETE FROM activity_splits WHERE activity_id = %s", [activity_id]),
        ("DELETE FROM activity_best_efforts WHERE activity_id = %s", [activity_id]),
        ("DELETE FROM activity_streams WHERE activity_id = %s", [activity_id]),
        ("DELETE FROM activity_laps WHERE activity_id = %s", [activity_id]),
    ]
    conn = _safe_conn()
    cur = conn.cursor()
    for sql, params in deletes:
        cur.execute(sql, params)
    cur.execute("""
        INSERT INTO sync_tombstones (entity_type, entity_id, deleted_at)
        VALUES ('activity', %s, NOW())
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET deleted_at = NOW()
    """, [str(activity_id)])
    conn.commit()
    print(f"[DB] Deleted activity {activity_id} and all related data")

    def _repl(c):
        for sql, params in deletes:
            c.execute(sql, params)
        c.execute(_CREATE_TOMBSTONES_SQL)
        c.execute("""
            INSERT INTO sync_tombstones (entity_type, entity_id, deleted_at)
            VALUES ('activity', %s, NOW())
            ON CONFLICT (entity_type, entity_id) DO UPDATE SET deleted_at = NOW()
        """, [str(activity_id)])
    _replicate(f"delete_activity[{activity_id}]", _repl)


# ── Sync Meta ──

def _read_sync_meta(cur) -> dict:
    """Fetch and JSON-decode the sync_meta table (raw string on decode failure)."""
    cur.execute("SELECT key, value FROM sync_meta")
    result = {}
    for row in cur.fetchall():
        try:
            result[row[0]] = json.loads(row[1])
        except Exception:
            result[row[0]] = row[1]
    return result


def get_sync_meta() -> dict:
    try:
        return _read_sync_meta(_safe_conn().cursor())
    except Exception:
        return {}


def get_sync_meta_from_neon() -> dict:
    """Best-effort read of sync_meta from Neon when self-hosted uses a local DB.

    This lets the local runtime recover shared metadata (notably Garmin tokens)
    even if the last Neon -> local mirror skipped `sync_meta`, or when local
    token files are absent but Neon still has the canonical copy.
    """
    if not NEON_DATABASE_URL or NEON_DATABASE_URL == DATABASE_URL:
        return {}
    try:
        return _read_sync_meta(_safe_secondary_conn().cursor())
    except Exception as e:
        print(f"[DB-NEON] get_sync_meta_from_neon failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if getattr(_secondary_local, "conn", None) is not None:
                _secondary_local.conn.rollback()
        except Exception:
            pass
        return {}


def set_sync_meta(key: str, value):
    payload = json.dumps(value)
    upsert_sql = """
        INSERT INTO sync_meta (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, updated_at = NOW()
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(upsert_sql, [key, payload])
    conn.commit()

    # Replicate so the secondary DB stays in sync (e.g. Garmin tokens shared
    # between self-hosted and Vercel via sync_meta).
    def _repl(c):
        c.execute(_CREATE_SYNC_META_SQL)
        c.execute(upsert_sql, [key, payload])
    _replicate(f"set_sync_meta[{key}]", _repl)


# ── Ajustements du plan (coach) ──

def get_plan_overrides() -> dict:
    """Ajustements du coach, indexes par jour ISO.

    Lecture best-effort : si la table n'existe pas encore (migration pas passee)
    ou si la base est indisponible, le plan code en dur reste affiche tel quel.
    """
    # Lu a chaque chargement du plan : on tente le SELECT seul, sans DDL, pour ne
    # pas payer un aller-retour de migration par requete. La table est creee par
    # init_db_migrations ; le rattrapage ci-dessous ne sert qu'au premier passage.
    try:
        conn = _safe_conn()
        cur = conn.cursor()
        cur.execute("SELECT day, payload, note, source FROM plan_overrides ORDER BY day")
        rows = cur.fetchall()
    except Exception as e:
        print(f"[DB] get_plan_overrides failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if getattr(_local, "conn", None) is not None:
                _local.conn.rollback()
            conn = _safe_conn()
            cur = conn.cursor()
            cur.execute(_CREATE_PLAN_OVERRIDES_SQL)
            conn.commit()
        except Exception as create_err:
            print(
                f"[DB] plan_overrides create failed: {type(create_err).__name__}: {create_err}",
                file=sys.stderr,
            )
        return {}

    overrides = {}
    for day, payload, note, source in rows:
        try:
            session = json.loads(payload)
        except (TypeError, ValueError):
            print(f"[DB] plan_overrides[{day}] payload illisible, ignore", file=sys.stderr)
            continue
        if not isinstance(session, dict):
            continue
        overrides[str(day)[:10]] = {"session": session, "note": note, "source": source}
    return overrides


def upsert_plan_override(day: str, session: dict, note: str = "", source: str = "coach"):
    """Enregistre l'ajustement du coach pour un jour. Replique vers la 2e base."""
    day_iso = str(day)[:10]
    payload = json.dumps(session)
    upsert_sql = """
        INSERT INTO plan_overrides (day, payload, note, source, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (day) DO UPDATE SET
            payload = EXCLUDED.payload,
            note = EXCLUDED.note,
            source = EXCLUDED.source,
            updated_at = NOW()
    """
    params = [day_iso, payload, note or None, source or None]
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(_CREATE_PLAN_OVERRIDES_SQL)
    cur.execute(upsert_sql, params)
    conn.commit()

    def _repl(c):
        c.execute(_CREATE_PLAN_OVERRIDES_SQL)
        c.execute(upsert_sql, params)
    _replicate(f"upsert_plan_override[{day_iso}]", _repl)
    return day_iso


def delete_plan_override(day: str) -> bool:
    """Supprime l'ajustement d'un jour (retour au plan code en dur)."""
    day_iso = str(day)[:10]
    delete_sql = "DELETE FROM plan_overrides WHERE day = %s"
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(_CREATE_PLAN_OVERRIDES_SQL)
    cur.execute(delete_sql, [day_iso])
    removed = cur.rowcount > 0
    conn.commit()

    def _repl(c):
        c.execute(_CREATE_PLAN_OVERRIDES_SQL)
        c.execute(delete_sql, [day_iso])
    _replicate(f"delete_plan_override[{day_iso}]", _repl)
    return removed


def upsert_vo2max(date: str, value):
    """Store one VO2max point (date = 'YYYY-MM-DD'). Replicated to the secondary DB."""
    if value is None:
        return
    try:
        value = float(value)
    except (TypeError, ValueError):
        return
    upsert_sql = """
        INSERT INTO vo2max_history (date, vo2max, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (date) DO UPDATE SET vo2max = EXCLUDED.vo2max, updated_at = NOW()
    """
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(upsert_sql, [str(date), value])
    conn.commit()

    def _repl(c):
        c.execute(_CREATE_VO2MAX_SQL)
        c.execute(upsert_sql, [str(date), value])
    _replicate(f"upsert_vo2max[{date}]", _repl)


def upsert_sleep_score(
    day: str,
    score,
    quality: str | None = None,
    duration_seconds=None,
) -> bool:
    """Store one Garmin sleep point.

    Garmin's sleep date is the wake-up day, not the night start day.
    """
    score_value = _safe_int(score, None)
    duration_value = _safe_int(duration_seconds, None)
    if score_value is None and duration_value is None:
        return False
    upsert_sql = """
        INSERT INTO sleep_history
            (date, sleep_score, sleep_quality, sleep_duration_seconds, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (date) DO UPDATE SET
            sleep_score = EXCLUDED.sleep_score,
            sleep_quality = EXCLUDED.sleep_quality,
            sleep_duration_seconds = EXCLUDED.sleep_duration_seconds,
            updated_at = NOW()
    """
    params = [str(day)[:10], score_value, quality, duration_value]
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute(upsert_sql, params)
    conn.commit()

    def _repl(c):
        c.execute(_CREATE_SLEEP_SQL)
        c.execute(upsert_sql, params)
    _replicate(f"upsert_sleep_score[{day}]", _repl)
    return True


def get_vo2max_history() -> list:
    """Return VO2max points ordered by date asc: [{date, vo2max}]."""
    conn = _safe_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT date, vo2max FROM vo2max_history ORDER BY date ASC")
        return [{"date": str(r[0]), "vo2max": r[1]} for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB] get_vo2max_history failed: {e}", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def get_recent_runs_for_plan(target_date: str, days: int = 3) -> list:
    """Return recent runs for the rolling daily-plan window ending on target_date."""
    day = date.fromisoformat(str(target_date)[:10])
    start = (day - timedelta(days=max(0, days - 1))).isoformat()
    end = (day + timedelta(days=1)).isoformat()
    for attempt in range(2):
        conn = _safe_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, name, start_date_local, distance, moving_time, average_heartrate, max_heartrate
                FROM activities
                WHERE type = 'Run'
                  AND start_date_local >= %s
                  AND start_date_local < %s
                ORDER BY start_date_local DESC
            """, [start, end])
            runs = []
            for row in cur.fetchall():
                distance_m = float(row[3] or 0)
                moving_time = int(row[4] or 0)
                pace = (moving_time / (distance_m / 1000.0)) if distance_m > 0 and moving_time > 0 else None
                iso = _iso_notz(row[2]) if row[2] else ""
                runs.append({
                    "id": row[0],
                    "name": row[1] or "Run",
                    "start_date_local": iso,
                    "date": iso[:10],
                    "distance_m": distance_m,
                    "distance_km": round(distance_m / 1000.0, 2),
                    "moving_time": moving_time,
                    "pace_sec_per_km": pace,
                    "average_heartrate": float(row[5]) if row[5] is not None else None,
                    "max_heartrate": float(row[6]) if row[6] is not None else None,
                })
            return attach_plan_run_structure(_dedupe_recent_plan_runs(runs))
        except Exception as e:
            print(f"[DB] get_recent_runs_for_plan failed ({attempt + 1}/2): {type(e).__name__}: {e}", file=sys.stderr)
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                if getattr(_local, "conn", None) is not None:
                    _local.conn.close()
            except Exception:
                pass
            _local.conn = None
    return []


def get_latest_sleep_score(target_date: str) -> dict | None:
    """Return sleep for the target wake-up day, or None when unavailable."""
    conn = _safe_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT date, sleep_score, sleep_quality, sleep_duration_seconds
            FROM sleep_history
            WHERE date = %s AND sleep_score IS NOT NULL
            LIMIT 1
        """, [str(target_date)[:10]])
        row = cur.fetchone()
        if not row:
            return None
        return {
            "date": str(row[0]),
            "sleep_score": row[1],
            "sleep_quality": row[2],
            "sleep_duration_seconds": row[3],
        }
    except Exception as e:
        print(f"[DB] get_latest_sleep_score failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ── Athlete ──

def get_athlete() -> dict | None:
    """Return athlete profile."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, firstname, lastname, city, country, sex, weight, profile_url
        FROM athletes LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "firstname": row[1], "lastname": row[2],
        "city": row[3], "country": row[4], "sex": row[5],
        "weight": row[6], "profile": row[7],
    }


def get_athlete_stats() -> dict | None:
    """Return athlete stats."""
    conn = _safe_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM athlete_stats LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


# ── Status ──

def get_sync_status() -> dict:
    """Aggregate sync counters for /api/data/status (polled by the frontend).

    One scan of activities computes every counter — five sequential COUNT
    queries (each paying its own _safe_conn round-trips) used to dominate the
    endpoint latency on Neon. The EXISTS probe per run stays cheaper than a
    DISTINCT over the full streams table; detailsRemaining intentionally only
    counts never-fetched runs (recent runs lacking best_efforts are retried by
    backfill jobs but would pin the UI banner on a misleading remainder).
    """
    conn = _safe_conn()
    cur = conn.cursor()
    # list_complete rides along as one more output column: reading it through
    # get_sync_meta() would pay a second conn probe + query per status poll.
    cur.execute("""
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE details_fetched_at IS NOT NULL),
            COUNT(*) FILTER (WHERE details_fetched_at IS NULL),
            COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM activity_streams s WHERE s.activity_id = a.id
            )),
            (SELECT COUNT(*) FROM activity_best_efforts
             WHERE name IN ('5K', '10K', 'Half-Marathon')),
            (SELECT value FROM sync_meta WHERE key = 'list_complete')
        FROM activities a
        WHERE a.type = 'Run'
    """)
    activity_count, details_count, need_details, streams_count, computed_count, list_complete_raw = cur.fetchone()
    try:
        list_complete = json.loads(list_complete_raw) if list_complete_raw is not None else True
    except Exception:
        list_complete = list_complete_raw
    streams_remaining = max(0, activity_count - streams_count)
    return {
        "activityCount": activity_count,
        "listComplete": list_complete,  # DB is source of truth
        "detailsCount": details_count,
        "detailsRemaining": need_details,
        "detailsComplete": need_details == 0 and activity_count > 0,
        "computedCount": computed_count,
        "streamsCount": streams_count,
        "streamsRemaining": streams_remaining,
        "streamsComplete": streams_remaining == 0 and activity_count > 0,
    }


def get_db_readiness() -> dict:
    """Lightweight check: is the Neon DB populated enough to drive the UI?

    Originally called `get_sync_status()` which runs 6 queries including a
    full SELECT on activities-without-details just to do `len()` over it.
    On a populated Neon DB that pushed the endpoint past Vercel's gateway
    timeout (60s) at cold start. The frontend only logs the result and
    doesn't gate rendering on it, so a single COUNT is sufficient.
    """
    try:
        activity_count = get_activity_count()
    except Exception as e:
        return {"ready": False, "reason": f"db_error:{type(e).__name__}", "activityCount": 0}

    if activity_count == 0:
        return {"ready": False, "reason": "no_activities", "activityCount": 0}
    return {"ready": True, "reason": "ok", "activityCount": activity_count}
