#!/usr/bin/env python3
"""Genere le journal coach marathon : 10 derniers runs analyses + projection des prochaines seances.

Lit le dump pg_dump local (.runtime/local-db/bdd_runs.sql), n'effectue AUCUN acces reseau.
Sortie : un fichier markdown destine a etre pousse vers iCloud Drive et requete depuis l'iPhone
(via un Projet Claude / piece jointe).

Usage:
  python3 scripts/coach_journal.py [--dump PATH] [--out PATH] [--json PATH] [--date YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys


def fmt_record(seconds, km):
    """'45:00 (4:30/km)', ou '' si le record n'est pas connu."""
    if not seconds:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    pace = int(round(total / km))
    return f"{clock} ({pace // 60}:{pace % 60:02d}/km)"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runner_profile import PROFILE as RUNNER  # noqa: E402

# Le profil du coureur vient de `runner_profile` : objectif, FC max de repli et
# allures y sont deja resolus (fichier du coureur, environnement, ou snapshot
# Garmin). Les records et les references cardiaques sont ensuite RELUS dans le
# dump a chaque generation par profile_for() — figer un record ici rendrait le
# coach aveugle a un meilleur effort et le ferait calibrer sur un chrono perime.
PROFILE = {
    "pr_5k": fmt_record(RUNNER.records.get("5k"), 5.0),
    "pr_10k": fmt_record(RUNNER.records.get("10k"), 10.0),
    "pr_semi": fmt_record(RUNNER.records.get("semi"), 21.0975),
    "pr_marathon": fmt_record(RUNNER.records.get("marathon"), 42.195),
    "objectif": (
        f"{RUNNER.race_name}, {RUNNER.race_date.isoformat()} — calibrage "
        f"{RUNNER.goal_label} ({RUNNER.pace('marathon')})"
    ),
    # La FC facile n'est qu'un repli : easy_hr_reference() la relit dans le dump.
    "fc_facile": int(round(RUNNER.max_hr * 0.78)),
    "fc_max": RUNNER.max_hr,
}

# Zones d'allure (min/km), derivees de l'objectif : une table recopiee ici
# divergerait de celle du site des le premier ajustement d'objectif.
ZONES = {
    label: (int(round(RUNNER.paces[key][0])), int(round(RUNNER.paces[key][1])))
    for label, key in (
        ("Recuperation", "recovery"),
        ("Footing facile", "easy"),
        ("Endurance moyenne", "steady"),
        ("Allure marathon", "marathon"),
        ("Allure semi", "semi"),
        ("Seuil", "threshold"),
        ("VO2max", "vo2"),
        ("Lignes", "strides"),
    )
}


def parse_copy(path, table, activity_ids=None):
    rows, cols, inblk = [], [], False
    accepted_ids = ({str(activity_id) for activity_id in activity_ids}
                    if activity_ids is not None else None)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not inblk:
                if line.startswith(f"COPY public.{table} ("):
                    cols = line.split("(", 1)[1].split(")", 1)[0].split(", ")
                    inblk = True
                continue
            if line.startswith("\\."):
                break
            row = dict(zip(cols, line.rstrip("\n").split("\t")))
            if accepted_ids is None or row.get("activity_id") in accepted_ids:
                rows.append(row)
    return rows


_COPY_UNESCAPES = (("\\t", "\t"), ("\\n", "\n"), ("\\r", "\r"), ("\\\\", "\\"))


def unescape_copy_value(value):
    """Decode une valeur du format texte de COPY (backslash-escapes)."""
    if not value:
        return value
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            pair = value[i:i + 2]
            replacement = next((rep for esc, rep in _COPY_UNESCAPES if esc == pair), None)
            if replacement is not None:
                out.append(replacement)
                i += 2
                continue
        out.append(value[i])
        i += 1
    return "".join(out)


def parse_plan_overrides(path):
    """Ajustements du coach presents dans le dump, prets pour set_plan_overrides."""
    overrides = {}
    for row in parse_copy(path, "plan_overrides"):
        day = (row.get("day") or "").strip()
        payload = row.get("payload")
        if not day or not payload or payload == "\\N":
            continue
        try:
            session = json.loads(unescape_copy_value(payload))
        except (TypeError, ValueError):
            print(f"[coach-journal] plan_overrides[{day}] illisible, ignore", file=sys.stderr)
            continue
        note = row.get("note")
        overrides[day] = {
            "session": session,
            "note": None if note in (None, "", "\\N") else unescape_copy_value(note),
        }
    return overrides


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _toint(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def dump_source_info(path):
    """Provenance du dump, pour qu'une date de generation ne masque pas sa fraicheur."""
    display_path = os.path.normpath(os.fspath(path))
    absolute = os.path.abspath(display_path)
    try:
        stat = os.stat(absolute)
    except OSError:
        return {
            "path": display_path,
            "exists": False,
            "modified_at": None,
            "age_seconds": None,
        }

    modified = dt.datetime.fromtimestamp(stat.st_mtime).astimezone()
    now = dt.datetime.now().astimezone()
    return {
        "path": display_path,
        "exists": True,
        "modified_at": modified.isoformat(timespec="seconds"),
        "age_seconds": max(0, int((now - modified).total_seconds())),
    }


# Les quatre distances que garmin_freshness recalcule : un record relu dans le
# dump prime toujours sur ce que le profil du coureur annonce.
PROFILE_PR_KEYS = {
    "5K": "pr_5k",
    "10K": "pr_10k",
    "Half-Marathon": "pr_semi",
    "Marathon": "pr_marathon",
}

EASY_HR_WINDOW_DAYS = 42
EASY_HR_MIN_DURATION_SECONDS = 25 * 60
EASY_HR_PACE_MIN_SECONDS = 5 * 60 + 20
EASY_HR_PACE_MAX_SECONDS = 5 * 60 + 40
EASY_HR_MIN_SAMPLES = 3
EASY_HR_MAX_AVERAGE_FOR_BASELINE = 151
_QUALITY_NAME_RE = re.compile(
    r"(?:\bfraction|\bseuil|\bvo2|\binterval|\btempo|\ballure marathon|\bam\b|\b\d+\s*x\s*\d+)",
    re.IGNORECASE,
)


def easy_hr_reference(
    run_rows,
    as_of,
    *,
    default=PROFILE["fc_facile"],
    structured_quality_ids=None,
):
    """Baseline FC facile robuste, sans confondre qualite et endurance.

    On retient les moyennes cardiaques des runs d'au moins 25 minutes dont
    l'allure moyenne est dans la bande facile 5:20-5:40/km, puis leur mediane.
    Moins de trois observations ne suffisent pas a remplacer le repli connu :
    la mediane observee reste exposee pour rendre ce choix explicable.
    """
    try:
        if isinstance(as_of, dt.datetime):
            day = as_of.date()
        elif isinstance(as_of, dt.date):
            day = as_of
        else:
            day = dt.date.fromisoformat(str(as_of)[:10])
    except (TypeError, ValueError):
        day = None

    structured_quality_ids = {str(value) for value in (structured_quality_ids or set())}
    samples = []
    excluded_quality_count = 0
    if day is not None:
        for row in run_rows or []:
            if row.get("type") not in (None, "Run"):
                continue
            stamp_value = row.get("date") or row.get("start_date_local")
            try:
                stamp = dt.date.fromisoformat(str(stamp_value)[:10])
            except (TypeError, ValueError):
                continue
            age = (day - stamp).days
            if not 0 <= age <= EASY_HR_WINDOW_DAYS:
                continue

            seconds = fnum(row.get("moving_time"))
            distance_m = fnum(row.get("distance")) or fnum(row.get("distance_m"))
            if distance_m is None and fnum(row.get("distance_km")) is not None:
                distance_m = fnum(row.get("distance_km")) * 1000
            average_hr = fnum(row.get("average_heartrate"))
            if not seconds or seconds < EASY_HR_MIN_DURATION_SECONDS or not distance_m or distance_m <= 0:
                continue
            if average_hr is None or not 30 <= average_hr <= 250:
                continue

            pace = seconds / (distance_m / 1000.0)
            if not EASY_HR_PACE_MIN_SECONDS <= pace <= EASY_HR_PACE_MAX_SECONDS:
                continue
            # Une qualite peut afficher 5:20-5:40/km sur la moyenne globale a
            # cause de l'echauffement et des recuperations. Son nom structure ou
            # une FC moyenne deja soutenue l'excluent de la baseline facile.
            is_structured_quality = str(row.get("id")) in structured_quality_ids
            if (
                is_structured_quality
                or _QUALITY_NAME_RE.search(str(row.get("name") or ""))
                or average_hr > EASY_HR_MAX_AVERAGE_FOR_BASELINE
            ):
                excluded_quality_count += 1
                continue
            samples.append(average_hr)

    observed_median = statistics.median(samples) if samples else None
    enough_samples = len(samples) >= EASY_HR_MIN_SAMPLES
    value = int(round(observed_median)) if enough_samples else int(default)
    return {
        "value": value,
        "source": "observed_median_42d" if enough_samples else "fallback",
        "observedMedian": round(observed_median, 1) if observed_median is not None else None,
        "sampleCount": len(samples),
        "minimumSamples": EASY_HR_MIN_SAMPLES,
        "windowDays": EASY_HR_WINDOW_DAYS,
        "minimumDurationMinutes": EASY_HR_MIN_DURATION_SECONDS // 60,
        "paceRangeSecPerKm": [EASY_HR_PACE_MIN_SECONDS, EASY_HR_PACE_MAX_SECONDS],
        "maximumAverageHr": EASY_HR_MAX_AVERAGE_FOR_BASELINE,
        "excludedQualityCount": excluded_quality_count,
        "qualityDetection": "laps_then_name_and_average_hr",
        "fallbackReason": None if enough_samples else "insufficient_samples",
    }


def _structured_quality_activity_ids(dump_path, run_rows, as_of):
    """Runs a intervalles prouves par leurs laps, meme avec un nom Garmin generique."""
    from daily_training_plan import _interval_structure

    try:
        day = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
    except (TypeError, ValueError):
        return set()

    candidates = []
    for row in run_rows:
        try:
            stamp = dt.date.fromisoformat(str(row.get("start_date_local") or row.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        if 0 <= (day - stamp).days <= EASY_HR_WINDOW_DAYS and row.get("id") not in (None, "", "\\N"):
            candidates.append(row)

    candidate_ids = {str(row["id"]) for row in candidates}
    if not candidate_ids:
        return set()

    laps_by_activity = {}
    for lap in parse_copy(dump_path, "activity_laps", candidate_ids):
        laps_by_activity.setdefault(str(lap.get("activity_id")), []).append(lap)

    quality_ids = set()
    for row in candidates:
        activity_id = str(row["id"])
        seconds = fnum(row.get("moving_time")) or 0
        distance_m = fnum(row.get("distance")) or 0
        interval_run = {
            # Laisser l'id vide evite de polluer le cache et les logs du moteur.
            "id": None,
            "date": str(row.get("start_date_local") or "")[:10],
            "moving_time": seconds,
            "distance_m": distance_m,
            "distance_km": distance_m / 1000 if distance_m else 0,
            "pace_sec_per_km": seconds / (distance_m / 1000) if seconds and distance_m else 0,
            "average_heartrate": fnum(row.get("average_heartrate")) or 0,
            "max_heartrate": fnum(row.get("max_heartrate")) or 0,
            "laps": planner_laps(laps_by_activity.get(activity_id, [])),
        }
        if _interval_structure(interval_run)[0]:
            quality_ids.add(activity_id)
    return quality_ids


def profile_for(dump_path, as_of=None):
    """PROFILE avec records et references cardiaques relus dans le dump.

    Meme regle de lecture que le site (database_pg.get_computed_bests_bulk) :
    seules les activites 'Run' comptent, et une fenetre trop descendante est
    ecartee via MAX_NET_DROP_PER_KM — sinon un chrono en descente deviendrait un
    record ici alors que la page Records le refuse, et les deux chemins coach
    diraient a nouveau deux choses differentes.

    Dump illisible ou table absente : on garde les valeurs de PROFILE. Un record
    perime vaut mieux qu'un plantage de la generation matinale.
    """
    from best_effort_rules import EFFORT_TARGET_METERS, is_downhill_assisted
    from heart_rate_reference import max_hr_reference

    try:
        activity_rows = parse_copy(dump_path, "activities")
        efforts = parse_copy(dump_path, "activity_best_efforts")
    except OSError as exc:
        print(f"[coach-journal] records illisibles ({exc}), PROFILE fige conserve", file=sys.stderr)
        activity_rows, efforts = [], []

    run_rows = [row for row in activity_rows if row.get("type") == "Run"]
    run_ids = {row.get("id") for row in run_rows}

    best = {}
    for row in efforts:
        name = row.get("name")
        if name not in PROFILE_PR_KEYS or row.get("activity_id") not in run_ids:
            continue
        seconds = _toint(row.get("moving_time")) or _toint(row.get("elapsed_time"))
        if not seconds:
            continue
        if is_downhill_assisted(fnum(row.get("elevation_delta")), fnum(row.get("distance"))):
            continue
        if name not in best or seconds < best[name]:
            best[name] = seconds

    profile = dict(PROFILE)
    for name, seconds in best.items():
        km = EFFORT_TARGET_METERS[name] / 1000.0
        profile[PROFILE_PR_KEYS[name]] = f"{fmt_duration(seconds)} ({fmt_pace(seconds / km)})"
        if PROFILE[PROFILE_PR_KEYS[name]] and profile[PROFILE_PR_KEYS[name]] != PROFILE[PROFILE_PR_KEYS[name]]:
            print(
                f"[coach-journal] {name} relu dans le dump : "
                f"{PROFILE[PROFILE_PR_KEYS[name]]} -> {profile[PROFILE_PR_KEYS[name]]}",
                file=sys.stderr,
            )

    reference_day = as_of or dt.date.today()
    max_hr = max_hr_reference(
        run_rows,
        reference_day.isoformat() if isinstance(reference_day, dt.date) else str(reference_day),
        default=PROFILE["fc_max"],
    )
    structured_quality_ids = _structured_quality_activity_ids(dump_path, run_rows, reference_day)
    easy_hr = easy_hr_reference(
        run_rows,
        reference_day,
        structured_quality_ids=structured_quality_ids,
    )
    profile["fc_max"] = int(round(max_hr["value"]))
    profile["fc_max_reference"] = max_hr
    profile["fc_facile"] = easy_hr["value"]
    profile["fc_facile_reference"] = easy_hr
    return profile


def fmt_duration(seconds):
    """45:00 pour un 10K, 1:38:07 pour un semi."""
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(sec_per_km):
    if not sec_per_km or sec_per_km <= 0:
        return "-"
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}/km"


def rolling_run_volume(runs, today, days=7):
    """Distance des `days` dates glissantes, aujourd'hui inclus.

    Pour 7 jours le delta valide est 0..6. L'ancienne condition `<= 7`
    couvrait huit dates et affichait 70,9 km le 17 aout au lieu de 60,7 km.
    """
    return sum(
        run["dist"]
        for run in runs
        if 0 <= (today - run["dt"].date()).days < days
    )


def render_guidance_session(guidance):
    title = guidance.get("title") or "Seance coach"
    session = guidance.get("session") or {}
    main = session.get("main") or "-"
    return f"{title} : {main}"


# Fenetre des activites hors course remontees au coach. 21 jours : assez large
# pour couvrir un week-end de rando ou de ski en debut de bloc, assez court pour
# que le snapshot reste une photo de la charge actuelle.
CROSS_TRAINING_WINDOW_DAYS = 21

# Libelles lisibles pour la categorie stockee en base (activities.type).
CROSS_TRAINING_LABELS = {
    "Hike": "Randonnee",
    "Walk": "Marche",
    "Ride": "Velo",
    "Swim": "Natation",
    "Ski": "Ski",
    "Rowing": "Rame / pagaie",
    "RockClimbing": "Escalade",
    "WeightTraining": "Renforcement",
    "Workout": "Seance croisee",
    "Other": "Autre",
}


def cross_training_payload(activity):
    """Une activite hors course, reduite a ce qui pese sur la fraicheur."""
    duration = _toint(activity.get("moving_time")) or _toint(activity.get("elapsed_time")) or 0
    distance_m = fnum(activity.get("distance")) or 0
    kind = activity.get("type") or "Other"
    minutes = round(duration / 60)
    # Les runs s'affichent en mm:ss ; une rando de 3 h donnerait "191:43", illisible.
    duree = f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"
    return {
        "date": activity["_dt"].date().isoformat(),
        "type": kind,
        "sport": CROSS_TRAINING_LABELS.get(kind, kind),
        "sport_garmin": activity.get("garmin_type_key") or None,
        "nom": activity.get("name") or CROSS_TRAINING_LABELS.get(kind, kind),
        "distance_km": round(distance_m / 1000.0, 2),
        "duree": duree,
        "duree_minutes": minutes,
        "denivele_m": _toint(activity.get("total_elevation_gain")) or 0,
        "fc_moy": _toint(activity.get("average_heartrate")),
    }


def planner_run_payload(run):
    payload = {
        "id": f"{run['dt'].date().isoformat()}-{run['name'] or 'run'}",
        "date": run["dt"].date().isoformat(),
        "start_date_local": run["dt"].isoformat(),
        "distance_km": round(run["dist"], 2),
        "distance_m": round(run["dist"] * 1000, 1),
        "moving_time": run["mt"],
        "pace_sec_per_km": run["pace"],
        "average_heartrate": _toint(run["hr"]),
        "max_heartrate": _toint(run["mhr"]),
    }
    # Les laps permettent au plan d'identifier les blocs de travail et les
    # seances clees deja couvertes.
    if run.get("laps"):
        payload["laps"] = run["laps"]
    return payload


def planner_laps(laps):
    """Laps du dump SQL, normalises pour l'adaptation du plan."""
    out = []
    for index, lap in enumerate(sorted(laps, key=lambda l: _toint(l.get("lap_index")) or 0)):
        seconds = _toint(lap.get("moving_time")) or _toint(lap.get("elapsed_time")) or 0
        meters = fnum(lap.get("distance")) or 0
        if seconds <= 0 or meters <= 0:
            continue
        out.append({
            "lap_index": _toint(lap.get("lap_index")) or index,
            "moving_time": seconds,
            "distance_m": meters,
            "average_heartrate": fnum(lap.get("average_heartrate")),
            "max_heartrate": fnum(lap.get("max_heartrate")),
        })
    return out


def classify(name, pace, laps, detected_fast=None):
    n = (name or "").lower()
    has_fast_lap = any((1000 / s) < 255 for s in laps if s > 0)  # < 4:15/km
    if (any(k in n for k in ["x", "fraction", "seuil", "vo2", "1000", "800", "500", "400", "2000", "1500"])
            or has_fast_lap or bool(detected_fast)):
        return "Qualite"
    return "Footing"


def _percentile(values, ratio):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * ratio)]


def _stream_points(rows):
    """Return usable, monotonic time/distance samples from an activity stream."""
    points = []
    for row in rows:
        time_sec = fnum(row.get("time_sec"))
        distance = fnum(row.get("distance"))
        if time_sec is None or distance is None:
            continue
        point = {
            "time": time_sec,
            "distance": distance,
            "heartrate": fnum(row.get("heartrate")),
        }
        if points and (time_sec <= points[-1]["time"] or distance < points[-1]["distance"]):
            continue
        points.append(point)
    return points


def _smoothed_stream_speeds(points, radius_seconds=8):
    """Compute GPS speed over a centered window to ignore one-sample spikes."""
    speeds = []
    left = 0
    right = 0
    for index, point in enumerate(points):
        while left + 1 < index and points[left + 1]["time"] <= point["time"] - radius_seconds:
            left += 1
        right = max(right, index)
        while right + 1 < len(points) and points[right]["time"] < point["time"] + radius_seconds:
            right += 1
        elapsed = points[right]["time"] - points[left]["time"]
        distance = points[right]["distance"] - points[left]["distance"]
        speeds.append(distance / elapsed if elapsed > 0 and distance >= 0 else 0)
    return speeds


def _range_speed(points, start_time, end_time):
    samples = [point for point in points if start_time <= point["time"] <= end_time]
    if len(samples) < 2:
        return None
    elapsed = samples[-1]["time"] - samples[0]["time"]
    if elapsed <= 0:
        return None
    return (samples[-1]["distance"] - samples[0]["distance"]) / elapsed


def _normalize_repetition_distances(efforts):
    """Snap a consistent series to its likely programmed repetition distance."""
    if len(efforts) < 2:
        return efforts
    distances = [effort[0] for effort in efforts]
    durations = [effort[3] for effort in efforts]
    median_distance = statistics.median(distances)
    median_duration = statistics.median(durations)
    if not median_distance or not median_duration:
        return efforts
    distance_spread = max(abs(value - median_distance) / median_distance for value in distances)
    duration_spread = max(abs(value - median_duration) / median_duration for value in durations)
    if distance_spread > 0.30 or duration_spread > 0.40:
        return efforts

    standard_distances = (200, 300, 400, 500, 600, 800, 1000, 1200, 1500, 1600, 2000, 3000, 5000)
    target = min(standard_distances, key=lambda value: abs(value - median_distance))
    if abs(target - median_distance) / median_distance > 0.15:
        return efforts
    return [(target, pace, heartrate, duration, start, end) for _, pace, heartrate, duration, start, end in efforts]


def _is_repetition_group(efforts):
    if len(efforts) < 2:
        return False
    paces = [effort[1] for effort in efforts]
    if min(paces) <= 0 or max(paces) / min(paces) > 1.25:
        return False
    if len(efforts) == 2:
        distances = [effort[0] for effort in efforts]
        durations = [effort[3] for effort in efforts]
        if min(distances) <= 0 or max(distances) / min(distances) > 1.40:
            return False
        if min(durations) <= 0 or max(durations) / min(durations) > 1.50:
            return False
    return True


def detect_stream_repetitions(rows):
    """Detect repeated fast efforts separated by recoveries in a raw GPS stream.

    Thresholds are derived from the run itself. A candidate must contain a
    sustained acceleration, be materially faster than both surrounding
    sections, and belong to a group of at least two efforts. This deliberately
    ignores isolated GPS spikes, hills and strides while recovering repetitions
    that were not marked with the watch's lap button.
    """
    points = _stream_points(rows)
    if len(points) < 60 or points[-1]["time"] - points[0]["time"] < 180:
        return []

    speeds = _smoothed_stream_speeds(points)
    active_speeds = [speed for speed in speeds if speed > 0.5]
    if len(active_speeds) < 60:
        return []

    slow_speed = _percentile(active_speeds, 0.25)
    fast_speed = _percentile(active_speeds, 0.80)
    if slow_speed <= 0 or fast_speed / slow_speed < 1.15:
        return []
    boundary_speed = max(
        _percentile(active_speeds, 0.55),
        slow_speed + (fast_speed - slow_speed) * 0.50,
    )

    # Find sustained high-speed seeds and bridge very short smoothing dips.
    seeds = []
    seed_start = None
    for index, speed in enumerate(speeds):
        if speed >= fast_speed and seed_start is None:
            seed_start = index
        elif speed < fast_speed and seed_start is not None:
            if points[index - 1]["time"] - points[seed_start]["time"] >= 8:
                seeds.append([seed_start, index - 1])
            seed_start = None
    if seed_start is not None and points[-1]["time"] - points[seed_start]["time"] >= 8:
        seeds.append([seed_start, len(points) - 1])

    merged_seeds = []
    for start, end in seeds:
        if merged_seeds and points[start]["time"] - points[merged_seeds[-1][1]]["time"] <= 8:
            merged_seeds[-1][1] = end
        else:
            merged_seeds.append([start, end])

    # Expand seeds to the acceleration/deceleration boundaries.
    candidates = []
    for start, end in merged_seeds:
        while start > 0 and speeds[start] >= boundary_speed:
            start -= 1
        while end + 1 < len(points) and speeds[end] >= boundary_speed:
            end += 1
        if candidates and start <= candidates[-1][1]:
            candidates[-1][1] = max(candidates[-1][1], end)
        else:
            candidates.append([start, end])

    efforts = []
    for start, end in candidates:
        start_time = points[start]["time"]
        end_time = points[end]["time"]
        duration = end_time - start_time
        distance = points[end]["distance"] - points[start]["distance"]
        if duration < 25 or duration > 1200 or distance < 150:
            continue
        average_speed = distance / duration if duration > 0 else 0
        before = _range_speed(points, max(points[0]["time"], start_time - 65), start_time - 5)
        after = _range_speed(points, end_time + 5, min(points[-1]["time"], end_time + 65))
        neighbours = [speed for speed in (before, after) if speed is not None and speed > 0]
        if not neighbours or average_speed / max(neighbours) < 1.12:
            continue
        heartrates = [point["heartrate"] for point in points[start:end + 1] if point["heartrate"]]
        max_heartrate = int(round(max(heartrates))) if heartrates else None
        efforts.append((distance, 1000 / average_speed, max_heartrate, duration, start_time, end_time))

    # An acceleration is a repetition only when another comparable effort
    # follows after a recovery; isolated surges remain ordinary running.
    groups = []
    for effort in efforts:
        if groups:
            previous = groups[-1][-1]
            grouping_gap = max(180, 2.5 * max(previous[3], effort[3]))
            if effort[4] - previous[5] <= grouping_gap:
                groups[-1].append(effort)
                continue
        groups.append([effort])

    repeated = []
    for group in groups:
        if _is_repetition_group(group):
            repeated.extend(_normalize_repetition_distances(group))
    return [(distance, pace, heartrate) for distance, pace, heartrate, _, _, _ in repeated]


def _fast_laps(laps):
    fast = []
    for lap in sorted(laps, key=lambda item: int(item.get("lap_index") or 0)):
        speed = fnum(lap.get("average_speed")) or 0
        distance = fnum(lap.get("distance")) or 0
        if speed > 0 and (1000 / speed) < 270 and distance >= 300:  # < 4:30/km, >=300m
            fast.append((distance, 1000 / speed, lap.get("max_heartrate")))
    return fast


def _laps_match_stream_repetitions(lap_fast, stream_fast):
    if len(lap_fast) != len(stream_fast) or not lap_fast:
        return False
    lap_distance = statistics.median(item[0] for item in lap_fast)
    stream_distance = statistics.median(item[0] for item in stream_fast)
    return stream_distance > 0 and abs(lap_distance - stream_distance) / stream_distance <= 0.20


def run_metrics(a):
    """Metriques detaillees d'un run pour l'analyse coach : foulee, D+, effet
    d'entrainement, sante du jour et meteo. Lues directement sur la ligne
    activites deja parsee (aucun parseur ad hoc supplementaire)."""
    def _cad(v):
        n = fnum(v)
        return round(n * 2) if n is not None else None  # Strava stocke la cadence par jambe
    def _temp(v):
        n = fnum(v)
        return round(n, 1) if n is not None else None
    def _clean(v):
        return None if v in (None, "", "\\N") else v

    stride = fnum(a.get("avg_stride_length"))          # cm
    gct = fnum(a.get("avg_ground_contact_time"))       # ms
    vosc = fnum(a.get("avg_vertical_oscillation"))     # cm
    vratio = fnum(a.get("avg_vertical_ratio"))         # %

    zones = None
    raw = a.get("hr_time_in_zones")
    if raw and raw != "\\N":
        try:
            data = json.loads(unescape_copy_value(raw))
            zones = {int(z["zone"]): int(round(fnum(z.get("seconds")) or 0)) for z in data}
        except (TypeError, ValueError, KeyError):
            zones = None

    return {
        "denivele_positif_m": _toint(a.get("total_elevation_gain")),
        "denivele_negatif_m": _toint(a.get("elevation_loss")),
        "cadence_spm": _cad(a.get("average_cadence")),
        "cadence_max_spm": _cad(a.get("max_cadence")),
        "longueur_foulee_m": round(stride / 100, 2) if stride is not None else None,
        "temps_contact_sol_ms": round(gct) if gct is not None else None,
        "oscillation_verticale_cm": round(vosc, 1) if vosc is not None else None,
        "ratio_vertical_pct": round(vratio, 1) if vratio is not None else None,
        "effet_aerobie": round(fnum(a.get("aerobic_training_effect")), 1) if fnum(a.get("aerobic_training_effect")) is not None else None,
        "effet_anaerobie": round(fnum(a.get("anaerobic_training_effect")), 1) if fnum(a.get("anaerobic_training_effect")) is not None else None,
        "charge_entrainement": _toint(a.get("activity_training_load")),
        "label_effet": _clean(a.get("training_effect_label")),
        "vo2max": _toint(a.get("vo2max")),
        "fc_temps_par_zone_s": zones,
        "meteo": {
            "temperature_c": _temp(a.get("weather_temperature")),
            "ressenti_c": _temp(a.get("weather_apparent_temperature")),
            "humidite_pct": _toint(a.get("weather_humidity")),
            "vent_kmh": _toint(a.get("weather_wind_speed")),
            "code": _toint(a.get("weather_code")),
        },
        "sante": {
            "sommeil_score": _toint(a.get("health_sleep_score")),
            "sommeil_duree_s": _toint(a.get("health_sleep_duration_seconds")),
            "vfc_ms": _toint(a.get("health_hrv_last_night_avg_ms")),
            "vfc_statut": _clean(a.get("health_hrv_status")),
            "fc_repos_bpm": _toint(a.get("health_resting_hr_bpm")),
            "fc_repos_7j_bpm": _toint(a.get("health_resting_hr_7d_avg_bpm")),
        },
    }


def analyse_run(a, laps_by_act, streams_by_act=None):
    dist = (fnum(a.get("distance")) or 0) / 1000
    mt = int(fnum(a.get("moving_time")) or 0)
    pace = mt / dist if dist > 0 else 0
    hr = a.get("average_heartrate")
    mhr = a.get("max_heartrate")
    laps = laps_by_act.get(a.get("id"), [])
    lap_speeds = [fnum(l.get("average_speed")) or 0 for l in laps]
    lap_fast = _fast_laps(laps)
    stream_rows = (streams_by_act or {}).get(a.get("id"), [])
    stream_fast = detect_stream_repetitions(stream_rows)
    gps_is_more_complete = len(stream_fast) > len(lap_fast)
    gps_disambiguates_auto_laps = (
        len(stream_fast) == len(lap_fast)
        and not _laps_match_stream_repetitions(lap_fast, stream_fast)
    )
    if len(stream_fast) >= 2 and (gps_is_more_complete or gps_disambiguates_auto_laps):
        fast = stream_fast
        fast_source = "gps"
    else:
        fast = lap_fast
        fast_source = "laps" if lap_fast else None
    kind = classify(a.get("name"), pace, lap_speeds, fast)
    if stream_fast:
        print(
            f"[coach-journal] activity {a.get('id')}: GPS detected {len(stream_fast)} repetitions; "
            f"fast laps={len(lap_fast)}; using {fast_source}",
            file=sys.stderr,
        )
    return {
        "dt": a["_dt"], "dist": dist, "mt": mt, "pace": pace,
        "hr": hr, "mhr": mhr, "kind": kind, "fast": fast, "fast_source": fast_source,
        "name": a.get("name"),
        "laps": planner_laps(laps),
        "metrics": run_metrics(a),
    }


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.path.join(proj, ".runtime/local-db/bdd_runs.sql"))
    ap.add_argument("--out", default=os.path.join(proj, ".runtime/journal-coach.md"))
    ap.add_argument("--json", default=None, help="chemin de sortie du snapshot JSON")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    source_dump = dump_source_info(args.dump)

    profile = profile_for(args.dump, today)
    acts = parse_copy(args.dump, "activities")
    runs = []
    others = []
    for a in acts:
        sd = a.get("start_date_local")
        if not sd or sd == "\\N":
            continue
        try:
            a["_dt"] = dt.datetime.fromisoformat(sd.replace("Z", "")[:19])
        except ValueError:
            continue
        if a.get("type") != "Run":
            # Rando, velo, ski, muscu... : hors statistiques de course, mais le
            # coach doit les voir pour juger la charge reelle (D+, temps sur pieds)
            # avant de programmer une seance a enjeu le lendemain.
            others.append(a)
            continue
        # privilegier garmin si doublon (meme date+distance approx)
        runs.append(a)
    runs.sort(key=lambda x: x["_dt"], reverse=True)
    others.sort(key=lambda x: x["_dt"], reverse=True)
    recent_others = [
        a for a in others
        if 0 <= (today - a["_dt"].date()).days <= CROSS_TRAINING_WINDOW_DAYS
    ][:10]

    sys.path.insert(0, proj)
    from daily_training_plan import (
        build_three_day_training_guidance,
        set_plan_overrides,
    )

    # Les ajustements du coach vivent en base (table plan_overrides) : sans eux le
    # snapshot repartirait du calendrier fige et contredirait le site.
    plan_overrides = parse_plan_overrides(args.dump)
    set_plan_overrides(plan_overrides)
    if plan_overrides:
        print(
            f"[coach-journal] {len(plan_overrides)} ajustement(s) coach appliques",
            file=sys.stderr,
        )

    # 10 derniers runs distincts (dedup garmin/strava : meme jour + distance ~ identique)
    last_activities, seen = [], []
    for a in runs:
        key = (a["_dt"].date(), round((fnum(a.get("distance")) or 0) / 100))
        if key in seen:
            if a.get("source") == "garmin":
                pass
            else:
                continue
        seen.append(key)
        last_activities.append(a)
        if len(last_activities) >= 10:
            break

    recent_ids = {activity.get("id") for activity in last_activities}
    laps_by_act = {}
    for lap in parse_copy(args.dump, "activity_laps", recent_ids):
        laps_by_act.setdefault(lap.get("activity_id"), []).append(lap)
    streams_by_act = {}
    for stream in parse_copy(args.dump, "activity_streams", recent_ids):
        streams_by_act.setdefault(stream.get("activity_id"), []).append(stream)
    last_runs = [analyse_run(activity, laps_by_act, streams_by_act) for activity in last_activities]

    # volume 7 jours glissants (aujourd'hui + les 6 dates precedentes)
    vol = rolling_run_volume(last_runs, today)

    planner_runs = [planner_run_payload(r) for r in last_runs]
    guidance = build_three_day_training_guidance(today.isoformat(), planner_runs, None)
    # Ne pas reconstruire J+1 a J+3 un par un : la fenetre multi-jours est la
    # sortie unique du site, deja reconciliee avec les runs et les overrides.
    # La recalculer ici avait cree un second chemin susceptible de diverger.
    future_guidance = (guidance.get("sessions") or [])[1:4]
    current_week = guidance.get("currentWeek") or {}

    # --- rendu markdown ---
    L = []
    L.append(f"# Journal Coach - {RUNNER.race_name}")
    L.append(f"_Genere le {today.isoformat()} a partir des donnees Garmin/Strava locales._\n")
    L.append("## Profil & objectif")
    L.append(f"- Objectif : {profile['objectif']}")
    L.append(f"- Records : marathon {profile['pr_marathon']} ; 10K {profile['pr_10k']} ; 5K {profile['pr_5k']}")
    easy_ref = profile["fc_facile_reference"]
    max_ref = profile["fc_max_reference"]
    easy_detail = (
        f"mediane {easy_ref['windowDays']} j, {easy_ref['sampleCount']} runs"
        if easy_ref["source"] == "observed_median_42d"
        else "repli personnel, echantillon insuffisant"
    )
    max_detail = (
        f"observee le {max_ref['observedOn']} sur {max_ref['windowDays']} j"
        if max_ref["source"] == "observed_90d"
        else "repli personnel, aucune observation recente"
    )
    L.append(
        f"- FC facile observee ~{profile['fc_facile']} ({easy_detail}) / "
        f"FC max utilisee {profile['fc_max']} ({max_detail})"
    )
    L.append(f"- Volume 7 derniers jours : {vol:.0f} km\n")

    L.append("## Zones d'allure")
    for z, (a1, a2) in ZONES.items():
        L.append(f"- {z} : {fmt_pace(a1)} - {fmt_pace(a2)}")
    L.append("")

    L.append("## 10 derniers runs (analyse)")
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    for r in last_runs:
        j = jours[r["dt"].weekday()]
        line = (f"- **{j} {r['dt'].strftime('%d/%m')}** - {r['kind']} - "
                f"{r['dist']:.2f} km en {r['mt']//60}:{r['mt']%60:02d} "
                f"({fmt_pace(r['pace'])}) - FCmoy {r['hr']} / FCmax {r['mhr']} - {r['name']}")
        L.append(line)
        if r["fast"]:
            frac = "; ".join(f"{int(round(d/100)*100)}m a {fmt_pace(p)} (FCmax {h})" for d, p, h in r["fast"])
            source = " detectees dans le flux GPS" if r["fast_source"] == "gps" else ""
            L.append(f"    - Fractions{source} : {frac}")
    L.append("")

    autres = [cross_training_payload(a) for a in recent_others]
    if autres:
        L.append(f"## Autres activites ({CROSS_TRAINING_WINDOW_DAYS} derniers jours)")
        L.append("_Hors statistiques de course, mais comptent dans la fatigue._")
        for a in autres:
            d3 = dt.date.fromisoformat(a["date"])
            j = jours[d3.weekday()]
            detail = a["duree"]
            if a["distance_km"]:
                detail += f", {a['distance_km']:.1f} km"
            if a["denivele_m"]:
                detail += f", {a['denivele_m']} m D+"
            if a["fc_moy"]:
                detail += f", FCmoy {a['fc_moy']}"
            L.append(f"- **{j} {d3.strftime('%d/%m')}** - {a['sport']} - {detail} - {a['nom']}")
        L.append("")

    # lecture rapide
    nb_q = sum(1 for r in last_runs[:10] if r["kind"] == "Qualite")
    L.append("## Lecture de la semaine")
    if current_week:
        km_min = current_week.get("estimatedKmMin")
        km_max = current_week.get("estimatedKmMax")
        days_min = current_week.get("plannedRunDaysMin")
        days_max = current_week.get("plannedRunDaysMax")
        km_text = f"{km_min}-{km_max}" if km_min != km_max else str(km_max)
        days_text = f"{days_min}-{days_max}" if days_min != days_max else str(days_max)
        L.append(
            f"- {current_week.get('label')} — {current_week.get('phaseLabel')} : "
            f"environ {km_text} km, {days_text} sorties."
        )
    L.append(f"- {nb_q} seance(s) qualite sur les 10 derniers runs, volume 7 jours {vol:.0f} km.")
    last = last_runs[0] if last_runs else None
    if last:
        if last["kind"] == "Qualite":
            L.append("- Dernier run = qualite : la priorite du jour est la recuperation (footing facile ou repos).")
        else:
            L.append("- Dernier run = footing : pret pour une seance a enjeu si le calendrier le prevoit.")
    L.append("")

    L.append("## Projection des 3 prochaines seances")
    for item in future_guidance:
        d2 = dt.date.fromisoformat(item["date"])
        j = jours[d2.weekday()]
        L.append(f"- **{j} {d2.strftime('%d/%m')}** : {render_guidance_session(item)}")
    L.append("")
    L.append("> Regle d'ajustement : si fatigue / FC anormalement haute pour l'allure / nuit courte / "
             "forte chaleur (>30 C), alleger de 20-30 % ou remplacer par footing facile. "
             "Allure marathon de calibrage : 4:37/km, sans empiler deux seances cles.")

    out = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"[coach-journal] written to {args.out}", file=sys.stderr)

    # --- snapshot JSON (pour le MCP) ---
    if args.json:
        payload = {
            "schema_version": 1,
            "genere_le": today.isoformat(),
            "date_generation": today.isoformat(),
            "source_dump": source_dump,
            "objectif": profile["objectif"],
            "profil": {
                "pr_10k": profile["pr_10k"], "pr_5k": profile["pr_5k"],
                "pr_marathon": profile["pr_marathon"],
                "fc_facile": profile["fc_facile"], "fc_max": profile["fc_max"],
                "fc_facile_reference": profile["fc_facile_reference"],
                "fc_max_reference": profile["fc_max_reference"],
            },
            "volume_7j_km": round(vol, 1),
            "semaine_courante": {
                "numero": current_week.get("index"),
                "debut": current_week.get("start"),
                "fin": current_week.get("end"),
                "libelle": current_week.get("label"),
                "phase": current_week.get("phaseLabel"),
                "volume_km_min": current_week.get("estimatedKmMin"),
                "volume_km_max": current_week.get("estimatedKmMax"),
                "sorties_min": current_week.get("plannedRunDaysMin"),
                "sorties_max": current_week.get("plannedRunDaysMax"),
            } if current_week else None,
            "zones_allure": {z: {"min": fmt_pace(a1), "max": fmt_pace(a2)} for z, (a1, a2) in ZONES.items()},
            "seance_du_jour": {"date": today.isoformat(), "seance": render_guidance_session(guidance)},
            "derniers_runs": [
                {
                    "date": r["dt"].date().isoformat(),
                    "jour": jours[r["dt"].weekday()],
                    "type": r["kind"],
                    "nom": r["name"],
                    "distance_km": round(r["dist"], 2),
                    "duree": f"{r['mt']//60}:{r['mt']%60:02d}",
                    "allure": fmt_pace(r["pace"]),
                    "fc_moy": _toint(r["hr"]),
                    "fc_max": _toint(r["mhr"]),
                    "fractions": [
                        {"distance_m": int(round(d / 100) * 100), "allure": fmt_pace(p),
                         "fc_max": _toint(h), "source": r["fast_source"]}
                        for d, p, h in r["fast"]
                    ],
                    "metrics": r.get("metrics"),
                }
                for r in last_runs
            ],
            # Activites hors course : le coach les lit pour juger la fatigue,
            # jamais pour calculer volume, allures ou records.
            "autres_activites": autres,
            "projection": [
                {
                    "date": item["date"],
                    "jour": jours[dt.date.fromisoformat(item["date"]).weekday()],
                    "seance": render_guidance_session(item),
                }
                for item in future_guidance
            ],
            "regle_ajustement": ("Si fatigue / FC anormalement haute / nuit courte / chaleur >30C : "
                                 "alleger 20-30% ou footing facile. AM de calibrage 4:37/km ; "
                                 "ne jamais empiler deux seances cles."),
        }
        os.makedirs(os.path.dirname(args.json), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[coach-journal] json written to {args.json}", file=sys.stderr)

    print(args.out)


if __name__ == "__main__":
    main()
