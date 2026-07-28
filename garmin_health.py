"""Garmin wellness snapshots attached to run end times."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    number = _coerce_float(value)
    if number is None:
        return None
    return int(round(number))


def _iso_seconds(dt: datetime) -> str:
    return dt.replace(microsecond=0, tzinfo=None).isoformat()


def parse_local_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_garmin_local_timestamp(value: Any) -> datetime | None:
    """Parse Garmin local timestamps.

    Garmin sometimes sends local wall-clock timestamps as epoch millis shifted to
    the local clock (for example sleepEndTimestampLocal). Treat them as naive
    local datetimes so they can be compared with activities.start_date_local.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        try:
            return datetime.utcfromtimestamp(seconds).replace(microsecond=0)
        except (OSError, OverflowError, ValueError):
            return None
    return parse_local_datetime(value)


def parse_day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def run_end_local(run: dict[str, Any]) -> datetime | None:
    start = parse_local_datetime(run.get("start_date_local"))
    if start is None:
        return None
    duration = _coerce_int(run.get("elapsed_time"))
    if duration is None or duration <= 0:
        duration = _coerce_int(run.get("moving_time")) or 0
    return start + timedelta(seconds=duration)


def health_candidate_days(end_time: datetime) -> list[str]:
    current = end_time.date()
    previous = current - timedelta(days=1)
    return [current.isoformat(), previous.isoformat()]


def _sleep_scores(payload: dict[str, Any], dto: dict[str, Any]) -> dict[str, Any]:
    scores = dto.get("sleepScores")
    if not isinstance(scores, dict):
        scores = payload.get("sleepScores")
    return scores if isinstance(scores, dict) else {}


def extract_sleep_snapshot(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        return None

    scores = _sleep_scores(payload, dto)
    overall = scores.get("overall") if isinstance(scores.get("overall"), dict) else {}
    score = _coerce_int(overall.get("value"))
    duration = _coerce_int(dto.get("sleepTimeSeconds"))
    start = parse_garmin_local_timestamp(dto.get("sleepStartTimestampLocal"))
    end = parse_garmin_local_timestamp(dto.get("sleepEndTimestampLocal"))
    sleep_day = parse_day(dto.get("calendarDate") or payload.get("calendarDate"))

    if score is None and duration is None and start is None and end is None:
        return None

    quality = overall.get("qualifierKey")
    if not isinstance(quality, str) or not quality.strip():
        quality = dto.get("sleepResultTypePK")
    return {
        "date": sleep_day.isoformat() if sleep_day else None,
        "end_time": end,
        "health_sleep_date": sleep_day.isoformat() if sleep_day else None,
        "health_sleep_score": score,
        "health_sleep_quality": str(quality) if quality not in (None, "") else None,
        "health_sleep_duration_seconds": duration,
        "health_sleep_start_local": _iso_seconds(start) if start else None,
        "health_sleep_end_local": _iso_seconds(end) if end else None,
    }


def _is_snapshot_available(snapshot: dict[str, Any], run_end: datetime) -> bool:
    end_time = snapshot.get("end_time")
    if isinstance(end_time, datetime):
        return end_time <= run_end
    day = parse_day(snapshot.get("date"))
    return bool(day and day <= run_end.date())


def _latest_available(
    snapshots: list[dict[str, Any] | None], run_end: datetime
) -> dict[str, Any] | None:
    candidates = [
        snapshot for snapshot in snapshots
        if snapshot and _is_snapshot_available(snapshot, run_end)
    ]
    if not candidates:
        return None

    def sort_key(snapshot: dict[str, Any]) -> tuple[datetime, str]:
        end_time = snapshot.get("end_time")
        if isinstance(end_time, datetime):
            return end_time, str(snapshot.get("date") or "")
        day = parse_day(snapshot.get("date")) or date.min
        return datetime.combine(day, datetime.min.time()), str(snapshot.get("date") or "")

    return max(candidates, key=sort_key)


def extract_hrv_snapshot(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    summary = payload.get("hrvSummary")
    if not isinstance(summary, dict):
        summary = payload
    hrv_day = parse_day(summary.get("calendarDate") or payload.get("calendarDate"))
    end = (
        parse_garmin_local_timestamp(payload.get("endTimestampLocal"))
        or parse_garmin_local_timestamp(payload.get("sleepEndTimestampLocal"))
    )
    baseline = summary.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    last_night = _coerce_float(summary.get("lastNightAvg"))
    weekly = _coerce_float(summary.get("weeklyAvg"))
    status = summary.get("status") or payload.get("hrvStatus")

    if last_night is None:
        last_night = _coerce_float(payload.get("avgOvernightHrv"))
    if last_night is None and weekly is None and status in (None, ""):
        return None

    return {
        "date": hrv_day.isoformat() if hrv_day else None,
        "end_time": end,
        "health_hrv_date": hrv_day.isoformat() if hrv_day else None,
        "health_hrv_last_night_avg_ms": last_night,
        "health_hrv_weekly_avg_ms": weekly,
        "health_hrv_status": str(status) if status not in (None, "") else None,
        "health_hrv_baseline_low_ms": _coerce_float(baseline.get("balancedLow")),
        "health_hrv_baseline_high_ms": _coerce_float(baseline.get("balancedUpper")),
    }


def extract_resting_hr_snapshot(
    heart_rates_payload: Any, rhr_payload: Any = None
) -> dict[str, Any] | None:
    day: date | None = None
    resting = None
    weekly = None
    if isinstance(heart_rates_payload, dict):
        day = parse_day(heart_rates_payload.get("calendarDate"))
        resting = _coerce_int(heart_rates_payload.get("restingHeartRate"))
        weekly = _coerce_float(heart_rates_payload.get("lastSevenDaysAvgRestingHeartRate"))

    if resting is None and isinstance(rhr_payload, dict):
        metrics = (
            ((rhr_payload.get("allMetrics") or {}).get("metricsMap") or {})
            .get("WELLNESS_RESTING_HEART_RATE")
        )
        if isinstance(metrics, list):
            for item in metrics:
                if not isinstance(item, dict):
                    continue
                value = _coerce_int(item.get("value"))
                if value is None:
                    continue
                resting = value
                day = parse_day(item.get("calendarDate")) or day
                break

    if resting is None and weekly is None:
        return None
    return {
        "date": day.isoformat() if day else None,
        "health_resting_hr_date": day.isoformat() if day else None,
        "health_resting_hr_bpm": resting,
        "health_resting_hr_7d_avg_bpm": weekly,
    }


def _clean_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in snapshot.items()
        if key.startswith("health_") and value not in (None, "")
    }


def build_run_health_snapshot(
    run: dict[str, Any],
    sleep_payloads: list[Any],
    hrv_payloads: list[Any],
    heart_rates_payloads: list[Any],
    rhr_payloads: list[Any] | None = None,
) -> dict[str, Any]:
    end_time = run_end_local(run)
    if end_time is None:
        return {}

    snapshot: dict[str, Any] = {"health_snapshot_at": _iso_seconds(end_time)}
    sleep = _latest_available(
        [extract_sleep_snapshot(payload) for payload in sleep_payloads], end_time
    )
    hrv = _latest_available(
        [extract_hrv_snapshot(payload) for payload in hrv_payloads], end_time
    )
    if sleep:
        snapshot.update(_clean_snapshot(sleep))
    if hrv:
        snapshot.update(_clean_snapshot(hrv))

    rhr_payloads = rhr_payloads or []
    fallback_resting = None
    for heart_payload, rhr_payload in zip(
        heart_rates_payloads, [*rhr_payloads, *([None] * len(heart_rates_payloads))]
    ):
        resting = extract_resting_hr_snapshot(heart_payload, rhr_payload)
        if not resting or not _is_snapshot_available(resting, end_time):
            continue
        if resting.get("health_resting_hr_bpm") is not None:
            snapshot.update(_clean_snapshot(resting))
            break
        if fallback_resting is None:
            fallback_resting = resting
    else:
        if fallback_resting:
            snapshot.update(_clean_snapshot(fallback_resting))

    return snapshot


def fetch_run_health_snapshot(
    api: Any,
    run: dict[str, Any],
    cache: dict[tuple[str, str], Any] | None = None,
    *,
    on_error: Callable[[str, str, Exception], None] | None = None,
) -> dict[str, Any]:
    end_time = run_end_local(run)
    if end_time is None:
        return {}
    cache = cache if cache is not None else {}

    def get(method_name: str, day: str) -> Any:
        key = (method_name, day)
        if key in cache:
            return cache[key]
        try:
            method = getattr(api, method_name)
            cache[key] = method(day)
        except Exception as exc:
            cache[key] = None
            if on_error:
                on_error(method_name, day, exc)
        return cache[key]

    days = health_candidate_days(end_time)
    snapshot: dict[str, Any] = {"health_snapshot_at": _iso_seconds(end_time)}

    sleep_payload = get("get_sleep_data", days[0])
    sleep = _latest_available([extract_sleep_snapshot(sleep_payload)], end_time)
    if not sleep:
        sleep = _latest_available(
            [extract_sleep_snapshot(get("get_sleep_data", days[1]))], end_time
        )
    if sleep:
        snapshot.update(_clean_snapshot(sleep))

    hrv_payload = get("get_hrv_data", days[0])
    hrv = _latest_available([extract_hrv_snapshot(hrv_payload)], end_time)
    if not hrv:
        hrv = _latest_available(
            [extract_hrv_snapshot(get("get_hrv_data", days[1]))], end_time
        )
    if hrv:
        snapshot.update(_clean_snapshot(hrv))

    resting = extract_resting_hr_snapshot(
        get("get_heart_rates", days[0]), get("get_rhr_day", days[0])
    )
    if not resting or not _is_snapshot_available(resting, end_time) or (
        resting.get("health_resting_hr_bpm") is None
    ):
        fallback = extract_resting_hr_snapshot(
            get("get_heart_rates", days[1]), get("get_rhr_day", days[1])
        )
        if fallback and _is_snapshot_available(fallback, end_time):
            resting = fallback
    if resting and _is_snapshot_available(resting, end_time):
        snapshot.update(_clean_snapshot(resting))

    return snapshot
