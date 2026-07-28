"""
Garmin Connect freshness check.

Token storage:
- Self-hosted: files in GARMIN_TOKEN_DIR (default `.runtime/garminconnect/`)
- Vercel: JSON payload in Neon sync_meta['garmin_tokens']

Legacy garth token stores are migrated on read when possible.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from garminconnect import Garmin, GarminConnectAuthenticationError

import db
from garmin_health import extract_sleep_snapshot, fetch_run_health_snapshot

GARMIN_TOKEN_DIR = os.environ.get("GARMIN_TOKEN_DIR", "")
GARMIN_TOKEN_FILE = "garmin_tokens.json"
LEGACY_GARTH_TOKEN_FILE = "oauth2_token.json"

RUNNING_TYPE_KEYS = {
    "running",
    "trail_running",
    "treadmill_running",
    "indoor_running",
    "street_running",
    "track_running",
    "obstacle_run",
    "virtual_run",
}

_RUN_SUMMARY_EXCLUDED_KEYS = {
    "ownerDisplayName",
    "ownerFullName",
    "ownerProfileImageUrlLarge",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlSmall",
    "userRoles",
    "userPro",
    "summarizedDiveInfo",
    "qualifyingDive",
    "decoDive",
}


class GarminMFARequiredError(RuntimeError):
    pass


def _extract_client_id_from_token(di_token: str) -> str | None:
    try:
        parts = di_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        value = payload.get("client_id")
        return str(value) if value else None
    except Exception:
        return None


def _normalize_token_data(token_data: Any) -> dict[str, Any]:
    if isinstance(token_data, str):
        token_data = json.loads(token_data)
    if not isinstance(token_data, dict):
        raise ValueError("Unsupported Garmin token payload")

    if GARMIN_TOKEN_FILE in token_data:
        token_data = token_data[GARMIN_TOKEN_FILE]
    elif LEGACY_GARTH_TOKEN_FILE in token_data:
        token_data = token_data[LEGACY_GARTH_TOKEN_FILE]

    if not isinstance(token_data, dict):
        raise ValueError("Unsupported Garmin token payload shape")

    if "di_token" in token_data or "di_refresh_token" in token_data:
        normalized = {
            "di_token": token_data.get("di_token"),
            "di_refresh_token": token_data.get("di_refresh_token"),
            "di_client_id": token_data.get("di_client_id"),
        }
    elif "access_token" in token_data or "refresh_token" in token_data:
        normalized = {
            "di_token": token_data.get("access_token"),
            "di_refresh_token": token_data.get("refresh_token"),
            "di_client_id": token_data.get("di_client_id"),
        }
    else:
        raise ValueError("Garmin token payload missing expected keys")

    if not normalized["di_client_id"] and normalized["di_token"]:
        normalized["di_client_id"] = _extract_client_id_from_token(
            str(normalized["di_token"])
        )
    if not normalized["di_token"] or not normalized["di_refresh_token"]:
        raise ValueError("Incomplete Garmin token payload")
    return normalized


def _primary_token_dir(token_dir: str = "") -> Path | None:
    raw = token_dir or GARMIN_TOKEN_DIR
    if not raw:
        return None
    return Path(raw).expanduser()


def _candidate_token_paths(token_dir: str = "") -> list[Path]:
    primary = _primary_token_dir(token_dir)
    if primary is None:
        return []

    candidates: list[Path] = [primary]
    if primary.name == "garminconnect":
        candidates.append(primary.with_name("garth"))
    elif primary.name == "garth":
        candidates.append(primary.with_name("garminconnect"))
    return candidates


def _read_local_token_payload(token_dir: str = "") -> tuple[dict[str, Any] | None, Path | None]:
    for candidate in _candidate_token_paths(token_dir):
        if candidate.is_file():
            try:
                return _normalize_token_data(candidate.read_text()), candidate.parent
            except Exception as exc:
                print(f"[GARMIN] failed to parse token file {candidate}: {exc}", file=sys.stderr)
                continue

        token_file = candidate / GARMIN_TOKEN_FILE
        if token_file.exists():
            try:
                return _normalize_token_data(token_file.read_text()), candidate
            except Exception as exc:
                print(f"[GARMIN] failed to parse {token_file}: {exc}", file=sys.stderr)
                continue

        legacy_file = candidate / LEGACY_GARTH_TOKEN_FILE
        if legacy_file.exists():
            try:
                legacy_payload = json.loads(legacy_file.read_text())
                return _normalize_token_data({LEGACY_GARTH_TOKEN_FILE: legacy_payload}), candidate
            except Exception as exc:
                print(f"[GARMIN] failed to parse legacy token file {legacy_file}: {exc}", file=sys.stderr)
                continue

    return None, _primary_token_dir(token_dir)


def _load_profile_from_api(api: Garmin) -> None:
    api._load_profile_and_settings()


def serialize_garmin_tokens(api: Garmin) -> dict[str, Any]:
    return {GARMIN_TOKEN_FILE: json.loads(api.client.dumps())}


def _save_api_tokens(api: Garmin, token_dir: str = "") -> None:
    target = _primary_token_dir(token_dir)
    if target is not None:
        api.client.dump(str(target))
        print(f"[GARMIN] tokens saved to {target}", file=sys.stderr)
        # IMPORTANT : ne pas s'arrêter ici. Sur self_hosted un token_dir local est
        # défini, donc garminconnect rafraîchit le token dans ce fichier — mais ce
        # token frais n'était jamais propagé vers sync_meta. Vercel, qui n'a pas de
        # token_dir, lit uniquement sync_meta et se retrouvait avec un token figé
        # (access token expiré) → freshness-check échoue en silence
        # (garmin_not_authenticated) → aucun run ajouté → entraînement non mis à jour.
        # On mirrore donc systématiquement le token (possiblement rafraîchi) dans
        # sync_meta, répliqué ensuite vers Neon, pour que Vercel ait toujours un
        # token vivant.
    try:
        db.set_sync_meta("garmin_tokens", serialize_garmin_tokens(api))
        print("[GARMIN] tokens mirrored to sync_meta (DB/Neon)", file=sys.stderr)
    except Exception as exc:
        print(f"[GARMIN] sync_meta token mirror failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)


def _build_profile(api: Garmin) -> dict[str, Any]:
    raw_profile: dict[str, Any] = {}
    try:
        profile_data = api.client.connectapi(
            "/userprofile-service/userprofile/personal-information"
        )
        if isinstance(profile_data, dict):
            raw_profile = profile_data
    except Exception as exc:
        print(f"[GARMIN] profile fetch failed (non-fatal): {exc}", file=sys.stderr)

    user_info = raw_profile.get("userInfo", {}) if isinstance(raw_profile, dict) else {}
    fallback_profile: dict[str, Any] = {}
    try:
        profile_data = api.get_user_profile()
        if isinstance(profile_data, dict):
            fallback_profile = profile_data
    except Exception as exc:
        print(f"[GARMIN] user profile fetch failed (non-fatal): {exc}", file=sys.stderr)

    fallback_user_data = fallback_profile.get("userData", {})
    if not isinstance(fallback_user_data, dict):
        fallback_user_data = {}

    full_name = (
        user_info.get("fullName")
        or fallback_user_data.get("fullName")
        or getattr(api, "full_name", "")
        or ""
    )
    display_name = (
        user_info.get("displayName")
        or fallback_user_data.get("displayName")
        or getattr(api, "display_name", "")
        or ""
    )
    return {
        "user_id": (
            user_info.get("userId")
            or fallback_profile.get("id")
            or fallback_user_data.get("userId")
            or 0
        ),
        "display_name": display_name,
        "full_name": full_name,
        "profile_image": (
            user_info.get("profileImageUrlLarge")
            or fallback_user_data.get("profileImageUrlLarge")
            or ""
        ),
    }


def _api_from_token_payload(token_data: Any) -> Garmin | None:
    try:
        normalized = _normalize_token_data(token_data)
        api = Garmin()
        api.client.loads(json.dumps(normalized))
        # Le token stocké en base peut avoir un access token (di_token) expiré :
        # ce chemin n'appelle jamais login(), qui est le seul endroit où
        # garminconnect rafraîchit proactivement le DI token. On force donc le
        # refresh ici (via le di_refresh_token, plus longue durée de vie) avant le
        # premier appel authentifié, sinon Vercel meurt sur un access token mort.
        try:
            client = api.client
            if getattr(client, "di_refresh_token", None) and client._token_expires_soon():
                print("[GARMIN] DB access token expiring/expired — refreshing before use", file=sys.stderr)
                client._refresh_session()
        except Exception as exc:
            print(f"[GARMIN] proactive token refresh failed (continuing): {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            _load_profile_from_api(api)
        except Exception as exc:
            print(
                f"[GARMIN] profile preload failed (non-fatal): {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return api
    except Exception as exc:
        print(f"[GARMIN] token payload load failed: {exc}", file=sys.stderr)
        return None


def load_garmin_api(token_dir: str = "") -> Garmin | None:
    target = _primary_token_dir(token_dir)
    if target is not None:
        token_payload, source_dir = _read_local_token_payload(token_dir)
        if token_payload:
            api = _api_from_token_payload(token_payload)
            if api is not None:
                needs_save = source_dir is not None and (
                    source_dir != target or not (target / GARMIN_TOKEN_FILE).exists()
                )
                if needs_save:
                    _save_api_tokens(api, str(target))
                    print(
                        f"[GARMIN] migrated legacy token store to {target / GARMIN_TOKEN_FILE}",
                        file=sys.stderr,
                    )
                else:
                    print(f"[GARMIN] tokens loaded from {source_dir}", file=sys.stderr)
                return api
            print("[GARMIN] local token load failed — trying DB fallback", file=sys.stderr)
        else:
            print("[GARMIN] no local Garmin tokens found — trying DB fallback", file=sys.stderr)

        meta = db.get_sync_meta() or {}
        token_data = meta.get("garmin_tokens")
        if not token_data:
            meta = db.get_sync_meta_from_neon() or {}
            token_data = meta.get("garmin_tokens")
            if token_data:
                print("[GARMIN] using garmin_tokens from Neon sync_meta", file=sys.stderr)
        elif token_data:
            print("[GARMIN] using garmin_tokens from local sync_meta", file=sys.stderr)

        if not token_data:
            return None

        api = _api_from_token_payload(token_data)
        if api is None:
            return None
        _save_api_tokens(api, str(target))
        print(f"[GARMIN] hydrated local Garmin tokens into {target}", file=sys.stderr)
        return api

    try:
        meta = db.get_sync_meta() or {}
        token_data = meta.get("garmin_tokens")
        if not token_data:
            print("[GARMIN] no garmin_tokens in Neon sync_meta", file=sys.stderr)
            return None
        api = _api_from_token_payload(token_data)
        if api is None:
            return None
        if not isinstance(token_data, dict) or GARMIN_TOKEN_FILE not in token_data:
            _save_api_tokens(api)
            print("[GARMIN] migrated legacy Neon Garmin tokens", file=sys.stderr)
        else:
            print("[GARMIN] tokens loaded from Neon", file=sys.stderr)
        return api
    except Exception as exc:
        print(f"[GARMIN] load tokens from Neon failed: {exc}", file=sys.stderr)
    return None


def load_garmin_profile(token_dir: str = "") -> dict[str, Any] | None:
    """Return the Garmin profile represented by an existing valid token store."""
    api = load_garmin_api(token_dir)
    return _build_profile(api) if api is not None else None


def garmin_login(
    email: str,
    password: str,
    token_dir: str = "",
    mfa_code: str = "",
) -> dict[str, Any]:
    prompt_mfa = None
    clean_mfa = (mfa_code or "").strip()
    if clean_mfa:
        prompt_mfa = lambda: clean_mfa

    api = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    tokenstore = str(_primary_token_dir(token_dir)) if _primary_token_dir(token_dir) else None

    try:
        api.login(tokenstore)
    except GarminConnectAuthenticationError as exc:
        message = str(exc)
        if "mfa" in message.lower():
            raise GarminMFARequiredError(
                "Code MFA Garmin requis. Renseigne le champ MFA puis réessaie."
            ) from exc
        raise

    _save_api_tokens(api, token_dir)
    return _build_profile(api)


def get_garmin_profile(token_dir: str = "") -> dict[str, Any] | None:
    api = load_garmin_api(token_dir)
    if api is None:
        return None
    try:
        profile = _build_profile(api)
        _save_api_tokens(api, token_dir)
        return profile
    except Exception as exc:
        print(f"[GARMIN] get_garmin_profile failed: {exc}", file=sys.stderr)
        return None


def _run_specific_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the activity payload while excluding account and sleep data.

    Garmin adds fields over time. Persisting the run-only raw payload prevents
    a newly introduced performance metric from being silently lost before a
    normalized column is added. Profile fields and anything sleep-related are
    intentionally excluded from this activity store.
    """
    return {
        key: value
        for key, value in raw.items()
        if key not in _RUN_SUMMARY_EXCLUDED_KEYS and "sleep" not in key.lower()
    }


def _summary_zone_times(raw: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    rows = []
    for zone in range(1, 6):
        value = raw.get(f"{prefix}_{zone}")
        if value is None:
            continue
        rows.append({"zone": zone, "seconds": float(value), "source": "summary"})
    return rows


def _normalize_zone_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize Garmin HR/power zone endpoints without storing unrelated data."""
    if not isinstance(payload, list):
        return []
    rows = []
    for item in payload:
        if not isinstance(item, dict) or item.get("zoneNumber") is None:
            continue
        rows.append({
            "zone": int(item["zoneNumber"]),
            "seconds": float(item.get("secsInZone") or 0),
            "low_boundary": item.get("zoneLowBoundary"),
            "source": "garmin_zone_endpoint",
        })
    return rows


def _privacy_is_private(value: Any) -> bool | None:
    if not isinstance(value, dict):
        return None
    key = value.get("typeKey") or value.get("key") or value.get("label")
    if key is None:
        return None
    return str(key).lower() == "private"


def _extract_run(raw: dict[str, Any]) -> dict[str, Any] | None:
    activity_type = raw.get("activityType") or {}
    type_key = (
        activity_type.get("typeKey", "").lower()
        if isinstance(activity_type, dict)
        else ""
    )
    if type_key not in RUNNING_TYPE_KEYS:
        return None

    start_raw = raw.get("startTimeLocal", "")
    try:
        start_iso = datetime.fromisoformat(start_raw.replace(" ", "T")).isoformat()
    except Exception:
        start_iso = start_raw

    cadence_raw = raw.get("averageRunningCadenceInStepsPerMinute") or 0
    cadence = (cadence_raw / 2.0) if cadence_raw else None
    max_cadence_raw = (
        raw.get("maxRunningCadenceInStepsPerMinute")
        or raw.get("maxDoubleCadence")
        or 0
    )
    fastest_splits = {
        key.removeprefix("fastestSplit_"): value
        for key, value in raw.items()
        if key.startswith("fastestSplit_") and value is not None
    }
    start_latlng = None
    if raw.get("startLatitude") is not None and raw.get("startLongitude") is not None:
        start_latlng = [raw["startLatitude"], raw["startLongitude"]]
    end_latlng = None
    if raw.get("endLatitude") is not None and raw.get("endLongitude") is not None:
        end_latlng = [raw["endLatitude"], raw["endLongitude"]]

    activity_id = int(raw["activityId"])
    return {
        "id": activity_id,
        "garmin_activity_id": activity_id,
        "source": "garmin",
        "athlete_id": raw.get("ownerId") or 0,
        "name": raw.get("activityName") or "Run",
        "start_date_local": start_iso,
        "distance": float(raw.get("distance") or 0),
        "moving_time": int(raw.get("movingDuration") or raw.get("duration") or 0),
        "elapsed_time": int(raw.get("duration") or 0),
        "total_elevation_gain": raw.get("elevationGain") or 0,
        "average_speed": float(raw.get("averageSpeed") or 0),
        "max_speed": float(raw.get("maxSpeed") or 0),
        "average_heartrate": raw.get("averageHR"),
        "max_heartrate": raw.get("maxHR"),
        "calories": raw.get("calories") or 0,
        "average_cadence": cadence,
        "max_cadence": (max_cadence_raw / 2.0) if max_cadence_raw else None,
        "sport_type": "Run",
        "type": "Run",
        "has_heartrate": bool(raw.get("averageHR")),
        "pr_count": 0,
        "suffer_score": None,
        "gear_id": None,
        "summary_polyline": None,
        "start_latlng": start_latlng,
        "end_latlng": end_latlng,
        "start_date_gmt": raw.get("startTimeGMT"),
        "manual": raw.get("manualActivity"),
        "private": _privacy_is_private(raw.get("privacy")),
        "average_temp": raw.get("averageTemperature") or raw.get("avgTemperature"),
        "average_watts": raw.get("avgPower"),
        "weighted_average_watts": raw.get("normPower"),
        "max_watts": raw.get("maxPower"),
        "elev_high": raw.get("maxElevation"),
        "elev_low": raw.get("minElevation"),
        "device_name": raw.get("manufacturer"),
        "garmin_activity_uuid": raw.get("activityUUID"),
        "garmin_timezone_id": raw.get("timeZoneId"),
        "garmin_device_id": raw.get("deviceId"),
        "lap_count": raw.get("lapCount"),
        "elevation_loss": raw.get("elevationLoss"),
        "aerobic_training_effect": raw.get("aerobicTrainingEffect"),
        "anaerobic_training_effect": raw.get("anaerobicTrainingEffect"),
        "activity_training_load": raw.get("activityTrainingLoad"),
        "vo2max": raw.get("vO2MaxValue"),
        "training_effect_label": raw.get("trainingEffectLabel"),
        "avg_stride_length": raw.get("avgStrideLength"),
        "avg_ground_contact_time": raw.get("avgGroundContactTime"),
        "avg_vertical_oscillation": raw.get("avgVerticalOscillation"),
        "avg_vertical_ratio": raw.get("avgVerticalRatio"),
        "avg_grade_adjusted_speed": raw.get("avgGradeAdjustedSpeed"),
        "body_battery_delta": raw.get("differenceBodyBattery"),
        "steps": raw.get("steps"),
        "moderate_intensity_minutes": raw.get("moderateIntensityMinutes"),
        "vigorous_intensity_minutes": raw.get("vigorousIntensityMinutes"),
        "min_temperature": raw.get("minTemperature"),
        "max_temperature": raw.get("maxTemperature"),
        "avg_respiration_rate": raw.get("avgRespirationRate"),
        "min_respiration_rate": raw.get("minRespirationRate"),
        "max_respiration_rate": raw.get("maxRespirationRate"),
        "water_estimated": raw.get("waterEstimated"),
        "garmin_workout_id": raw.get("workoutId"),
        "garmin_course_id": raw.get("courseId"),
        "hr_time_in_zones": _summary_zone_times(raw, "hrTimeInZone"),
        "power_time_in_zones": _summary_zone_times(raw, "powerTimeInZone"),
        "garmin_fastest_splits": fastest_splits,
        "garmin_summary": _run_specific_summary(raw),
    }


def _run_identifier_strings(run: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in (run.get("id"), run.get("garmin_activity_id"))
        if value is not None and str(value)
    }


def _is_tombstoned_run(run: dict[str, Any], tombstone_ids: set[str]) -> bool:
    return bool(_run_identifier_strings(run) & {str(value) for value in tombstone_ids})


def _filter_tombstoned_runs(
    runs: list[dict[str, Any]], tombstone_ids: set[str]
) -> list[dict[str, Any]]:
    """Pure anti-resurrection filter used by freshness and unit tests."""
    normalized = {str(value) for value in tombstone_ids}
    return [run for run in runs if not (_run_identifier_strings(run) & normalized)]


def _parse_existing_starts(existing: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Pre-parse DB rows once so _is_duplicate doesn't re-parse them per candidate."""
    parsed = []
    for row in existing:
        try:
            db_dt = datetime.fromisoformat(
                str(row.get("start_date_local", "")).replace("Z", "").replace(" ", "T")
            ).replace(tzinfo=None)
            parsed.append((db_dt, float(row.get("distance") or 0)))
        except Exception:
            continue
    return parsed


def _is_duplicate(run: dict[str, Any], existing_parsed: list[tuple[datetime, float]]) -> bool:
    try:
        run_dt = datetime.fromisoformat(run["start_date_local"]).replace(tzinfo=None)
    except Exception:
        return False
    run_dist = float(run.get("distance") or 0)

    for db_dt, db_dist in existing_parsed:
        diff_s = abs((run_dt - db_dt).total_seconds())
        if diff_s > 300:
            continue
        if db_dist and run_dist:
            if abs(run_dist - db_dist) / max(run_dist, db_dist) < 0.02:
                return True
        else:
            return True
    return False


def _safe(getter, label):
    """Call a Garmin getter, log + swallow errors. Returns None on failure."""
    try:
        return getter()
    except Exception as exc:
        print(f"[GARMIN] {label} failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


# ── Phase 3: best efforts from splits ──

_BEST_EFFORT_TARGETS = [
    ("5K", 5000),
    ("10K", 10000),
    ("Half-Marathon", 21097),
    ("Marathon", 42195),
]


def _compute_best_efforts(activity_id: int, cum: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Best efforts from cumulative (distance_m, time_s) samples.

    Resolution follows the input: stream samples when available (precise),
    lap boundaries otherwise (coarse). Two-pointer sweep, O(n) per target.
    The frontend falls back to pace-estimation when best_efforts are absent, so
    this only improves accuracy, never breaks Records."""
    efforts: list[dict[str, Any]] = []
    if not cum:
        return efforts
    n = len(cum)
    for idx, (name, meters) in enumerate(_BEST_EFFORT_TARGETS, start=1):
        best = None
        j = 0
        for i in range(n):
            d0 = cum[i - 1][0] if i > 0 else 0.0
            t0 = cum[i - 1][1] if i > 0 else 0.0
            if j < i:
                j = i
            while j < n and cum[j][0] - d0 < meters:
                j += 1
            if j >= n:
                break  # windows starting later can't reach the target either
            dur = cum[j][1] - t0
            if dur > 0 and (best is None or dur < best):
                best = dur
        if best is not None:
            efforts.append({
                "id": int(activity_id) * 100 + idx,
                "name": name,
                "distance": meters,
                "moving_time": int(best),
                "elapsed_time": int(best),
            })
    return efforts


def _laps_from_garmin(activity_id: int, laps_raw: list) -> list[dict[str, Any]]:
    """Normalize Garmin lapDTOs into activity_laps rows.

    Lap ids are synthesized (activity_id * 1000 + index) since Garmin laps have
    no usable id; the delete+insert in db.upsert_activity_laps keeps it stable."""
    rows: list[dict[str, Any]] = []
    for i, lap in enumerate(laps_raw, start=1):
        if not isinstance(lap, dict):
            continue
        idx = int(lap.get("lapIndex") or i)
        t = float(lap.get("duration") or lap.get("elapsedDuration") or 0)
        mt = float(lap.get("movingDuration") or t or 0)
        hr = lap.get("averageHR")
        max_hr = lap.get("maxHR")
        cad = lap.get("averageRunCadence")
        rows.append({
            "id": int(activity_id) * 1000 + idx,
            "lap_index": idx,
            "name": f"Lap {idx}",
            "distance": float(lap.get("distance") or 0),
            "elapsed_time": int(t),
            "moving_time": int(mt),
            "start_date": lap.get("startTimeGMT") or lap.get("startTimeLocal"),
            "average_speed": float(lap.get("averageSpeed") or 0),
            "max_speed": float(lap.get("maxSpeed") or 0),
            "average_heartrate": float(hr) if hr is not None else None,
            "max_heartrate": int(max_hr) if max_hr is not None else None,
            # Garmin reports double (both-feet) cadence; halve to match the
            # per-activity average_cadence convention used elsewhere.
            "average_cadence": (float(cad) / 2.0) if cad else None,
            "total_elevation_gain": float(lap.get("elevationGain") or 0),
            "elevation_loss": lap.get("elevationLoss"),
            "elev_high": lap.get("maxElevation"),
            "elev_low": lap.get("minElevation"),
            "max_vertical_speed": lap.get("maxVerticalSpeed"),
            "start_lat": lap.get("startLatitude"),
            "start_lng": lap.get("startLongitude"),
            "end_lat": lap.get("endLatitude"),
            "end_lng": lap.get("endLongitude"),
            "max_cadence": (
                float(lap["maxRunCadence"]) / 2.0
                if lap.get("maxRunCadence") is not None else None
            ),
            "average_watts": lap.get("averagePower"),
            "max_watts": lap.get("maxPower"),
            "min_watts": lap.get("minPower"),
            "weighted_average_watts": lap.get("normalizedPower"),
            "total_work": lap.get("totalWork"),
            "grade_adjusted_speed": lap.get("avgGradeAdjustedSpeed"),
            "ground_contact_time": lap.get("groundContactTime"),
            "stride_length": lap.get("strideLength"),
            "vertical_oscillation": lap.get("verticalOscillation"),
            "vertical_ratio": lap.get("verticalRatio"),
            "calories": lap.get("calories"),
            "bmr_calories": lap.get("bmrCalories"),
            "intensity_type": lap.get("intensityType"),
            "workout_step_index": lap.get("wktStepIndex"),
            "workout_compliance_score": lap.get("directWorkoutComplianceScore"),
            "garmin_data": lap,
        })
    return rows


def _splits_from_streams(time_col: list, dist_col: list, split_len: float = 1000.0) -> list[dict[str, Any]]:
    """Compute metric (1 km) splits from cumulative distance/time stream samples,
    interpolating the crossing time at each km boundary. Returns [] when the
    streams are too sparse to be useful."""
    pts = [(d, t) for d, t in zip(dist_col, time_col) if d is not None and t is not None]
    if len(pts) < 2 or pts[-1][0] < split_len / 2:
        return []
    splits: list[dict[str, Any]] = []
    boundary = split_len
    # Garmin's first sample can already be a few meters into the activity.
    # Splits still start at the activity origin, not at that first GPS sample.
    prev_d, prev_t = 0.0, 0.0
    last_d, last_t = pts[0]
    idx = 1
    for d, t in pts[1:]:
        while d >= boundary and d > last_d:
            frac = (boundary - last_d) / (d - last_d)
            t_b = last_t + frac * (t - last_t)
            dur = t_b - prev_t
            splits.append({
                "split": idx, "distance": boundary - prev_d,
                "elapsed_time": int(round(dur)), "moving_time": int(round(dur)),
                "average_speed": (split_len / dur) if dur > 0 else 0,
            })
            prev_d, prev_t = boundary, t_b
            idx += 1
            boundary += split_len
        last_d, last_t = d, t
    if last_d - prev_d > 1:  # trailing partial split
        dur = last_t - prev_t
        splits.append({
            "split": idx, "distance": last_d - prev_d,
            "elapsed_time": int(round(dur)), "moving_time": int(round(dur)),
            "average_speed": ((last_d - prev_d) / dur) if dur > 0 else 0,
        })
    return splits


def _enrich_activity(api: Garmin, aid: int, distance: float, start_iso: str = "") -> bool:
    """Fetch + store the full granularity of one run: laps, streams, km splits,
    best efforts. Returns True when Garmin returned actual data.

    details_fetched_at is only marked when data came back (or when the run is
    >3 days old — at that point Garmin will never have more), so a run uploaded
    seconds ago with still-empty details gets retried on the next check."""
    # 1) Laps (watch laps, lapDTOs)
    splits_raw = _safe(lambda: api.get_activity_splits(aid), f"get_activity_splits({aid})")
    laps_raw = []
    if isinstance(splits_raw, dict):
        laps_raw = splits_raw.get("lapDTOs") or splits_raw.get("splits") or []
    lap_rows = _laps_from_garmin(aid, laps_raw)
    if lap_rows:
        try:
            db.upsert_activity_laps(aid, lap_rows)
        except Exception as exc:
            print(f"[GARMIN] upsert_activity_laps({aid}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        print(f"[GARMIN] no laps returned for {aid} (raw: {str(splits_raw)[:300]})", file=sys.stderr)

    # 2) Streams (per-sample GPS/HR/speed)
    points = 0
    streams = None
    try:
        streams = _build_streams(api, aid)
        if streams:
            db.upsert_streams(aid, streams)
            points = len(streams["time"]["data"])
            print(f"[GARMIN] hydrated {points} stream points for activity {aid}", file=sys.stderr)
    except Exception as exc:
        print(f"[GARMIN] streams hydrate({aid}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    # 3) Activity-specific HR and power zone times, including zone boundaries.
    hr_zones = _normalize_zone_payload(
        _safe(lambda: api.get_activity_hr_in_timezones(aid), f"get_activity_hr_in_timezones({aid})")
    )
    power_zones = _normalize_zone_payload(
        _safe(lambda: api.get_activity_power_in_timezones(aid), f"get_activity_power_in_timezones({aid})")
    )
    if hr_zones or power_zones:
        try:
            db.upsert_activity_run_zones(aid, hr_zones, power_zones)
        except Exception as exc:
            print(f"[GARMIN] run zones upsert({aid}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    # 4) Km splits + best efforts — stream-derived when possible (precise),
    #    lap-derived otherwise (coarse, like before).
    splits: list[dict[str, Any]] = []
    cum: list[tuple[float, float]] = []
    if streams:
        time_col = streams["time"]["data"]
        dist_col = streams["distance"]["data"]
        splits = _splits_from_streams(time_col, dist_col)
        cum = [(d, t) for d, t in zip(dist_col, time_col) if d is not None and t is not None]
    if not splits and lap_rows:
        build_cum = not cum
        cd = 0.0
        ct = 0.0
        for i, lap in enumerate(lap_rows, start=1):
            splits.append({
                "split": i, "distance": lap["distance"],
                "elapsed_time": lap["elapsed_time"], "moving_time": lap["moving_time"],
                "average_speed": lap["average_speed"],
            })
            if build_cum:
                cd += lap["distance"]
                ct += lap["elapsed_time"]
                cum.append((cd, ct))
    best_efforts = _compute_best_efforts(aid, cum)

    got_data = bool(lap_rows) or points > 0
    give_up = False
    if not got_data and start_iso:
        try:
            run_dt = datetime.fromisoformat(str(start_iso).replace("Z", "").replace(" ", "T")).replace(tzinfo=None)
            give_up = datetime.now() - run_dt > timedelta(days=3)
        except Exception:
            pass
    try:
        db.upsert_activity_details(aid, splits, best_efforts, distance,
                                   mark_fetched=got_data or give_up,
                                   replace_splits=bool(splits))
    except Exception as exc:
        print(f"[GARMIN] upsert_activity_details({aid}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    print(f"[GARMIN] enriched {aid}: laps={len(lap_rows)} stream_pts={points} "
          f"splits={len(splits)} efforts={len(best_efforts)} "
          f"hr_zones={len(hr_zones)} power_zones={len(power_zones)} "
          f"marked={got_data or give_up}", file=sys.stderr)
    return got_data


def _fetch_details(api: Garmin, new_activities: list[dict[str, Any]], result: dict[str, Any]) -> None:
    """For each new run, fetch and store laps + streams + km splits + best efforts."""
    fetched = 0
    for run in new_activities[:30]:  # cap per cycle to stay friendly with rate limits
        if _enrich_activity(api, run["id"], float(run.get("distance") or 0),
                            str(run.get("start_date_local") or "")):
            fetched += 1
        time.sleep(0.15)
    result["details_fetched"] = fetched
    print(f"[GARMIN] details fetched for {fetched} activities", file=sys.stderr)


def _build_streams(api: Garmin, activity_id: int) -> dict[str, Any] | None:
    """Fetch Garmin per-sample metrics and normalise them into the Strava
    key-by-type stream shape that db.upsert_streams expects:
      time, distance, heartrate, velocity_smooth, altitude, cadence, latlng.

    Returns None when the activity has no detailed metrics — e.g. a manual
    entry without GPS/HR samples.
    """
    details = _safe(lambda: api.get_activity_details(activity_id, 2000, 4000),
                    f"get_activity_details({activity_id})")
    if not isinstance(details, dict):
        return None
    descriptors = details.get("metricDescriptors") or []
    samples = details.get("activityDetailMetrics") or []
    if not descriptors or not samples:
        return None

    by_key: dict[str, int] = {}
    for d in descriptors:
        if isinstance(d, dict) and d.get("key") and d.get("metricsIndex") is not None:
            by_key.setdefault(d["key"], d["metricsIndex"])

    def series(*keys):
        """Column for the first descriptor key present, or None if none match."""
        i = next((by_key[k] for k in keys if k in by_key), None)
        if i is None:
            return None
        out = []
        for s in samples:
            m = s.get("metrics") if isinstance(s, dict) else None
            out.append(m[i] if isinstance(m, list) and i < len(m) else None)
        return out

    n = len(samples)
    # Time axis: prefer elapsed seconds; fall back to epoch-ms delta, then index.
    time_col = series("sumDuration", "sumElapsedDuration", "sumMovingDuration")
    if time_col is None or all(v is None for v in time_col):
        ts = series("directTimestamp")
        base = next((v for v in ts if v is not None), None) if ts else None
        if base is not None:
            time_col = [None if v is None else (v - base) / 1000.0 for v in ts]
        else:
            time_col = list(range(n))

    lat_col = series("directLatitude")
    lng_col = series("directLongitude")
    latlng = None
    if lat_col and lng_col:
        latlng = [
            [la, lo] if (la is not None and lo is not None) else None
            for la, lo in zip(lat_col, lng_col)
        ]

    cad_col = series("directDoubleCadence", "directRunCadence")
    if cad_col is not None:
        # Garmin reports double (both-feet) cadence; halve to match the
        # per-activity average_cadence convention used elsewhere.
        cad_col = [None if v is None else v / 2.0 for v in cad_col]

    known_descriptor_keys = {
        "sumDuration", "sumElapsedDuration", "sumMovingDuration", "directTimestamp",
        "directLatitude", "directLongitude", "directDoubleCadence", "directRunCadence",
        "sumDistance", "directHeartRate", "directSpeed", "directElevation",
        "directPower", "directTemperature", "directAirTemperature", "directMoving",
        "directGrade", "directVerticalSpeed", "directBodyBattery",
        "directFractionalCadence", "directGradeAdjustedSpeed",
        "directGroundContactTime", "directPerformanceCondition", "directStrideLength",
        "directVerticalOscillation", "directVerticalRatio", "sumAccumulatedPower",
        "directCorrectedElevation", "directUncorrectedElevation",
    }
    extra_metrics = []
    unknown_indexes = {
        key: index for key, index in by_key.items() if key not in known_descriptor_keys
    }
    for sample in samples:
        metrics = sample.get("metrics") if isinstance(sample, dict) else None
        extra = {}
        if isinstance(metrics, list):
            for key, index in unknown_indexes.items():
                if index < len(metrics) and metrics[index] is not None:
                    extra[key] = metrics[index]
        extra_metrics.append(extra or None)

    return {
        "time": {"data": time_col},
        "distance": {"data": series("sumDistance") or []},
        "heartrate": {"data": series("directHeartRate") or []},
        "velocity_smooth": {"data": series("directSpeed") or []},
        "altitude": {"data": series("directElevation") or []},
        "cadence": {"data": cad_col or []},
        "latlng": {"data": latlng or []},
        "watts": {"data": series("directPower") or []},
        "temperature": {"data": series("directTemperature", "directAirTemperature") or []},
        "moving": {"data": series("directMoving") or []},
        "grade_smooth": {"data": series("directGrade") or []},
        "vertical_speed": {"data": series("directVerticalSpeed") or []},
        "body_battery": {"data": series("directBodyBattery") or []},
        "fractional_cadence": {"data": series("directFractionalCadence") or []},
        "grade_adjusted_speed": {"data": series("directGradeAdjustedSpeed") or []},
        "ground_contact_time": {"data": series("directGroundContactTime") or []},
        "performance_condition": {"data": series("directPerformanceCondition") or []},
        "stride_length": {"data": series("directStrideLength") or []},
        "vertical_oscillation": {"data": series("directVerticalOscillation") or []},
        "vertical_ratio": {"data": series("directVerticalRatio") or []},
        "accumulated_power": {"data": series("sumAccumulatedPower") or []},
        "corrected_altitude": {"data": series("directCorrectedElevation") or []},
        "uncorrected_altitude": {"data": series("directUncorrectedElevation") or []},
        "garmin_metrics": {"data": extra_metrics},
    }


def hydrate_activity_streams(api: Garmin, activity_id: int) -> int:
    """Fetch + store Garmin streams for one activity (hydrate-on-miss path).

    Returns the number of stream points stored (0 when the activity has no
    detailed metrics — e.g. a treadmill run without GPS/HR samples).
    """
    streams = _build_streams(api, activity_id)
    if not streams:
        return 0
    try:
        db.upsert_streams(activity_id, streams)
    except Exception as exc:
        print(f"[GARMIN] upsert_streams({activity_id}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    n = len(streams["time"]["data"])
    print(f"[GARMIN] hydrated {n} stream points for activity {activity_id}", file=sys.stderr)
    return n


def get_or_hydrate_streams(activity_id: int, token_dir: str = ""):
    """Read streams from the DB; on a miss, fetch them from Garmin once and store.

    Garmin runs land without stream rows, so the first time a run detail is
    opened we hydrate from Garmin and persist (also replicated to the secondary
    DB). Subsequent reads are served straight from the DB. Shared by the
    self-hosted server and the Vercel app.
    """
    cached = db.get_streams(activity_id)
    if cached and cached.get("streams"):
        return cached
    try:
        api = load_garmin_api(token_dir)
        if api is not None:
            hydrate_activity_streams(api, activity_id)
            cached = db.get_streams(activity_id)
    except Exception as e:
        print(f"[streams] hydrate {activity_id} failed (non-fatal): {type(e).__name__}: {e}", file=sys.stderr)
    return cached



# ── Phase 3: VO2max / training status / gear ──

def _extract_vo2max(metrics: Any) -> float | None:
    """Garmin get_max_metrics returns a list (or dict). VO2max sits under
    generic.vo2MaxPreciseValue / vo2MaxValue. Raw shape is logged for tuning."""
    if not metrics:
        return None
    item = metrics[0] if isinstance(metrics, list) and metrics else metrics
    if not isinstance(item, dict):
        return None
    generic = item.get("generic") if isinstance(item.get("generic"), dict) else {}
    for key in ("vo2MaxPreciseValue", "vo2MaxValue"):
        if generic.get(key):
            return generic.get(key)
        if item.get(key):
            return item.get(key)
    return None


def _normalize_training_status(ts: Any) -> dict[str, Any]:
    """Pull a human status + load from Garmin's training-status payload.
    Shape varies across accounts/devices, so we keep the raw blob too."""
    out: dict[str, Any] = {"raw": ts, "status": None}
    try:
        latest = (ts or {}).get("mostRecentTrainingStatus") or {}
        data = latest.get("latestTrainingStatusData") or {}
        if isinstance(data, dict) and data:
            first = next(iter(data.values()))
            if isinstance(first, dict):
                out["status"] = (
                    first.get("trainingStatusFeedbackPhrase")
                    or first.get("trainingStatus")
                )
                out["fitnessTrend"] = first.get("fitnessTrend")
        load = (ts or {}).get("mostRecentTrainingLoadBalance") or {}
        load_map = load.get("metricsTrainingLoadBalanceDTOMap") if isinstance(load, dict) else None
        if isinstance(load_map, dict) and load_map:
            lb = next(iter(load_map.values()))
            if isinstance(lb, dict):
                out["acwr"] = lb.get("acuteTrainingLoad")
                out["trainingLoad"] = lb.get("trainingBalanceFeedbackPhrase")
    except Exception as exc:
        print(f"[GARMIN] normalize training_status failed: {exc}", file=sys.stderr)
    return out


def _gear_uuid(g: dict) -> Any:
    return g.get("uuid") or g.get("gearPk") or g.get("gearUuid")


def _garmin_user_id(api: Garmin) -> Any:
    """Resolve the Garmin user profile id, with socialProfile fallback."""
    prof = _safe(lambda: api.get_user_profile(), "get_user_profile") or {}
    if isinstance(prof, dict):
        uid = prof.get("id") or prof.get("userProfileId") or prof.get("profileId")
        if uid:
            return uid
    social = _safe(lambda: api.client.connectapi("/userprofile-service/socialProfile"), "socialProfile") or {}
    if isinstance(social, dict):
        return social.get("profileId") or social.get("id")
    return None


def _refresh_gear(api: Garmin) -> None:
    user_id = _garmin_user_id(api)
    if not user_id:
        print("[GARMIN] no user profile id for gear — skipping", file=sys.stderr)
        return
    gear_list = _safe(lambda: api.get_gear(user_id), "get_gear")
    print(f"[GARMIN] gear raw: {str(gear_list)[:600]}", file=sys.stderr)
    if not isinstance(gear_list, list):
        return
    mapped: list[dict[str, Any]] = []
    for g in gear_list:
        if not isinstance(g, dict):
            continue
        uuid = _gear_uuid(g)
        stats = _safe(lambda: api.get_gear_stats(uuid), "get_gear_stats") if uuid else None
        dist_m = 0
        if isinstance(stats, dict):
            dist_m = stats.get("totalDistance") or stats.get("totalDistanceMeters") or 0
        display = g.get("displayName") or g.get("customMakeModel") or "Garmin gear"
        # Prefix with 'g_' so db.upsert_gears routes it to the shoes table
        # (its bike branch triggers on ids starting with 'b').
        mapped.append({
            "id": "g_" + str(uuid or display),
            "name": display,
            "brand_name": g.get("gearMakeName") or "",
            "model_name": g.get("gearModelName") or "",
            "distance": dist_m,
            "retired": bool(g.get("dateEnd")),
        })
    if mapped:
        try:
            db.upsert_gears(mapped)
            print(f"[GARMIN] upserted {len(mapped)} gear items", file=sys.stderr)
        except Exception as exc:
            print(f"[GARMIN] upsert_gears failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _garmin_gear_assignments(api: Garmin, candidate_garmin_ids: set) -> dict:
    """Authoritative gear per run, straight from Garmin.

    For each active gear, ask Garmin which activities used it
    (api.get_gear_activities) and map those Garmin activityIds to the canonical
    gear id (the Strava id when the same shoe also exists on Strava, so Garmin
    and Strava runs group under one shoe). Best-effort — returns {} on any error.
    """
    out: dict = {}
    if api is None or not candidate_garmin_ids:
        return out
    user_id = _garmin_user_id(api)
    if not user_id:
        return out
    gear_list = _safe(lambda: api.get_gear(user_id), "get_gear")
    if not isinstance(gear_list, list):
        return out
    canon = db.get_canonical_gear_map()
    for g in gear_list:
        if not isinstance(g, dict) or g.get("dateEnd"):  # skip retired gear
            continue
        uuid = _gear_uuid(g)
        if not uuid:
            continue
        name = (g.get("displayName") or g.get("customMakeModel") or "").strip().lower()
        canonical = canon.get(name) or ("g_" + str(uuid))
        acts = _safe(lambda: api.get_gear_activities(uuid), f"get_gear_activities[{uuid}]")
        if not isinstance(acts, list):
            continue
        for a in acts:
            aid = a.get("activityId") if isinstance(a, dict) else None
            if aid is not None and str(aid) in candidate_garmin_ids:
                out[str(aid)] = canonical
    return out


def _standalone_ungeared_runs(rows: list[dict], since: str) -> list[dict]:
    """Recent ungeared runs that are not already represented by a geared twin."""
    geared_timestamps = {
        r.get("sdl") for r in rows if r.get("gear_id") and r.get("sdl")
    }
    return [
        r
        for r in rows
        if not r.get("gear_id")
        and (r.get("sdl") or "")[:10] >= since
        and (r.get("sdl") or "") not in geared_timestamps
    ]


def _reconcile_run_gear(api: Garmin, since_days: int = 120) -> int:
    """Attach the right shoe to recent runs that have no gear_id.

    Garmin imports land with gear_id=None (the activity payload carries no gear),
    so without this the *current* shoe's mileage is split between its Strava-
    geared runs and its Garmin-only runs. We fill the gap for recent runs only
    (the discipline tag is unreliable on old history) in priority order:
      1. Garmin-authoritative (get_gear_activities),
      2. same-time Strava twin,
      3. active shoe of the period.
    Only changed rows are written (and replicated). Returns runs updated.
    """
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    try:
        # Cheap COUNT guard first: in steady state there is nothing to
        # reconcile, so don't pull the full run timeline every 15-min cycle.
        if db.count_ungeared_runs_since(since) == 0:
            print("[GARMIN] gear reconcile: no recent ungeared runs", file=sys.stderr)
            return 0
        rows = db.get_run_gear_rows()
    except Exception as exc:
        print(f"[GARMIN] gear reconcile: read failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0

    recent_ungeared = [
        r for r in rows if not r.get("gear_id") and (r.get("sdl") or "")[:10] >= since
    ]
    if not recent_ungeared:
        print("[GARMIN] gear reconcile: no recent ungeared runs", file=sys.stderr)
        return 0
    print(
        f"[GARMIN] gear reconcile: {len(recent_ungeared)} recent ungeared runs (since {since})",
        file=sys.stderr,
    )
    standalone_ungeared = _standalone_ungeared_runs(rows, since)
    skipped_twins = len(recent_ungeared) - len(standalone_ungeared)
    if skipped_twins:
        print(
            f"[GARMIN] gear reconcile: {skipped_twins} geared twins intentionally ignored",
            file=sys.stderr,
        )
    if not standalone_ungeared:
        return 0

    cur_gear = {str(r["id"]): r.get("gear_id") for r in rows}
    candidate_gids = {
        str(r.get("garmin_activity_id"))
        for r in standalone_ungeared
        if r.get("garmin_activity_id")
    }

    assignments: dict = {}
    # Pass 1 — Garmin-authoritative (best-effort, keyed by Garmin activityId).
    try:
        garmin_map = _garmin_gear_assignments(api, candidate_gids)
        for r in standalone_ungeared:
            gaid = str(r.get("garmin_activity_id") or "")
            if gaid and gaid in garmin_map:
                assignments[str(r["id"])] = garmin_map[gaid]
        if garmin_map:
            print(f"[GARMIN] gear reconcile: {len(garmin_map)} matched via Garmin gear API", file=sys.stderr)
    except Exception as exc:
        print(f"[GARMIN] gear reconcile: Garmin pass failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    # Pass 2+3 — same-time twin / active shoe of the period (fills the rest).
    for aid, gid in db.compute_gear_assignments(rows, since=since).items():
        assignments.setdefault(str(aid), gid)

    changed = 0
    for aid, gid in assignments.items():
        if gid and cur_gear.get(aid) != gid:
            try:
                db.update_activity_gear(aid, gid)
                changed += 1
                print(f"[GARMIN] gear reconcile: activity {aid} -> {gid}", file=sys.stderr)
            except Exception as exc:
                print(f"[GARMIN] gear reconcile: update {aid} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"[GARMIN] gear reconcile: {changed} runs updated", file=sys.stderr)
    return changed


def _refresh_metrics(api: Garmin, result: dict[str, Any]) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    # VO2max
    metrics = _safe(lambda: api.get_max_metrics(today), "get_max_metrics")
    print(f"[GARMIN] max_metrics raw: {str(metrics)[:600]}", file=sys.stderr)
    vo2 = _extract_vo2max(metrics)
    if vo2:
        try:
            db.upsert_vo2max(today, vo2)
            result["vo2max"] = vo2
            print(f"[GARMIN] VO2max {today} = {vo2}", file=sys.stderr)
        except Exception as exc:
            print(f"[GARMIN] upsert_vo2max failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    # Sleep history. Garmin dates sleep by wake-up day; refreshing today and
    # yesterday picks up late-finalized scores without treating yesterday's
    # wake-up score as today's night.
    sleep_added = 0
    current_day = datetime.now().date()
    for day in (
        (current_day - timedelta(days=1)).isoformat(),
        current_day.isoformat(),
    ):
        payload = _safe(lambda day=day: api.get_sleep_data(day), f"get_sleep_data({day})")
        sleep = extract_sleep_snapshot(payload)
        if not sleep:
            continue
        sleep_day = sleep.get("health_sleep_date") or sleep.get("date") or day
        try:
            if db.upsert_sleep_score(
                str(sleep_day)[:10],
                sleep.get("health_sleep_score"),
                sleep.get("health_sleep_quality"),
                sleep.get("health_sleep_duration_seconds"),
            ):
                sleep_added += 1
                print(
                    f"[GARMIN] sleep_history {sleep_day}: "
                    f"score={sleep.get('health_sleep_score')} "
                    f"quality={sleep.get('health_sleep_quality')} "
                    f"duration={sleep.get('health_sleep_duration_seconds')}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"[GARMIN] upsert_sleep_score failed for {sleep_day}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if sleep_added:
        result["sleep_history"] = sleep_added
    # Training status
    ts = _safe(lambda: api.get_training_status(today), "get_training_status")
    print(f"[GARMIN] training_status raw: {str(ts)[:600]}", file=sys.stderr)
    if ts:
        try:
            db.set_sync_meta("training_status", _normalize_training_status(ts))
            result["training_status"] = True
        except Exception as exc:
            print(f"[GARMIN] store training_status failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    # Gear
    _refresh_gear(api)
    # Attach the right shoe to recent runs Garmin imported without a gear_id.
    try:
        _reconcile_run_gear(api)
    except Exception as exc:
        print(f"[GARMIN] gear reconcile failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)


_WEATHER_HELPERS: tuple | None = None


def _load_weather_helpers() -> tuple:
    """Charge fetch_weather/parse_start depuis scripts/weather_for_run.py.

    Source unique de la logique météo Open-Meteo (mêmes API, paramètres,
    sélection archive/forecast, heure locale) — chargée par chemin pour ne pas
    polluer sys.path avec le dossier scripts/."""
    global _WEATHER_HELPERS
    if _WEATHER_HELPERS is None:
        import importlib.util
        path = Path(__file__).resolve().parent / "scripts" / "weather_for_run.py"
        spec = importlib.util.spec_from_file_location("weather_for_run", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WEATHER_HELPERS = (mod.fetch_weather, mod.parse_start)
    return _WEATHER_HELPERS


def _sync_run_weather(result: dict[str, Any]) -> None:
    """Pose la météo Open-Meteo des runs GPS récents qui n'en ont pas.

    Tourne à chaque freshness check : couvre les runs tout juste importés
    (météo présente dès la réplication vers Neon/local via le writer canonique
    db.upsert_activity_weather) et rattrape ceux arrivés sans météo (échec
    ponctuel, réplication depuis un déploiement plus ancien). Non-fatal."""
    runs = db.get_recent_runs_missing_weather(days=14, limit=8)
    if not runs:
        return
    fetch_weather, parse_start = _load_weather_helpers()
    added = 0
    for run in runs:
        try:
            date_iso, hour, _minute = parse_start(run["start_date_local"])
            weather = fetch_weather(
                float(run["start_lat"]), float(run["start_lng"]), date_iso, hour
            )
            weather["source"] = f"open-meteo-{weather.get('endpoint', 'forecast')}"
            db.upsert_activity_weather(int(run["id"]), weather)
            added += 1
            print(
                f"[GARMIN] weather set for run {run['id']} ({date_iso} {hour:02d}h): "
                f"{weather.get('temperature_2m')}°C code={weather.get('weather_code')}",
                file=sys.stderr,
            )
            time.sleep(0.15)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[GARMIN] weather fetch failed for run {run.get('id')} (non-fatal): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    if added:
        result["weather_added"] = added


def _sync_run_health(
    api: Garmin, result: dict[str, Any], runs: list[dict[str, Any]] | None = None
) -> None:
    """Attach Garmin sleep/HRV/resting-HR snapshots to recent runs."""
    targets = runs or db.get_recent_runs_missing_health(days=14, limit=8)
    if not targets:
        return
    cache: dict[tuple[str, str], Any] = {}
    added = 0
    missing = 0

    def on_error(method: str, day: str, exc: Exception) -> None:
        print(
            f"[GARMIN] health {method}({day}) failed (non-fatal): "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    for run in targets[:30]:
        snapshot = fetch_run_health_snapshot(api, run, cache, on_error=on_error)
        try:
            if db.upsert_activity_health(int(run["id"]), snapshot):
                added += 1
                print(
                    f"[GARMIN] health set for run {run['id']}: "
                    f"sleep={snapshot.get('health_sleep_score', '-')} "
                    f"hrv={snapshot.get('health_hrv_last_night_avg_ms', '-')} "
                    f"rhr={snapshot.get('health_resting_hr_bpm', '-')}",
                    file=sys.stderr,
                )
            else:
                missing += 1
        except Exception as exc:
            missing += 1
            print(
                f"[GARMIN] health upsert failed for run {run.get('id')} "
                f"(non-fatal): {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        time.sleep(0.1)

    if added:
        result["health_added"] = added
    if missing:
        result["health_missing"] = missing


def check_and_populate(token_dir: str = "") -> dict[str, Any]:
    global GARMIN_TOKEN_DIR
    if token_dir:
        GARMIN_TOKEN_DIR = token_dir

    result: dict[str, Any] = {
        "added": 0,
        "skipped": None,
        "checked": False,
        "details_fetched": 0,
        "source": "garmin",
    }

    api = load_garmin_api(token_dir)
    if api is None:
        result["skipped"] = "garmin_not_authenticated"
        result["reauth_required"] = True
        print("[GARMIN] not authenticated — configure Garmin tokens first", file=sys.stderr)
        return result

    try:
        latest_date = db.get_latest_activity_date()
    except Exception as exc:
        print(f"[GARMIN] db.get_latest_activity_date failed: {exc}", file=sys.stderr)
        result["skipped"] = f"db_error:{type(exc).__name__}"
        return result

    if latest_date:
        try:
            latest_dt = datetime.fromisoformat(
                str(latest_date).replace("Z", "").replace(" ", "T")
            )
            after_dt = (latest_dt - timedelta(days=7)).replace(tzinfo=None)
        except Exception:
            after_dt = datetime.now() - timedelta(days=30)
    else:
        after_dt = datetime.now() - timedelta(days=90)

    result["checked"] = True
    result["after_iso"] = after_dt.isoformat()
    result["latest"] = latest_date
    print(f"[GARMIN] looking for activities after {after_dt.isoformat()}", file=sys.stderr)

    try:
        known_ids = set(db.get_all_activity_ids())
    except Exception as exc:
        print(f"[GARMIN] get_all_activity_ids failed: {exc}", file=sys.stderr)
        known_ids = set()

    tombstone_ids = db.get_activity_tombstone_ids()

    try:
        existing_recent = _parse_existing_starts(
            db.get_activities_start_dates_since(after_dt.isoformat())
        )
    except Exception:
        existing_recent = []

    new_activities: list[dict[str, Any]] = []
    recent_known_activities: list[dict[str, Any]] = []
    total_garmin_runs = 0
    try:
        offset = 0
        per_page = 100
        while True:
            page = api.get_activities(start=offset, limit=per_page)
            if not isinstance(page, list) or not page:
                break

            page_dated_runs = 0
            page_has_recent = False
            for raw in page:
                run = _extract_run(raw)
                if run is None:
                    continue
                total_garmin_runs += 1
                if _is_tombstoned_run(run, tombstone_ids):
                    print(
                        f"[GARMIN] run {run['id']} ignoré "
                        "(supprimé par l'utilisateur)",
                        file=sys.stderr,
                    )
                    continue

                try:
                    run_dt = datetime.fromisoformat(
                        run["start_date_local"].replace("Z", "")
                    ).replace(tzinfo=None)
                except Exception:
                    run_dt = None
                if run_dt is not None:
                    page_dated_runs += 1
                    if run_dt < after_dt:
                        continue
                    page_has_recent = True

                if run["id"] in known_ids:
                    recent_known_activities.append(run)
                    continue

                if _is_duplicate(run, existing_recent):
                    print(
                        f"[GARMIN] duplicate skipped: id={run['id']} "
                        f"start={run['start_date_local']} dist={run['distance']:.0f}m",
                        file=sys.stderr,
                    )
                    continue

                new_activities.append(run)
                print(
                    f"[GARMIN] new run: id={run['id']} name={run['name']!r} "
                    f"start={run['start_date_local']} dist={run['distance']:.0f}m",
                    file=sys.stderr,
                )

            # Garmin serves activities newest-first (the 500 cap below already
            # relies on it): once a whole page of dated runs predates after_dt,
            # every later page does too — no point fetching them.
            if page_dated_runs > 0 and not page_has_recent:
                break
            if len(page) < per_page:
                break
            offset += per_page
            if offset >= 500:
                break
            time.sleep(0.15)
    except GarminConnectAuthenticationError as exc:
        print(f"[GARMIN] activity fetch auth failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result["skipped"] = "garmin_authentication_failed"
        result["reauth_required"] = True
        _save_api_tokens(api, token_dir)
        return result
    except Exception as exc:
        print(f"[GARMIN] activity fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result["skipped"] = f"garmin_error:{type(exc).__name__}"
        _save_api_tokens(api, token_dir)
        return result

    _save_api_tokens(api, token_dir)

    # Refresh run-specific summary metrics for recent known Garmin activities.
    # The DB helper compares the retained raw summary first, so unchanged runs
    # do not churn sync markers or trigger needless replica traffic.
    if recent_known_activities:
        try:
            result["summaries_refreshed"] = db.upsert_garmin_run_summaries(
                recent_known_activities
            )
        except Exception as exc:
            print(f"[GARMIN] summary refresh failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    # Phase 3: refresh Garmin-native metrics (VO2max, training status, gear)
    # on every check, even when there are no new runs.
    try:
        _refresh_metrics(api, result)
    except Exception as exc:
        print(f"[GARMIN] metrics refresh failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    # Retry granular enrichment for recent Garmin runs whose laps/splits/streams
    # are still empty: Garmin can return empty details right after an upload,
    # so the import-time fetch alone is not enough. Runs on every check.
    try:
        missing = db.get_recent_garmin_activities_missing_details(days=14)
        if missing:
            print(f"[GARMIN] retrying granular details for {len(missing)} recent runs: "
                  f"{[m['id'] for m in missing]}", file=sys.stderr)
            retried = 0
            for m in missing[:10]:  # cap per cycle
                if _enrich_activity(api, m["id"], float(m.get("distance") or 0),
                                    str(m.get("start_date_local") or "")):
                    retried += 1
                time.sleep(0.15)
            result["details_retried"] = retried
    except Exception as exc:
        print(f"[GARMIN] details retry failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    result["garmin_returned"] = total_garmin_runs
    result["garmin_runs"] = len(new_activities)
    result["new_after_dedup"] = len(new_activities)

    if not new_activities:
        print("[GARMIN] no new activities", file=sys.stderr)
        # Rattrapage météo même sans nouveau run (run arrivé par réplication
        # sans météo, échec Open-Meteo au check précédent...).
        try:
            _sync_run_weather(result)
        except Exception as exc:
            print(f"[GARMIN] weather sync failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            _sync_run_health(api, result)
        except Exception as exc:
            print(f"[GARMIN] health sync failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)
        return result

    try:
        db.upsert_activities(new_activities)
        result["added"] = len(new_activities)
        print(f"[GARMIN] inserted {len(new_activities)} activities", file=sys.stderr)
    except Exception as exc:
        print(f"[GARMIN] upsert failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result["skipped"] = f"db_error:{type(exc).__name__}"
        return result

    # Météo des runs fraîchement insérés : posée en primaire puis répliquée,
    # pour que chaque nouveau run parte vers Neon avec sa météo.
    try:
        _sync_run_weather(result)
    except Exception as exc:
        print(f"[GARMIN] weather sync failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    # Health/fatigue snapshot at run end: sleep, HRV and resting heart rate.
    try:
        _sync_run_health(api, result, new_activities)
    except Exception as exc:
        print(f"[GARMIN] health sync failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    # Phase 3: fetch splits per new activity and compute best efforts (Records).
    try:
        _fetch_details(api, new_activities, result)
    except Exception as exc:
        print(f"[GARMIN] details fetch failed (non-fatal): {type(exc).__name__}: {exc}", file=sys.stderr)

    return result
