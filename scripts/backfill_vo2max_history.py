#!/usr/bin/env python3
"""
Backfill Garmin VO2max history directly into Neon.

Goals:
- write only to the target PostgreSQL database (typically Neon)
- avoid changing the normal app sync flow
- progress day by day with a local resume cursor
- be safe to rerun thanks to date-based upserts

Examples:
    python scripts/backfill_vo2max_history.py
    python scripts/backfill_vo2max_history.py --start-date 2021-01-01
    python scripts/backfill_vo2max_history.py --max-days 60
    python scripts/backfill_vo2max_history.py --reset-state
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
    load_state,
    parse_day,
    parse_dateish,
    resolve_target_database_url,
    sanitize_target_host,
    save_state,
)

DEFAULT_STATE_PATH = PROJECT_ROOT / ".runtime" / "backfill_vo2max_state.json"


def extract_vo2max(metrics: Any) -> float | None:
    """Extract VO2max from Garmin get_max_metrics(day)."""
    if not metrics:
        return None

    item = metrics[0] if isinstance(metrics, list) and metrics else metrics
    if not isinstance(item, dict):
        return None

    generic = item.get("generic") if isinstance(item.get("generic"), dict) else {}
    for container in (generic, item):
        for key in ("vo2MaxPreciseValue", "vo2MaxValue"):
            raw = container.get(key) if isinstance(container, dict) else None
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill Garmin VO2max history into Neon, day by day."
    )
    parser.add_argument(
        "--env",
        default=str(DEFAULT_ENV_PATH),
        help="Path to the .env file used to resolve DATABASE_URL/DATABASE_URL_NEON.",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Target PostgreSQL URL. Overrides env and .env lookup.",
    )
    parser.add_argument(
        "--token-dir",
        default=str(DEFAULT_TOKEN_DIR),
        help="Garmin token directory. Falls back to sync_meta if empty or missing.",
    )
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_PATH),
        help="Local resume cursor file.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete the current state file and restart from the requested range.",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Start date (YYYY-MM-DD). Default: oldest run date found in target DB.",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="End date inclusive (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="Max Garmin day-queries to perform in this run. 0 = no limit.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.35,
        help="Delay between Garmin day-queries.",
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=3,
        help="Retries for transient non-rate-limit errors.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Base backoff for transient errors.",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Query Garmin even when the target day already exists in vo2max_history.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    token_dir = str(Path(args.token_dir).expanduser())

    target_url = resolve_target_database_url(env_path, args.database_url)
    if not target_url:
        print(
            "Target database URL not found. Provide --database-url or configure "
            "DATABASE_URL/DATABASE_URL_NEON.",
            file=sys.stderr,
        )
        return 1

    # Force the DB shim onto the direct target PostgreSQL database only.
    os.environ["DATABASE_URL"] = target_url
    os.environ.pop("DATABASE_URL_NEON", None)
    os.environ.pop("LOCAL_DATABASE_URL", None)
    os.environ.pop("SQLITE_PATH", None)

    sys.path.insert(0, str(PROJECT_ROOT))

    import db
    import garmin_freshness

    if args.reset_state and state_path.exists():
        state_path.unlink()
        print(f"[vo2-backfill] deleted state file: {state_path}")

    target_host = sanitize_target_host(target_url)
    print(f"[vo2-backfill] target DB -> {target_host}", file=sys.stderr)

    state = load_state(state_path)
    state_start = parse_dateish(state.get("start_date")) if state else None
    state_end = parse_dateish(state.get("end_date")) if state else None

    if args.start_date:
        start_day = parse_day(args.start_date, "--start-date")
    elif state_start is not None:
        start_day = state_start
    else:
        oldest_activity = db.get_oldest_activity_date()
        start_day = parse_dateish(oldest_activity)
        if start_day is None:
            print(
                "Could not infer a start date from activities. Pass --start-date YYYY-MM-DD.",
                file=sys.stderr,
            )
            return 1

    if args.end_date:
        end_day = parse_day(args.end_date, "--end-date")
    elif state_end is not None:
        end_day = state_end
    else:
        end_day = date.today()

    if start_day > end_day:
        print(
            f"Invalid range: start {start_day.isoformat()} is after end {end_day.isoformat()}",
            file=sys.stderr,
        )
        return 1

    counters = {
        "stored_points": 0,
        "missing_days": 0,
        "skipped_existing": 0,
        "garmin_queries": 0,
    }

    def snapshot(next_iso: str, completed: bool) -> dict[str, Any]:
        return {
            "metric": "vo2max",
            "target_host": target_host,
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "next_date": next_iso,
            "completed": completed,
            **counters,
        }

    if state:
        if state.get("target_host") != target_host:
            print(
                "State file target DB differs from the current target. "
                "Use --reset-state to restart safely.",
                file=sys.stderr,
            )
            return 1
        if state_start != start_day or state_end != end_day:
            print(
                "State file range differs from the requested range. "
                "Use --reset-state to restart safely.",
                file=sys.stderr,
            )
            return 1
        counters["stored_points"] = int(state.get("stored_points", 0))
        counters["missing_days"] = int(state.get("missing_days", 0))
        counters["skipped_existing"] = int(state.get("skipped_existing", 0))
        counters["garmin_queries"] = int(state.get("garmin_queries", 0))
        if state.get("completed"):
            print(
                f"[vo2-backfill] already completed for {start_day} -> {end_day}. "
                f"Use --reset-state to rerun.",
                file=sys.stderr,
            )
            return 0
        next_day = parse_dateish(state.get("next_date")) or start_day
        print(
            f"[vo2-backfill] resuming at {next_day.isoformat()} "
            f"(stored={counters['stored_points']} missing={counters['missing_days']})",
            file=sys.stderr,
        )
    else:
        next_day = start_day
        save_state(state_path, snapshot(next_day.isoformat(), False))

    history = db.get_vo2max_history()
    existing_dates = {
        str(row.get("date"))
        for row in history
        if parse_dateish(row.get("date")) and start_day <= parse_dateish(row.get("date")) <= end_day
    }

    api = garmin_freshness.load_garmin_api(token_dir)
    if api is None:
        print(
            "[vo2-backfill] unable to load Garmin tokens. "
            "Try scripts/garmin_setup.py or scripts/garmin_push_neon.py first.",
            file=sys.stderr,
        )
        return 1

    run_queries = 0
    current = next_day
    while current <= end_day:
        if args.max_days > 0 and run_queries >= args.max_days:
            print(
                f"[vo2-backfill] reached --max-days={args.max_days}; state saved at {current.isoformat()}",
                file=sys.stderr,
            )
            break

        day_iso = current.isoformat()
        if not args.refresh_existing and day_iso in existing_dates:
            counters["skipped_existing"] += 1
            current += timedelta(days=1)
            save_state(state_path, snapshot(current.isoformat(), False))
            continue

        metrics = None
        for attempt in range(1, max(1, args.retry_count) + 1):
            try:
                metrics = api.get_max_metrics(day_iso)
                break
            except Exception as exc:
                if is_rate_limited_error(exc):
                    garmin_freshness._save_api_tokens(api, token_dir)
                    save_state(state_path, snapshot(current.isoformat(), False))
                    print(
                        f"[vo2-backfill] Garmin rate limit around {day_iso}. "
                        f"State saved to {state_path}. Retry later.",
                        file=sys.stderr,
                    )
                    return 75
                if attempt >= max(1, args.retry_count):
                    garmin_freshness._save_api_tokens(api, token_dir)
                    print(
                        f"[vo2-backfill] Garmin query failed on {day_iso}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                sleep_for = args.retry_backoff_seconds * attempt
                print(
                    f"[vo2-backfill] transient Garmin error on {day_iso} "
                    f"(attempt {attempt}/{args.retry_count}): {type(exc).__name__}: {exc} "
                    f"-> retrying in {sleep_for:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_for)

        run_queries += 1
        counters["garmin_queries"] += 1

        vo2 = extract_vo2max(metrics)
        if vo2 is None:
            counters["missing_days"] += 1
            print(f"[vo2-backfill] {day_iso}: no VO2max", file=sys.stderr)
        else:
            db.upsert_vo2max(day_iso, vo2)
            existing_dates.add(day_iso)
            counters["stored_points"] += 1
            print(f"[vo2-backfill] {day_iso}: VO2max={vo2}", file=sys.stderr)

        current += timedelta(days=1)
        if counters["garmin_queries"] % 25 == 0:
            garmin_freshness._save_api_tokens(api, token_dir)

        completed = current > end_day
        save_state(state_path, snapshot(current.isoformat(), completed))

        if not completed and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    garmin_freshness._save_api_tokens(api, token_dir)

    if current > end_day:
        print(
            "[vo2-backfill] complete "
            f"stored={counters['stored_points']} "
            f"missing={counters['missing_days']} "
            f"skipped_existing={counters['skipped_existing']} "
            f"garmin_queries={counters['garmin_queries']}",
            file=sys.stderr,
        )
        print(
            "[vo2-backfill] Neon is now ready. To copy this metric to local Postgres "
            "without touching Garmin again, run:",
            file=sys.stderr,
        )
        print(
            "MIRROR_TABLES=vo2max_history python scripts/mirror_neon_to_local.py",
            file=sys.stderr,
        )
    else:
        print(
            "[vo2-backfill] paused before completion. Rerun the same command to resume.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    ensure_project_python()
    raise SystemExit(main())
