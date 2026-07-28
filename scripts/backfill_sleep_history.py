#!/usr/bin/env python3
"""
Backfill Garmin sleep scores into a target PostgreSQL database (Neon by default).

Mirrors the design of scripts/backfill_vo2max_history.py:
- write only to one target PostgreSQL database (typically Neon)
- never touch the normal app sync flow (replication is disabled here)
- progress day by day with a local resume cursor
- safe to rerun thanks to date-based upserts

After it finishes against Neon, copy the new table to local Postgres without
querying Garmin again:

    MIRROR_TABLES=sleep_history python scripts/mirror_neon_to_local.py

Or run it straight against the local DB instead of Neon:

    python scripts/backfill_sleep_history.py --database-url "$LOCAL_DATABASE_URL"

Examples:
    python scripts/backfill_sleep_history.py
    python scripts/backfill_sleep_history.py --start-date 2021-01-01
    python scripts/backfill_sleep_history.py --max-days 60
    python scripts/backfill_sleep_history.py --reset-state

By default, when no state file and no explicit --start-date are provided, the
script probes Garmin to find the first day that has a numeric sleep score and
starts there instead of crawling older duration-only nights one by one.
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

DEFAULT_STATE_PATH = PROJECT_ROOT / ".runtime" / "backfill_sleep_state.json"

METRIC = "sleep"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sleep_history (
    date TEXT PRIMARY KEY,
    sleep_score INTEGER,
    sleep_quality TEXT,
    sleep_duration_seconds INTEGER,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""

UPSERT_SQL = """
INSERT INTO sleep_history (date, sleep_score, sleep_quality, sleep_duration_seconds, updated_at)
VALUES (%s, %s, %s, %s, NOW())
ON CONFLICT (date) DO UPDATE SET
    sleep_score = EXCLUDED.sleep_score,
    sleep_quality = EXCLUDED.sleep_quality,
    sleep_duration_seconds = EXCLUDED.sleep_duration_seconds,
    updated_at = NOW()
"""


def extract_sleep(payload: Any) -> tuple[int | None, str | None, int | None]:
    """Extract (score, qualifier, duration_seconds) from Garmin get_sleep_data(day).

    The overall sleep score lives at dailySleepDTO.sleepScores.overall.value, but
    across Garmin API versions sleepScores has also appeared at the top level, so
    we look in both places defensively.
    """
    if not isinstance(payload, dict):
        return None, None, None

    dto = payload.get("dailySleepDTO")
    dto = dto if isinstance(dto, dict) else {}

    scores = dto.get("sleepScores")
    if not isinstance(scores, dict):
        scores = payload.get("sleepScores") if isinstance(payload.get("sleepScores"), dict) else {}

    overall = scores.get("overall") if isinstance(scores.get("overall"), dict) else {}

    score: int | None = None
    raw_score = overall.get("value")
    if raw_score is not None:
        try:
            score = int(round(float(raw_score)))
        except (TypeError, ValueError):
            score = None

    qualifier = overall.get("qualifierKey")
    if not isinstance(qualifier, str) or not qualifier.strip():
        qualifier = None

    duration: int | None = None
    raw_duration = dto.get("sleepTimeSeconds")
    if raw_duration is not None:
        try:
            duration = int(raw_duration)
        except (TypeError, ValueError):
            duration = None

    return score, qualifier, duration


def has_sleep_data(payload: Any) -> bool:
    """Whether Garmin returned a real sleep record even if no overall score exists."""
    if not isinstance(payload, dict):
        return False
    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        return False
    if dto.get("sleepTimeSeconds") is not None:
        return True
    return any(dto.get(key) is not None for key in (
        "deepSleepSeconds",
        "lightSleepSeconds",
        "remSleepSeconds",
        "awakeSleepSeconds",
        "sleepStartTimestampGMT",
        "sleepEndTimestampGMT",
    ))


def find_first_scored_sleep_date(
    api: Any,
    start_day: date,
    end_day: date,
    *,
    probe_step_days: int = 30,
) -> tuple[date | None, int]:
    """Find the earliest day with a numeric Garmin sleep score.

    Old accounts often expose sleep duration long before Garmin started
    returning an `overall` sleep score. Querying day by day from the first
    activity wastes a lot of time and looks broken. We probe in coarse steps,
    then refine within the first positive window.
    """
    queries = 0
    probe_step_days = max(1, int(probe_step_days))

    probe_day = start_day
    previous_probe = start_day
    first_positive_probe: date | None = None

    while probe_day <= end_day:
        queries += 1
        score, _, _ = extract_sleep(api.get_sleep_data(probe_day.isoformat()))
        if score is not None:
            first_positive_probe = probe_day
            break
        previous_probe = probe_day
        probe_day += timedelta(days=probe_step_days)

    if first_positive_probe is None:
        if previous_probe < end_day:
            queries += 1
            score, _, _ = extract_sleep(api.get_sleep_data(end_day.isoformat()))
            if score is not None:
                first_positive_probe = end_day
                previous_probe = max(start_day, end_day - timedelta(days=probe_step_days))
        if first_positive_probe is None:
            return None, queries

    refine_start = start_day if first_positive_probe == start_day else max(start_day, previous_probe)
    current = refine_start
    while current <= first_positive_probe:
        queries += 1
        score, _, _ = extract_sleep(api.get_sleep_data(current.isoformat()))
        if score is not None:
            return current, queries
        current += timedelta(days=1)

    return first_positive_probe, queries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill Garmin sleep scores into PostgreSQL, day by day."
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
        help="Start date (YYYY-MM-DD). Default: inferred first day with a Garmin sleep score.",
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
        help="Delay between Garmin day-queries (politeness, avoids rate limits).",
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
        help="Query Garmin even when the target day already exists in sleep_history.",
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

    # Force the DB shim onto the direct target PostgreSQL database only. Replication
    # to any secondary DB is intentionally disabled for a backfill.
    os.environ["DATABASE_URL"] = target_url
    os.environ.pop("DATABASE_URL_NEON", None)
    os.environ.pop("LOCAL_DATABASE_URL", None)
    os.environ.pop("SQLITE_PATH", None)

    sys.path.insert(0, str(PROJECT_ROOT))

    import database_pg as dbpg
    import garmin_freshness

    if args.reset_state and state_path.exists():
        state_path.unlink()
        print(f"[sleep-backfill] deleted state file: {state_path}")

    target_host = sanitize_target_host(target_url)
    print(f"[sleep-backfill] target DB -> {target_host}", file=sys.stderr)

    # Ensure the destination table exists before reading or writing.
    conn = dbpg._safe_conn()
    cur = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()

    state = load_state(state_path)
    state_start = parse_dateish(state.get("start_date")) if state else None
    state_end = parse_dateish(state.get("end_date")) if state else None

    if args.start_date:
        start_day = parse_day(args.start_date, "--start-date")
    elif state_start is not None:
        start_day = state_start
    else:
        oldest_activity = dbpg.get_oldest_activity_date()
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
            "metric": METRIC,
            "target_host": target_host,
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "next_date": next_iso,
            "completed": completed,
            **counters,
        }

    next_day: date | None = None

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
                f"[sleep-backfill] already completed for {start_day} -> {end_day}. "
                f"Use --reset-state to rerun.",
                file=sys.stderr,
            )
            return 0
        next_day = parse_dateish(state.get("next_date")) or start_day
        print(
            f"[sleep-backfill] resuming at {next_day.isoformat()} "
            f"(stored={counters['stored_points']} missing={counters['missing_days']})",
            file=sys.stderr,
        )
    api = garmin_freshness.load_garmin_api(token_dir)
    if api is None:
        print(
            "[sleep-backfill] unable to load Garmin tokens. "
            "Try scripts/garmin_setup.py or scripts/garmin_push_neon.py first.",
            file=sys.stderr,
        )
        return 1

    if next_day is None:
        next_day = start_day
        save_state(state_path, snapshot(next_day.isoformat(), False))

    if not args.start_date:
        probe_start = next_day
        original_start_day = start_day
        inferred_start, probe_queries = find_first_scored_sleep_date(api, probe_start, end_day)
        counters["garmin_queries"] += probe_queries
        if inferred_start is not None and inferred_start > probe_start:
            if state:
                print(
                    f"[sleep-backfill] next scored Garmin sleep found on {inferred_start.isoformat()} "
                    f"(resume point was {probe_start.isoformat()}); fast-forwarding past older unscored days.",
                    file=sys.stderr,
                )
                next_day = inferred_start
            else:
                start_day = inferred_start
                next_day = inferred_start
                print(
                    f"[sleep-backfill] first Garmin sleep score found on {start_day.isoformat()} "
                    f"(oldest activity: {original_start_day.isoformat()}); skipping older days without score.",
                    file=sys.stderr,
                )
            save_state(state_path, snapshot(next_day.isoformat(), False))
        elif inferred_start is None and not state:
            print(
                "[sleep-backfill] no Garmin sleep score found in the requested range; "
                "continuing from the original start date to preserve duration-only nights.",
                file=sys.stderr,
            )

    # Existing days already stored, to skip Garmin queries we don't need.
    existing_dates: set[str] = set()
    cur.execute(
        "SELECT date FROM sleep_history WHERE date >= %s AND date <= %s",
        [start_day.isoformat(), end_day.isoformat()],
    )
    for (row_date,) in cur.fetchall():
        parsed = parse_dateish(row_date)
        if parsed is not None:
            existing_dates.add(parsed.isoformat())

    run_queries = 0
    current = next_day
    while current <= end_day:
        if args.max_days > 0 and run_queries >= args.max_days:
            print(
                f"[sleep-backfill] reached --max-days={args.max_days}; state saved at {current.isoformat()}",
                file=sys.stderr,
            )
            break

        day_iso = current.isoformat()
        if not args.refresh_existing and day_iso in existing_dates:
            counters["skipped_existing"] += 1
            current += timedelta(days=1)
            save_state(state_path, snapshot(current.isoformat(), False))
            continue

        payload = None
        for attempt in range(1, max(1, args.retry_count) + 1):
            try:
                payload = api.get_sleep_data(day_iso)
                break
            except Exception as exc:
                if is_rate_limited_error(exc):
                    garmin_freshness._save_api_tokens(api, token_dir)
                    save_state(state_path, snapshot(current.isoformat(), False))
                    print(
                        f"[sleep-backfill] Garmin rate limit around {day_iso}. "
                        f"State saved to {state_path}. Retry later.",
                        file=sys.stderr,
                    )
                    return 75
                if attempt >= max(1, args.retry_count):
                    garmin_freshness._save_api_tokens(api, token_dir)
                    print(
                        f"[sleep-backfill] Garmin query failed on {day_iso}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                sleep_for = args.retry_backoff_seconds * attempt
                print(
                    f"[sleep-backfill] transient Garmin error on {day_iso} "
                    f"(attempt {attempt}/{args.retry_count}): {type(exc).__name__}: {exc} "
                    f"-> retrying in {sleep_for:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_for)

        run_queries += 1
        counters["garmin_queries"] += 1

        score, qualifier, duration = extract_sleep(payload)
        if score is None and duration is None:
            counters["missing_days"] += 1
            if has_sleep_data(payload):
                print(
                    f"[sleep-backfill] {day_iso}: sleep data present "
                    "but Garmin returned no overall sleep score or duration",
                    file=sys.stderr,
                )
            else:
                print(f"[sleep-backfill] {day_iso}: no sleep data", file=sys.stderr)
        else:
            cur.execute(UPSERT_SQL, [day_iso, score, qualifier, duration])
            conn.commit()
            existing_dates.add(day_iso)
            counters["stored_points"] += 1
            if score is None:
                print(
                    f"[sleep-backfill] {day_iso}: duration-only sleep "
                    f"duration_s={duration}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[sleep-backfill] {day_iso}: sleep_score={score} "
                    f"quality={qualifier or '-'} duration_s={duration if duration is not None else '-'}",
                    file=sys.stderr,
                )

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
            "[sleep-backfill] complete "
            f"stored={counters['stored_points']} "
            f"missing={counters['missing_days']} "
            f"skipped_existing={counters['skipped_existing']} "
            f"garmin_queries={counters['garmin_queries']}",
            file=sys.stderr,
        )
        print(
            "[sleep-backfill] Target DB is now ready. To copy sleep_history to local "
            "Postgres without touching Garmin again, run:",
            file=sys.stderr,
        )
        print(
            "MIRROR_TABLES=sleep_history python scripts/mirror_neon_to_local.py",
            file=sys.stderr,
        )
    else:
        print(
            "[sleep-backfill] paused before completion. Rerun the same command to resume.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    ensure_project_python()
    raise SystemExit(main())
