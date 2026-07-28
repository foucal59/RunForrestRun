#!/usr/bin/env python3
"""Incrementally converge Neon and local PostgreSQL without full-table copies.

Only runs whose `sync_status` is not `ok` participate. For those pending runs,
the sync exchanges lightweight manifests and child-row counts. Raw rows move
only when an activity is missing or one side has fewer laps, splits, best
efforts, or stream points.

Once both databases have the run, matching child counts, and
`details_fetched_at`, both rows receive `sync_complete_at`. Complete runs then
disappear entirely from future syncs.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pg8000.dbapi

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database_pg import SMALL_TABLE_DDL, ensure_run_metric_schema


CHILD_TABLES = (
    "activity_laps",
    "activity_splits",
    "activity_best_efforts",
    "activity_streams",
)

CHILD_INDEXES = (
    ("activity_laps", "idx_activity_laps_activity_id"),
    ("activity_splits", "idx_activity_splits_activity_id"),
    ("activity_best_efforts", "idx_activity_best_efforts_activity_id"),
    ("activity_streams", "idx_activity_streams_activity_id"),
)

ACTIVITY_COMPONENTS = {
    "summary": {
        "marker": "run_summary_updated_at",
        "columns": (
            "start_date", "start_lat", "start_lng", "end_lat", "end_lng",
            "manual", "private", "average_temp", "average_watts",
            "weighted_average_watts", "max_watts", "elev_high", "elev_low",
            "device_name", "garmin_activity_uuid", "garmin_timezone_id",
            "garmin_device_id", "lap_count", "elevation_loss", "max_cadence",
            "aerobic_training_effect", "anaerobic_training_effect",
            "activity_training_load", "vo2max", "training_effect_label",
            "avg_stride_length", "avg_ground_contact_time",
            "avg_vertical_oscillation", "avg_vertical_ratio",
            "avg_grade_adjusted_speed", "body_battery_delta", "steps",
            "moderate_intensity_minutes", "vigorous_intensity_minutes",
            "min_temperature", "max_temperature", "avg_respiration_rate",
            "min_respiration_rate", "max_respiration_rate", "water_estimated",
            "garmin_workout_id", "garmin_course_id", "garmin_fastest_splits",
            "garmin_summary", "run_summary_updated_at",
        ),
    },
    "zones": {
        "marker": "run_zones_updated_at",
        "columns": (
            "hr_time_in_zones", "power_time_in_zones", "run_zones_updated_at",
        ),
    },
    "weather": {
        "marker": "run_weather_updated_at",
        "columns": (
            "weather_temperature", "weather_apparent_temperature",
            "weather_humidity", "weather_precipitation", "weather_wind_speed",
            "weather_wind_gusts", "weather_code", "weather_source",
            "run_weather_updated_at",
        ),
    },
    "health": {
        "marker": "run_health_updated_at",
        "columns": (
            "health_snapshot_at", "health_sleep_date", "health_sleep_score",
            "health_sleep_quality", "health_sleep_duration_seconds",
            "health_sleep_start_local", "health_sleep_end_local",
            "health_hrv_date", "health_hrv_last_night_avg_ms",
            "health_hrv_weekly_avg_ms", "health_hrv_status",
            "health_hrv_baseline_low_ms", "health_hrv_baseline_high_ms",
            "health_resting_hr_date", "health_resting_hr_bpm",
            "health_resting_hr_7d_avg_bpm", "run_health_updated_at",
        ),
    },
}

CHILD_COMPONENT_MARKERS = {
    "activity_laps": "run_laps_updated_at",
    "activity_splits": "run_details_updated_at",
    "activity_best_efforts": "run_details_updated_at",
    "activity_streams": "run_streams_updated_at",
}

CHILD_ORDER_CANDIDATES = {
    "activity_laps": ("lap_index",),
    "activity_splits": ("split_type", "split_index"),
    "activity_best_efforts": ("name", "distance", "id"),
    "activity_streams": ("stream_index",),
}

CHILD_HASH_IGNORED_COLUMNS = {
    "activity_laps": ("id",),
    "activity_splits": ("id",),
}

SMALL_TABLES = {
    "vo2max_history": "date",
    "sleep_history": "date",
    "shoes": "id",
    "bikes": "id",
    "sync_meta": "key",
}


def _parse_db_url(url: str) -> dict:
    parsed = urlparse(url)
    params: dict = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
        "timeout": int(os.environ.get("SYNC_DB_TIMEOUT", "120")),
    }
    qs = parse_qs(parsed.query)
    if qs.get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    return params


def _connect(url: str, label: str):
    print(f"[incremental-sync] connecting to {label} ({urlparse(url).hostname})", file=sys.stderr)
    return pg8000.dbapi.connect(**_parse_db_url(url))


def _table_exists(conn, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT to_regclass(%s)", [f"public.{table}"])
    row = cur.fetchone()
    return bool(row and row[0])


def _column_exists(conn, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
    """, [table, column])
    return bool(cur.fetchone())


def _index_exists(conn, index: str) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = %s
    """, [index])
    return bool(cur.fetchone())


def _ensure_support(conn, label: str, dry_run: bool) -> None:
    if dry_run:
        return
    cur = conn.cursor()
    if not _column_exists(conn, "activities", "sync_complete_at"):
        cur.execute("ALTER TABLE activities ADD COLUMN sync_complete_at TIMESTAMPTZ")
    if not _column_exists(conn, "activities", "sync_status"):
        cur.execute("ALTER TABLE activities ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'partial'")
    if not _table_exists(conn, "sync_tombstones"):
        cur.execute("""
            CREATE TABLE sync_tombstones (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (entity_type, entity_id)
        )
        """)
    for table, ddl in SMALL_TABLE_DDL.items():
        if not _table_exists(conn, table):
            cur.execute(ddl)
            print(f"[incremental-sync] {label}: created {table}", file=sys.stderr)
    if (
        _table_exists(conn, "sync_meta")
        and not _column_exists(conn, "sync_meta", "updated_at")
    ):
        cur.execute(
            "ALTER TABLE sync_meta ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW()"
        )
        print(
            f"[incremental-sync] {label}: added sync_meta.updated_at",
            file=sys.stderr,
        )
    added = ensure_run_metric_schema(conn)
    if added:
        print(f"[incremental-sync] {label}: run metric columns added {added}", file=sys.stderr)
    conn.commit()
    for table, index in CHILD_INDEXES:
        if not _table_exists(conn, table) or _index_exists(conn, index):
            continue
        try:
            cur.execute(f'CREATE INDEX "{index}" ON public."{table}" (activity_id)')
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"[incremental-sync] {label}: index {index} skipped: {exc}", file=sys.stderr)


def _pending_activity_ids(conn) -> set[int]:
    cur = conn.cursor()
    cur.execute("""
        SELECT id
        FROM activities
        WHERE type = 'Run'
          AND (sync_status <> 'ok' OR sync_complete_at IS NULL)
    """)
    return {int(row[0]) for row in cur.fetchall()}


def _activity_manifest(conn, activity_ids: set[int]) -> dict[int, dict]:
    if not activity_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(activity_ids))
    marker_names = (
        "run_metrics_updated_at", "run_summary_updated_at",
        "run_zones_updated_at", "run_details_updated_at",
        "run_laps_updated_at", "run_streams_updated_at",
        "run_weather_updated_at", "run_health_updated_at",
    )
    marker_select = []
    for marker in marker_names:
        if _column_exists(conn, "activities", marker):
            marker_select.append(f'"{marker}"')
        else:
            marker_select.append(f'NULL AS "{marker}"')
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, details_fetched_at, sync_complete_at, sync_status,
               {", ".join(marker_select)}
        FROM activities
        WHERE id IN ({placeholders})
    """, sorted(activity_ids))
    return {
        int(row[0]): {
            "details_fetched_at": row[1],
            "sync_complete_at": row[2],
            "sync_status": row[3],
            **{name: row[4 + index] for index, name in enumerate(marker_names)},
        }
        for row in cur.fetchall()
    }


def _child_counts(conn, table: str, activity_ids: set[int]) -> dict[int, int]:
    if not activity_ids or not _table_exists(conn, table):
        return {}
    placeholders = ", ".join(["%s"] * len(activity_ids))
    cur = conn.cursor()
    cur.execute(
        f'SELECT activity_id, COUNT(*) FROM public."{table}" '
        f'WHERE activity_id IN ({placeholders}) GROUP BY activity_id',
        sorted(activity_ids),
    )
    return {int(row[0]): int(row[1]) for row in cur.fetchall()}


def _child_manifest(conn, table: str, activity_ids: set[int]) -> dict[int, dict]:
    """Count and fingerprint child rows for pending activities only."""
    if not activity_ids or not _table_exists(conn, table):
        return {}
    available = {name for name, _ in _column_info(conn, table)}
    order_columns = [
        name for name in CHILD_ORDER_CANDIDATES[table] if name in available
    ]
    order_sql = ", ".join(f't."{name}"' for name in order_columns)
    if not order_sql:
        order_sql = "t.activity_id"
    row_expr = "to_jsonb(t)"
    for ignored in CHILD_HASH_IGNORED_COLUMNS.get(table, ()):
        if ignored in available:
            row_expr += f" - '{ignored}'"
    placeholders = ", ".join(["%s"] * len(activity_ids))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT activity_id, COUNT(*),
               md5(jsonb_agg({row_expr} ORDER BY {order_sql})::text)
        FROM public."{table}" t
        WHERE activity_id IN ({placeholders})
        GROUP BY activity_id
        """,
        sorted(activity_ids),
    )
    return {
        int(row[0]): {"count": int(row[1]), "hash": row[2]}
        for row in cur.fetchall()
    }


def _column_info(conn, table: str) -> list[tuple[str, str]]:
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, [table])
    return [(row[0], row[1]) for row in cur.fetchall()]


def _common_columns(src, dst, table: str) -> list[tuple[str, str]]:
    src_cols = _column_info(src, table)
    dst_names = {name for name, _ in _column_info(dst, table)}
    return [(name, data_type) for name, data_type in src_cols if name in dst_names]


def _transfer_columns(src, dst, table: str) -> list[tuple[str, str]]:
    columns = _common_columns(src, dst, table)
    if table == "activity_splits":
        # Split ids are local sequence values and can legitimately diverge
        # between Neon and the self-hosted DB. The stable key is the activity
        # plus split index/type, so let the destination allocate its own id.
        return [(name, data_type) for name, data_type in columns if name != "id"]
    return columns


def _coerce_row(row: tuple, columns: list[tuple[str, str]]) -> tuple:
    values = []
    for value, (_, data_type) in zip(row, columns):
        if value is not None and isinstance(value, (dict, list)) and data_type in ("json", "jsonb"):
            value = json.dumps(value, ensure_ascii=False)
        values.append(value)
    return tuple(values)


def _copy_activity(src, dst, activity_id: int, dry_run: bool) -> int:
    columns = _common_columns(src, dst, "activities")
    names = [name for name, _ in columns]
    cols_sql = ", ".join(f'"{name}"' for name in names)
    src_cur = src.cursor()
    src_cur.execute(f"SELECT {cols_sql} FROM activities WHERE id = %s", [activity_id])
    row = src_cur.fetchone()
    if not row:
        return 0
    if dry_run:
        return 1

    placeholders = ", ".join(
        "CAST(%s AS JSONB)" if data_type in ("json", "jsonb") else "%s"
        for _, data_type in columns
    )
    updates = ", ".join(f'"{name}" = EXCLUDED."{name}"' for name in names if name != "id")
    dst_cur = dst.cursor()
    dst_cur.execute(
        f'INSERT INTO activities ({cols_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT (id) DO UPDATE SET {updates}',
        _coerce_row(row, columns),
    )
    dst.commit()
    return 1


def _copy_activity_component(
    src, dst, activity_id: int, requested_columns: tuple[str, ...], dry_run: bool
) -> int:
    if dry_run:
        return 1
    src_types = dict(_column_info(src, "activities"))
    dst_names = {name for name, _ in _column_info(dst, "activities")}
    columns = [
        (name, src_types[name])
        for name in requested_columns
        if name in src_types and name in dst_names
    ]
    if not columns:
        return 0
    names = [name for name, _ in columns]
    cols_sql = ", ".join(f'"{name}"' for name in names)
    src_cur = src.cursor()
    src_cur.execute(
        f"SELECT {cols_sql} FROM activities WHERE id = %s", [activity_id]
    )
    row = src_cur.fetchone()
    if not row:
        return 0
    assignments = ", ".join(
        f'"{name}" = CAST(%s AS JSONB)'
        if data_type in ("json", "jsonb")
        else f'"{name}" = %s'
        for name, data_type in columns
    )
    dst_cur = dst.cursor()
    dst_cur.execute(
        f"UPDATE activities SET {assignments} WHERE id = %s",
        [*_coerce_row(row, columns), activity_id],
    )
    dst.commit()
    return 1


def _replace_child_rows(
    src, dst, table: str, activity_id: int, dry_run: bool, expected_count: int
) -> int:
    if dry_run:
        return expected_count
    columns = _transfer_columns(src, dst, table)
    names = [name for name, _ in columns]
    cols_sql = ", ".join(f'"{name}"' for name in names)
    src_cur = src.cursor()
    src_cur.execute(
        f'SELECT {cols_sql} FROM public."{table}" WHERE activity_id = %s',
        [activity_id],
    )
    rows = src_cur.fetchall()
    dst_cur = dst.cursor()
    dst_cur.execute(f'DELETE FROM public."{table}" WHERE activity_id = %s', [activity_id])
    if rows:
        placeholders = ", ".join(
            "CAST(%s AS JSONB)" if data_type in ("json", "jsonb") else "%s"
            for _, data_type in columns
        )
        insert_sql = f'INSERT INTO public."{table}" ({cols_sql}) VALUES ({placeholders})'
        dst_cur.executemany(insert_sql, [_coerce_row(row, columns) for row in rows])
    dst.commit()
    return len(rows)


def _tombstones(conn) -> dict[tuple[str, str], object]:
    if not _table_exists(conn, "sync_tombstones"):
        return {}
    cur = conn.cursor()
    cur.execute("SELECT entity_type, entity_id, deleted_at FROM sync_tombstones")
    return {(str(row[0]), str(row[1])): row[2] for row in cur.fetchall()}


def _timestamp_key(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    return str(value)


def _plan_small_table_actions(
    neon_state: dict[object, object], local_state: dict[object, object]
) -> list[tuple[str, str, object]]:
    """Plan row-level copies for one small table without touching a database.

    Each state maps primary key to updated_at. Missing rows are copied to the
    other side. For rows present on both sides, only a strictly newer timestamp
    wins; equal or absent timestamps never trigger an arbitrary overwrite.
    """
    actions: list[tuple[str, str, object]] = []
    for key in sorted(set(neon_state) | set(local_state), key=str):
        if key not in neon_state:
            actions.append(("local", "neon", key))
            continue
        if key not in local_state:
            actions.append(("neon", "local", key))
            continue
        neon_version = _timestamp_key(neon_state[key])
        local_version = _timestamp_key(local_state[key])
        if neon_version > local_version:
            actions.append(("neon", "local", key))
        elif local_version > neon_version:
            actions.append(("local", "neon", key))
    return actions


def _small_table_manifest(conn, table: str, primary_key: str) -> dict[object, object]:
    if not _table_exists(conn, table):
        return {}
    updated_expr = '"updated_at"' if _column_exists(conn, table, "updated_at") else "NULL"
    cur = conn.cursor()
    cur.execute(
        f'SELECT "{primary_key}", {updated_expr} FROM public."{table}"'
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _copy_small_table_row(
    src, dst, table: str, primary_key: str, key, dry_run: bool
) -> int:
    if dry_run:
        return 1
    columns = _common_columns(src, dst, table)
    names = [name for name, _ in columns]
    if primary_key not in names:
        raise RuntimeError(f"{table}: primary key {primary_key} is not transferable")
    cols_sql = ", ".join(f'"{name}"' for name in names)
    src_cur = src.cursor()
    src_cur.execute(
        f'SELECT {cols_sql} FROM public."{table}" WHERE "{primary_key}" = %s',
        [key],
    )
    row = src_cur.fetchone()
    if not row:
        return 0
    placeholders = ", ".join(
        "CAST(%s AS JSONB)" if data_type in ("json", "jsonb") else "%s"
        for _, data_type in columns
    )
    updates = ", ".join(
        f'"{name}" = EXCLUDED."{name}"' for name in names if name != primary_key
    )
    conflict_sql = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    dst_cur = dst.cursor()
    dst_cur.execute(
        f'INSERT INTO public."{table}" ({cols_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT ("{primary_key}") {conflict_sql}',
        _coerce_row(row, columns),
    )
    dst.commit()
    return 1


def _sync_small_tables(neon, local, dry_run: bool) -> int:
    copied = 0
    for table, primary_key in SMALL_TABLES.items():
        neon_state = _small_table_manifest(neon, table, primary_key)
        local_state = _small_table_manifest(local, table, primary_key)
        for source, destination, key in _plan_small_table_actions(
            neon_state, local_state
        ):
            src, dst = (neon, local) if source == "neon" else (local, neon)
            print(
                f"[incremental-sync] {table}[{key!r}]: "
                f"{source} -> {destination}",
                file=sys.stderr,
            )
            copied += _copy_small_table_row(
                src, dst, table, primary_key, key, dry_run
            )
    return copied


def _newer_run_metrics_side(neon_entry: dict, local_entry: dict) -> str | None:
    neon_value = _timestamp_key(neon_entry.get("run_metrics_updated_at"))
    local_value = _timestamp_key(local_entry.get("run_metrics_updated_at"))
    if neon_value == local_value:
        return None
    return "neon" if neon_value > local_value else "local"


def _newer_component_side(
    neon_entry: dict, local_entry: dict, marker: str
) -> str | None:
    neon_value = _timestamp_key(neon_entry.get(marker))
    local_value = _timestamp_key(local_entry.get(marker))
    if neon_value == local_value:
        return None
    return "neon" if neon_value > local_value else "local"


def _choose_child_source(
    neon_entry: dict | None,
    local_entry: dict | None,
    neon_marker=None,
    local_marker=None,
) -> str | None:
    """Choose one child-table source without coupling unrelated components.

    A component timestamp has priority. Legacy rows without markers fall back
    to the richer row count. Equal-count content divergence is resolved from
    Neon, the contractually complete side, instead of being marked converged.
    """
    neon_version = _timestamp_key(neon_marker)
    local_version = _timestamp_key(local_marker)
    if neon_version != local_version:
        return "neon" if neon_version > local_version else "local"

    neon_entry = neon_entry or {"count": 0, "hash": None}
    local_entry = local_entry or {"count": 0, "hash": None}
    neon_count = int(neon_entry.get("count") or 0)
    local_count = int(local_entry.get("count") or 0)
    if neon_count != local_count:
        return "neon" if neon_count > local_count else "local"
    if neon_entry.get("hash") != local_entry.get("hash"):
        return "neon"
    return None


def _apply_tombstone(conn, key: tuple[str, str], deleted_at, dry_run: bool) -> None:
    if dry_run:
        return
    entity_type, entity_id = key
    cur = conn.cursor()
    if entity_type == "activity":
        for table in CHILD_TABLES:
            if _table_exists(conn, table):
                cur.execute(f'DELETE FROM public."{table}" WHERE activity_id = %s', [int(entity_id)])
        cur.execute("DELETE FROM activities WHERE id = %s", [int(entity_id)])
    cur.execute("""
        INSERT INTO sync_tombstones (entity_type, entity_id, deleted_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (entity_type, entity_id) DO UPDATE
        SET deleted_at = GREATEST(sync_tombstones.deleted_at, EXCLUDED.deleted_at)
    """, [entity_type, entity_id, deleted_at])
    conn.commit()


def _sync_tombstones(neon, local, dry_run: bool) -> int:
    neon_marks = _tombstones(neon)
    local_marks = _tombstones(local)
    actions = 0
    for key in set(neon_marks) | set(local_marks):
        values = [v for v in (neon_marks.get(key), local_marks.get(key)) if v is not None]
        latest = max(values, key=_timestamp_key)
        if key not in neon_marks or _timestamp_key(neon_marks[key]) < _timestamp_key(latest):
            _apply_tombstone(neon, key, latest, dry_run)
            actions += 1
        if key not in local_marks or _timestamp_key(local_marks[key]) < _timestamp_key(latest):
            _apply_tombstone(local, key, latest, dry_run)
            actions += 1
    return actions


def _mark_details_complete(conn, activity_id: int, value, dry_run: bool) -> None:
    if value is None or dry_run:
        return
    cur = conn.cursor()
    cur.execute("""
        UPDATE activities
        SET details_fetched_at = COALESCE(details_fetched_at, %s)
        WHERE id = %s
    """, [value, activity_id])
    conn.commit()


def _mark_sync_complete(conn, activity_ids: list[int], dry_run: bool) -> int:
    if not activity_ids:
        return 0
    if dry_run:
        return len(activity_ids)
    placeholders = ", ".join(["%s"] * len(activity_ids))
    cur = conn.cursor()
    cur.execute(f"""
        UPDATE activities
        SET sync_complete_at = NOW(), sync_status = 'ok'
        WHERE id IN ({placeholders}) AND details_fetched_at IS NOT NULL
    """, activity_ids)
    conn.commit()
    return cur.rowcount or 0


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    prepare_only = "--prepare-only" in sys.argv[1:]
    neon_url = os.environ.get("DATABASE_URL_NEON") or os.environ.get("DATABASE_URL")
    local_url = os.environ.get("LOCAL_DATABASE_URL")
    if not neon_url or not local_url:
        print("[incremental-sync] DATABASE_URL_NEON and LOCAL_DATABASE_URL are required", file=sys.stderr)
        return 1
    if neon_url == local_url:
        print("[incremental-sync] Neon and local URLs are identical; nothing to do", file=sys.stderr)
        return 0

    started = time.time()
    neon = _connect(neon_url, "Neon")
    local = _connect(local_url, "local")
    try:
        _ensure_support(neon, "Neon", dry_run)
        _ensure_support(local, "local", dry_run)
        if prepare_only:
            print("[incremental-sync] support columns and indexes are ready", file=sys.stderr)
            return 0
        tombstone_actions = _sync_tombstones(neon, local, dry_run)
        small_table_actions = _sync_small_tables(neon, local, dry_run)

        pending_ids = _pending_activity_ids(neon) | _pending_activity_ids(local)
        neon_manifest = _activity_manifest(neon, pending_ids)
        local_manifest = _activity_manifest(local, pending_ids)
        neon_children = {
            table: _child_manifest(neon, table, pending_ids) for table in CHILD_TABLES
        }
        local_children = {
            table: _child_manifest(local, table, pending_ids) for table in CHILD_TABLES
        }

        copied_activities = 0
        copied_components = 0
        copied_rows = 0
        actions = 0
        complete_ids: list[int] = []
        for activity_id in sorted(pending_ids):
            in_neon = activity_id in neon_manifest
            in_local = activity_id in local_manifest
            if in_neon and not in_local:
                print(f"[incremental-sync] activity {activity_id}: Neon -> local (missing)")
                copied_activities += _copy_activity(neon, local, activity_id, dry_run)
                local_manifest[activity_id] = dict(neon_manifest[activity_id])
                in_local = True
                actions += 1
            elif in_local and not in_neon:
                print(f"[incremental-sync] activity {activity_id}: local -> Neon (missing)")
                copied_activities += _copy_activity(local, neon, activity_id, dry_run)
                neon_manifest[activity_id] = dict(local_manifest[activity_id])
                in_neon = True
                actions += 1

            if in_neon and in_local:
                for component, config in ACTIVITY_COMPONENTS.items():
                    marker = config["marker"]
                    source_side = _newer_component_side(
                        neon_manifest[activity_id], local_manifest[activity_id], marker
                    )
                    if not source_side:
                        continue
                    src, dst = (
                        (neon, local) if source_side == "neon" else (local, neon)
                    )
                    destination_side = "local" if source_side == "neon" else "neon"
                    print(
                        f"[incremental-sync] activity {activity_id} {component}: "
                        f"{source_side} -> {destination_side}",
                        file=sys.stderr,
                    )
                    copied_components += _copy_activity_component(
                        src, dst, activity_id, config["columns"], dry_run
                    )
                    destination_manifest = (
                        local_manifest if source_side == "neon" else neon_manifest
                    )
                    source_manifest = (
                        neon_manifest if source_side == "neon" else local_manifest
                    )
                    destination_manifest[activity_id][marker] = source_manifest[
                        activity_id
                    ][marker]
                    actions += 1

            converged = in_neon and in_local
            child_marker_sources = {
                marker: _newer_component_side(
                    neon_manifest[activity_id], local_manifest[activity_id], marker
                )
                for marker in set(CHILD_COMPONENT_MARKERS.values())
            }
            for table in CHILD_TABLES:
                marker = CHILD_COMPONENT_MARKERS[table]
                neon_entry = neon_children[table].get(activity_id)
                local_entry = local_children[table].get(activity_id)
                source_side = _choose_child_source(
                    neon_entry,
                    local_entry,
                    neon_manifest[activity_id].get(marker),
                    local_manifest[activity_id].get(marker),
                )
                if source_side:
                    src, dst = (
                        (neon, local) if source_side == "neon" else (local, neon)
                    )
                    source_children = (
                        neon_children if source_side == "neon" else local_children
                    )
                    destination_children = (
                        local_children if source_side == "neon" else neon_children
                    )
                    source_entry = source_children[table].get(
                        activity_id, {"count": 0, "hash": None}
                    )
                    destination_side = "local" if source_side == "neon" else "Neon"
                    print(
                        f"[incremental-sync] activity {activity_id} {table}: "
                        f"{source_side} -> {destination_side} "
                        f"(rows={source_entry['count']})",
                        file=sys.stderr,
                    )
                    copied_rows += _replace_child_rows(
                        src,
                        dst,
                        table,
                        activity_id,
                        dry_run,
                        int(source_entry["count"]),
                    )
                    destination_children[table][activity_id] = dict(source_entry)
                    actions += 1
                converged = converged and (
                    neon_children[table].get(activity_id)
                    == local_children[table].get(activity_id)
                )

            for marker, source_side in child_marker_sources.items():
                if not source_side:
                    continue
                src, dst = (
                    (neon, local) if source_side == "neon" else (local, neon)
                )
                _copy_activity_component(
                    src, dst, activity_id, (marker,), dry_run
                )
                destination_manifest = (
                    local_manifest if source_side == "neon" else neon_manifest
                )
                source_manifest = (
                    neon_manifest if source_side == "neon" else local_manifest
                )
                destination_manifest[activity_id][marker] = source_manifest[
                    activity_id
                ].get(marker)

            neon_details = neon_manifest[activity_id]["details_fetched_at"]
            local_details = local_manifest[activity_id]["details_fetched_at"]
            if neon_details and not local_details:
                _mark_details_complete(local, activity_id, neon_details, dry_run)
                local_details = neon_details
                actions += 1
            elif local_details and not neon_details:
                _mark_details_complete(neon, activity_id, local_details, dry_run)
                neon_details = local_details
                actions += 1

            if converged and neon_details and local_details:
                complete_ids.append(activity_id)

        marked_ok = _mark_sync_complete(neon, complete_ids, dry_run)
        marked_ok += _mark_sync_complete(local, complete_ids, dry_run)
        elapsed = time.time() - started
        mode = "dry-run" if dry_run else "applied"
        print(
            f"[incremental-sync] {mode}: pending={len(pending_ids)}, actions={actions}, "
            f"activities={copied_activities}, components={copied_components}, "
            f"child_rows={copied_rows}, "
            f"small_rows={small_table_actions}, marked_ok={marked_ok}, "
            f"tombstones={tombstone_actions}, elapsed={elapsed:.1f}s",
            file=sys.stderr,
        )
        return 0
    finally:
        try:
            neon.close()
        except Exception:
            pass
        try:
            local.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
