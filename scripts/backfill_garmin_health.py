#!/usr/bin/env python3
"""Backfill Garmin health/fatigue snapshots onto run activities.

Each run gets the Garmin wellness state available at its end time:
- sleep score, quality, duration and sleep window
- overnight HRV average/status/baseline
- resting heart rate and 7-day average

By default the script updates both LOCAL_DATABASE_URL and DATABASE_URL_NEON when
they are configured. It is safe to rerun: existing rows are skipped unless
--refresh-existing is passed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backfill_common import (
    DEFAULT_ENV_PATH,
    DEFAULT_TOKEN_DIR,
    PROJECT_ROOT,
    ensure_project_python,
    is_rate_limited_error,
    load_env_var,
    parse_day,
    sanitize_target_host,
)


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class RateLimitedError(RuntimeError):
    pass


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_or_file(env_path: Path, key: str) -> str:
    return os.environ.get(key) or load_env_var(env_path, key)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach Garmin sleep/HRV/resting-HR snapshots to runs."
    )
    parser.add_argument(
        "--env",
        default=str(DEFAULT_ENV_PATH),
        help="Path to .env used for DATABASE_URL_NEON/LOCAL_DATABASE_URL.",
    )
    parser.add_argument(
        "--target",
        choices=("both", "local", "neon", "primary"),
        default="both",
        help="Which configured database(s) to update. Ignored by --database-url.",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Custom PostgreSQL URL to update instead of configured targets.",
    )
    parser.add_argument(
        "--also-database-url",
        action="append",
        default=[],
        help="Additional PostgreSQL URL(s) to update after --database-url.",
    )
    parser.add_argument(
        "--token-dir",
        default=str(DEFAULT_TOKEN_DIR),
        help="Garmin token directory. Falls back to sync_meta when needed.",
    )
    parser.add_argument("--activity-id", action="append", type=int, default=[])
    parser.add_argument("--start-date", default="", help="Run start date >= YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="Run start date <= YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=0, help="Max runs per target. 0 = no limit.")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Recompute runs even when run_health_updated_at is already set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and count, but do not update database rows.",
    )
    parser.add_argument(
        "--mark-missing",
        action="store_true",
        help="Set run_health_updated_at even when Garmin returns no usable health data.",
    )
    parser.add_argument(
        "--mark-synced",
        action="store_true",
        help="After a direct write, mark the touched run sync_status=ok.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.15,
        help="Delay between processed runs.",
    )
    return parser


def _dedupe_targets(targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, url in targets:
        if not url or url in seen:
            continue
        seen.add(url)
        out.append((label, url))
    return out


def _resolve_targets(args: argparse.Namespace, env_path: Path) -> list[tuple[str, str]]:
    if args.database_url:
        return _dedupe_targets(
            [("custom", args.database_url)]
            + [(f"extra-{index}", url) for index, url in enumerate(args.also_database_url, start=1)]
        )

    local_url = _env_or_file(env_path, "LOCAL_DATABASE_URL")
    primary_url = _env_or_file(env_path, "DATABASE_URL")
    neon_url = _env_or_file(env_path, "DATABASE_URL_NEON")
    if not neon_url and primary_url and primary_url != local_url:
        neon_url = primary_url

    if args.target == "local":
        targets = [("local", local_url)]
    elif args.target == "neon":
        targets = [("neon", neon_url)]
    elif args.target == "primary":
        targets = [("primary", primary_url)]
    else:
        targets = [("local", local_url), ("neon", neon_url)]
        if not local_url and primary_url:
            targets.append(("primary", primary_url))

    return _dedupe_targets(targets)


def _connect(dbpg: Any, url: str, label: str):
    import pg8000.dbapi

    print(f"[health-backfill] connecting to {label} ({sanitize_target_host(url)})", file=sys.stderr)
    return pg8000.dbapi.connect(**dbpg._parse_db_url(url))


def _run_filters(args: argparse.Namespace) -> tuple[list[str], list[Any]]:
    clauses = ["type = 'Run'"]
    params: list[Any] = []
    if not args.refresh_existing:
        clauses.append("run_health_updated_at IS NULL")
    if args.activity_id:
        placeholders = ", ".join(["%s"] * len(args.activity_id))
        clauses.append(f"id IN ({placeholders})")
        params.extend(args.activity_id)
    if args.start_date:
        clauses.append("start_date_local >= %s")
        params.append(parse_day(args.start_date, "--start-date").isoformat())
    if args.end_date:
        end_exclusive = parse_day(args.end_date, "--end-date") + timedelta(days=1)
        clauses.append("start_date_local < %s")
        params.append(end_exclusive.isoformat())
    return clauses, params


def _runs_to_process(conn: Any, dbpg: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    added = dbpg.ensure_run_metric_schema(conn)
    if added:
        print(f"[health-backfill] schema columns added {added}", file=sys.stderr)
    conn.commit()

    clauses, params = _run_filters(args)
    limit_sql = " LIMIT %s" if args.limit > 0 else ""
    if args.limit > 0:
        params.append(args.limit)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, start_date_local, moving_time, elapsed_time, garmin_summary
        FROM activities
        WHERE {" AND ".join(clauses)}
        ORDER BY start_date_local ASC
        {limit_sql}
        """,
        params,
    )
    rows = cur.fetchall()
    return [
        {
            "id": int(row[0]),
            "start_date_local": dbpg._garmin_summary_start_local(row[4], row[1]),
            "moving_time": int(row[2] or 0),
            "elapsed_time": int(row[3] or 0),
        }
        for row in rows
    ]


def _apply_snapshot(
    conn: Any,
    dbpg: Any,
    activity_id: int,
    snapshot: dict[str, Any],
    dry_run: bool,
    mark_missing: bool,
    mark_synced: bool,
    version: str,
) -> bool:
    if not dbpg._has_health_values(snapshot) and not mark_missing:
        return False
    if dry_run:
        return True
    cur = conn.cursor()
    cur.execute(
        dbpg._ACTIVITY_HEALTH_UPDATE_SQL,
        dbpg._activity_health_row(activity_id, snapshot, version),
    )
    changed = bool(cur.rowcount and cur.rowcount > 0)
    if changed and mark_synced:
        cur.execute(
            """
            UPDATE activities
            SET sync_complete_at = CAST(%s AS TIMESTAMPTZ),
                sync_status = 'ok'
            WHERE id = %s
            """,
            [version, activity_id],
        )
    conn.commit()
    return changed


def main() -> int:
    args = _parser().parse_args()
    env_path = Path(args.env).expanduser().resolve()
    _load_env(env_path)
    token_dir = str(Path(args.token_dir).expanduser())

    import database_pg as dbpg
    import garmin_freshness
    from garmin_health import fetch_run_health_snapshot

    targets = _resolve_targets(args, env_path)
    if not targets:
        print(
            "[health-backfill] no target DB found. Configure LOCAL_DATABASE_URL/"
            "DATABASE_URL_NEON or pass --database-url.",
            file=sys.stderr,
        )
        return 1

    api = garmin_freshness.load_garmin_api(token_dir)
    if api is None:
        print(
            "[health-backfill] unable to load Garmin tokens. "
            "Try scripts/garmin_setup.py first.",
            file=sys.stderr,
        )
        return 1

    cache: dict[tuple[str, str], Any] = {}
    batch_version = dbpg._run_metrics_version()
    total_updated = 0
    total_marked_missing = 0
    total_missing = 0
    total_seen = 0

    def on_error(method: str, day: str, exc: Exception) -> None:
        if is_rate_limited_error(exc):
            raise RateLimitedError(f"{method}({day}) rate limited") from exc
        print(
            f"[health-backfill] Garmin {method}({day}) failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    for label, url in targets:
        conn = _connect(dbpg, url, label)
        try:
            runs = _runs_to_process(conn, dbpg, args)
            print(
                f"[health-backfill] {label}: selected {len(runs)} run(s)",
                file=sys.stderr,
            )
            updated = 0
            marked_missing = 0
            missing = 0
            for index, run in enumerate(runs, start=1):
                try:
                    snapshot = fetch_run_health_snapshot(
                        api, run, cache, on_error=on_error
                    )
                except RateLimitedError as exc:
                    garmin_freshness._save_api_tokens(api, token_dir)
                    print(
                        f"[health-backfill] {exc}. Stop here; rerun later to resume.",
                        file=sys.stderr,
                    )
                    return 75

                has_values = dbpg._has_health_values(snapshot)
                if _apply_snapshot(
                    conn,
                    dbpg,
                    int(run["id"]),
                    snapshot,
                    args.dry_run,
                    args.mark_missing,
                    args.mark_synced,
                    batch_version,
                ):
                    if has_values:
                        updated += 1
                        print(
                            f"[health-backfill] {label} {index}/{len(runs)} "
                            f"run={run['id']} sleep={snapshot.get('health_sleep_score', '-')} "
                            f"hrv={snapshot.get('health_hrv_last_night_avg_ms', '-')} "
                            f"rhr={snapshot.get('health_resting_hr_bpm', '-')}",
                            file=sys.stderr,
                        )
                    else:
                        marked_missing += 1
                        print(
                            f"[health-backfill] {label} {index}/{len(runs)} "
                            f"run={run['id']} marked without Garmin health data",
                            file=sys.stderr,
                        )
                else:
                    missing += 1
                    print(
                        f"[health-backfill] {label} {index}/{len(runs)} "
                        f"run={run['id']} no usable health data",
                        file=sys.stderr,
                    )
                if args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)

            total_updated += updated
            total_marked_missing += marked_missing
            total_missing += missing
            total_seen += len(runs)
            mode = "dry-run" if args.dry_run else "applied"
            print(
                f"[health-backfill] {label} {mode}: selected={len(runs)} "
                f"updated={updated} marked_missing={marked_missing} missing={missing}",
                file=sys.stderr,
            )
        finally:
            conn.close()

    garmin_freshness._save_api_tokens(api, token_dir)
    print(
        f"[health-backfill] complete targets={len(targets)} selected={total_seen} "
        f"updated={total_updated} marked_missing={total_marked_missing} "
        f"missing={total_missing} "
        f"garmin_cache_entries={len(cache)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    ensure_project_python()
    raise SystemExit(main())
