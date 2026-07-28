#!/usr/bin/env python3
"""
Import d'une activité Garmin depuis un fichier .fit vers Neon.

Usage :
  .venv/bin/pip install fitdecode
  .venv/bin/python3 scripts/import_fit.py /chemin/vers/ACTIVITY.fit
"""
import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import fitdecode
except ImportError:
    print("fitdecode manquant — lance : .venv/bin/pip install fitdecode")
    sys.exit(1)

import database_pg


def _semicircles_to_deg(sc):
    return sc * 180.0 / 2**31 if sc is not None else None


def _parse_fit(path: str) -> dict:
    session = {}
    speeds = []
    lats = []
    lons = []
    local_start = None

    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            fdata = {f.name: f.value for f in frame.fields}

            if frame.name == "session":
                session = fdata

            elif frame.name == "activity":
                if fdata.get("local_timestamp"):
                    local_start = fdata["local_timestamp"]

            elif frame.name == "record":
                spd = fdata.get("enhanced_speed") or fdata.get("speed")
                if spd is not None:
                    speeds.append(spd)
                lat = fdata.get("position_lat")
                lon = fdata.get("position_long")
                if lat is not None and lon is not None:
                    lats.append(lat)
                    lons.append(lon)

    return session, speeds, lats, lons, local_start


def fit_to_activity(path: str) -> dict:
    session, speeds, lats, lons, local_start = _parse_fit(path)

    # Garmin activity ID depuis le nom de fichier (ex: 4fca5225-23127191515_ACTIVITY.fit)
    basename = os.path.basename(path)
    garmin_id = None
    for part in basename.replace("-", "_").split("_"):
        if part.isdigit() and len(part) >= 8:
            garmin_id = int(part)
            break
    if garmin_id is None:
        raise ValueError(f"Impossible d'extraire l'activity ID depuis '{basename}'")

    distance = float(session.get("total_distance") or 0)
    timer_time = float(session.get("total_timer_time") or 0)
    elapsed_time = float(session.get("total_elapsed_time") or timer_time)
    avg_speed = (distance / timer_time) if timer_time > 0 else 0
    max_speed = max(speeds) if speeds else 0

    # Temps local (sans timezone)
    if local_start:
        start_local = local_start.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        ts = session.get("start_time")
        start_local = ts.strftime("%Y-%m-%dT%H:%M:%S") if ts else ""

    # GPS start/end (semicircles → degrés)
    slat = _semicircles_to_deg(lats[0]) if lats else None
    slng = _semicircles_to_deg(lons[0]) if lons else None
    elat = _semicircles_to_deg(lats[-1]) if lats else None
    elng = _semicircles_to_deg(lons[-1]) if lons else None

    cadence = session.get("avg_running_cadence")  # strides/min (1 pied), même format que Strava

    return {
        "id": garmin_id,
        "garmin_activity_id": garmin_id,
        "source": "garmin",
        "athlete_id": 0,
        "name": "Course à pied",
        "start_date_local": start_local,
        "distance": distance,
        "moving_time": int(timer_time),
        "elapsed_time": int(elapsed_time),
        "total_elevation_gain": session.get("total_ascent") or 0,
        "average_speed": avg_speed,
        "max_speed": max_speed,
        "average_heartrate": session.get("avg_heart_rate"),
        "max_heartrate": session.get("max_heart_rate"),
        "calories": session.get("total_calories") or 0,
        "average_cadence": cadence,
        "sport_type": "Run",
        "type": "Run",
        "has_heartrate": bool(session.get("avg_heart_rate")),
        "pr_count": 0,
        "suffer_score": None,
        "gear_id": None,
        "summary_polyline": None,
        "start_latlng": [slat, slng] if slat else None,
        "end_latlng": [elat, elng] if elat else None,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_fit.py /chemin/vers/ACTIVITY.fit")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"Fichier introuvable : {path}")
        sys.exit(1)

    print(f"Parsing {path}…")
    activity = fit_to_activity(path)

    print(f"  ID Garmin : {activity['garmin_activity_id']}")
    print(f"  Date      : {activity['start_date_local']}")
    print(f"  Distance  : {activity['distance']/1000:.2f} km")
    print(f"  Durée     : {activity['moving_time']//60}min{activity['moving_time']%60:02d}s")
    print(f"  FC moy    : {activity['average_heartrate']} bpm")
    print(f"  D+        : {activity['total_elevation_gain']} m")

    print("\nInsertion en base…")
    database_pg.upsert_activities([activity])
    print("OK — course importée.")


if __name__ == "__main__":
    main()
