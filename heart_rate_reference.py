"""Shared heart-rate reference used by the coach-facing scripts."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any


DEFAULT_MAX_HR = 181
MAX_HR_WINDOW_DAYS = 90
_MIN_PLAUSIBLE_OBSERVED_HR = 30
_MAX_PLAUSIBLE_OBSERVED_HR = 250


def _max_hr_points(history: list[dict[str, Any]]) -> list[tuple[date, float]]:
    points = []
    for item in history or []:
        stamp_value = item.get("date") or item.get("start_date_local")
        hr_value = item.get("max_heartrate")
        try:
            stamp = date.fromisoformat(str(stamp_value)[:10])
            hr = float(hr_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(hr):
            continue
        if not (_MIN_PLAUSIBLE_OBSERVED_HR <= hr <= _MAX_PLAUSIBLE_OBSERVED_HR):
            continue
        points.append((stamp, hr))
    return points


def max_hr_reference(
    history: list[dict[str, Any]],
    day_iso: str | None,
    *,
    window_days: int = MAX_HR_WINDOW_DAYS,
    default: float = DEFAULT_MAX_HR,
    override: float | None = None,
) -> dict[str, Any]:
    """Return the maximum observed HR in the preceding window, with provenance."""
    try:
        day = date.fromisoformat(str(day_iso)[:10]) if day_iso else None
    except ValueError:
        day = None

    observed = []
    if day is not None:
        start = day - timedelta(days=window_days)
        observed = [point for point in _max_hr_points(history) if start <= point[0] <= day]

    peak = max(observed, key=lambda point: (point[1], point[0])) if observed else None
    observed_hr = peak[1] if peak else None
    observed_on = peak[0].isoformat() if peak else None

    if override:
        value = float(override)
        source = "override"
    elif observed_hr is not None:
        value = observed_hr
        source = "observed_90d"
    else:
        value = float(default)
        source = "fallback"

    return {
        "value": value,
        "source": source,
        "observed90d": observed_hr,
        "observedOn": observed_on,
        "windowDays": window_days,
    }
