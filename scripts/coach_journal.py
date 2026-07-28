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
import statistics
import sys
from pathlib import Path

# Profil du coureur. Ces valeurs sont propres a chacun : renseigne-les via un
# fichier JSON (COACH_PROFILE_FILE, defaut <repo>/coach_profile.json) plutot que
# de les coder en dur. Les valeurs ci-dessous ne sont qu'un exemple neutre.
DEFAULT_PROFILE = {
    "pr_5k": "",
    "pr_10k": "",
    "pr_marathon": "",
    "objectif": "",
    "fc_facile": 140,
    "fc_max": 190,
}


def _load_profile() -> dict:
    """Charge le profil depuis COACH_PROFILE_FILE, sinon renvoie l'exemple neutre."""
    path = os.environ.get("COACH_PROFILE_FILE") or str(
        Path(__file__).resolve().parent.parent / "coach_profile.json"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            return {**DEFAULT_PROFILE, **json.load(fh)}
    except FileNotFoundError:
        return dict(DEFAULT_PROFILE)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[coach_journal] profil illisible ({path}): {exc}", file=sys.stderr)
        return dict(DEFAULT_PROFILE)


PROFILE = _load_profile()

# Zones d'allure (min/km)
ZONES = {
    "Recuperation": (6 * 60 + 35, 7 * 60 + 5),
    "Footing facile": (6 * 60 + 15, 6 * 60 + 45),
    "Endurance moyenne": (5 * 60 + 55, 6 * 60 + 15),
    "Allure marathon": (5 * 60 + 25, 5 * 60 + 35),
    "Allure semi": (5 * 60 + 15, 5 * 60 + 25),
    "Seuil": (5 * 60, 5 * 60 + 10),
    "VO2max": (4 * 60 + 40, 4 * 60 + 55),
    "Lignes": (4 * 60 + 20, 4 * 60 + 35),
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


def fmt_pace(sec_per_km):
    if not sec_per_km or sec_per_km <= 0:
        return "-"
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}/km"


def render_guidance_session(guidance):
    title = guidance.get("title") or "Seance coach"
    session = guidance.get("session") or {}
    main = session.get("main") or "-"
    return f"{title} : {main}"


def planner_run_payload(run):
    return {
        "id": f"{run['dt'].date().isoformat()}-{run['name'] or 'run'}",
        "date": run["dt"].date().isoformat(),
        "start_date_local": run["dt"].isoformat(),
        "distance_km": round(run["dist"], 2),
        "moving_time": run["mt"],
        "pace_sec_per_km": run["pace"],
        "average_heartrate": _toint(run["hr"]),
        "max_heartrate": _toint(run["mhr"]),
    }


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

    acts = parse_copy(args.dump, "activities")
    runs = []
    for a in acts:
        if a.get("type") != "Run":
            continue
        sd = a.get("start_date_local")
        if not sd or sd == "\\N":
            continue
        try:
            a["_dt"] = dt.datetime.fromisoformat(sd.replace("Z", "")[:19])
        except ValueError:
            continue
        # privilegier garmin si doublon (meme date+distance approx)
        runs.append(a)
    runs.sort(key=lambda x: x["_dt"], reverse=True)

    sys.path.insert(0, proj)
    from daily_training_plan import build_daily_training_guidance, build_three_day_training_guidance

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

    # volume 7 jours glissants
    vol = sum(r["dist"] for r in last_runs if (today - r["dt"].date()).days <= 7)

    planner_runs = [planner_run_payload(r) for r in last_runs]
    guidance = build_three_day_training_guidance(today.isoformat(), planner_runs, None)
    future_guidance = [
        build_daily_training_guidance(
            today + dt.timedelta(days=offset),
            planner_runs,
            None,
            as_of_day=today,
            apply_adjustments=False,
        )
        for offset in range(1, 4)
    ]

    # --- rendu markdown ---
    L = []
    L.append("# Journal Coach")
    L.append(f"_Genere le {today.isoformat()} a partir des donnees Garmin/Strava locales._\n")
    L.append("## Profil & objectif")
    L.append(f"- Objectif : {PROFILE['objectif']}")
    L.append(f"- Records : marathon {PROFILE['pr_marathon']} ; 10K {PROFILE['pr_10k']} ; 5K {PROFILE['pr_5k']}")
    L.append(f"- FC facile ~{PROFILE['fc_facile']} / FC max ~{PROFILE['fc_max']}")
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

    # lecture rapide
    nb_q = sum(1 for r in last_runs[:10] if r["kind"] == "Qualite")
    L.append("## Lecture de la semaine")
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
             "Utiliser l'allure cible configuree, sans empiler deux seances cles.")

    out = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"[coach-journal] written to {args.out}", file=sys.stderr)

    # --- snapshot JSON (pour le MCP) ---
    if args.json:
        payload = {
            "genere_le": today.isoformat(),
            "date_generation": today.isoformat(),
            "objectif": PROFILE["objectif"],
            "profil": {
                "pr_10k": PROFILE["pr_10k"], "pr_5k": PROFILE["pr_5k"],
                "pr_marathon": PROFILE["pr_marathon"],
                "fc_facile": PROFILE["fc_facile"], "fc_max": PROFILE["fc_max"],
            },
            "volume_7j_km": round(vol, 1),
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
                }
                for r in last_runs
            ],
            "projection": [
                {
                    "date": item["date"],
                    "jour": jours[dt.date.fromisoformat(item["date"]).weekday()],
                    "seance": render_guidance_session(item),
                }
                for item in future_guidance
            ],
            "regle_ajustement": ("Si fatigue / FC anormalement haute / nuit courte / chaleur >30C : "
                                 "alleger 20-30% ou footing facile. Utiliser l'allure cible configuree ; "
                                 "ne jamais empiler deux seances cles."),
        }
        os.makedirs(os.path.dirname(args.json), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[coach-journal] json written to {args.json}", file=sys.stderr)

    print(args.out)


if __name__ == "__main__":
    main()
