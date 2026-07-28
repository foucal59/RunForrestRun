#!/usr/bin/env python3
"""Backfill Garmin activity-only metrics for linked running activities.

This deliberately fetches only activity summaries, laps, streams, HR/power
zones and running dynamics. It never calls Garmin sleep or wellness endpoints.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--activity-id", action="append", type=int, default=[])
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--token-dir",
        default=str(ROOT / ".runtime" / "garminconnect"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    _load_env(ROOT / ".env")

    import db
    from garmin_freshness import _enrich_activity, _extract_run, load_garmin_api

    db.init_db_migrations()
    known_ids = db.get_garmin_run_ids()
    if not known_ids:
        print("[run-metrics] no linked Garmin runs in the primary DB", file=sys.stderr)
        return 0

    api = load_garmin_api(args.token_dir)
    if api is None:
        print("[run-metrics] Garmin authentication unavailable", file=sys.stderr)
        return 1

    found: dict[int, dict] = {}
    for offset in range(0, 5000, 100):
        page = api.get_activities(start=offset, limit=100)
        if not isinstance(page, list) or not page:
            break
        for raw in page:
            activity_id = raw.get("activityId") if isinstance(raw, dict) else None
            if activity_id is None or int(activity_id) not in known_ids:
                continue
            run = _extract_run(raw)
            if run is not None:
                found[int(activity_id)] = run
        if len(found) == len(known_ids) or len(page) < 100:
            break
        time.sleep(0.1)

    runs = sorted(found.values(), key=lambda run: run.get("start_date_local") or "")
    if args.activity_id:
        selected_ids = set(args.activity_id)
        runs = [run for run in runs if int(run["id"]) in selected_ids]
    if args.limit > 0:
        runs = runs[-args.limit:]
    print(
        f"[run-metrics] linked={len(known_ids)} found={len(found)} selected={len(runs)}",
        file=sys.stderr,
    )

    summary_updates = 0
    enriched = 0
    for index, run in enumerate(runs, start=1):
        summary_updates += db.upsert_garmin_run_summaries([run], force=True)
        if not args.summary_only:
            if _enrich_activity(
                api,
                int(run["id"]),
                float(run.get("distance") or 0),
                str(run.get("start_date_local") or ""),
            ):
                enriched += 1
        print(
            f"[run-metrics] {index}/{len(runs)} activity={run['id']}",
            file=sys.stderr,
        )
        time.sleep(0.1)

    missing = sorted(known_ids - set(found))
    print(
        f"[run-metrics] summaries_updated={summary_updates} enriched={enriched} "
        f"missing_from_garmin={missing}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
