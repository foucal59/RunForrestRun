from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any


EASY_PACE = "6:15-6:45/km"
RECOVERY_PACE = "6:35-7:05/km"
STEADY_PACE = "5:55-6:15/km"
THRESHOLD_PACE = "5:00-5:10/km"
GOAL_PACE = "5:25-5:35/km"
GOAL_PACE_TIGHT = "5:30/km"
SEMI_PACE = "5:15-5:25/km"
VO2_PACE = "4:40-4:55/km"
STRIDES_PACE = "4:20-4:35/km"
LONG_COMPLETION_RATIO = 0.85
# Une seance qualite n'est "couverte" que si le run atteint 70% du volume prevu :
# un 3 km rapide ne remplace pas un seuil de 4 x 6'.
QUALITY_COMPLETION_RATIO = 0.70
# Le seuil est volontairement generique. Chaque utilisateur doit adapter les
# allures d'exemple a son niveau avant d'utiliser le plan.
MARATHON_PACE_MAX_SEC = 345
PLAN_SOURCE = "marathon-template"
# Libelles affiches dans l'UI. Surchargeables sans toucher au code :
#   PLAN_RACE_NAME / PLAN_DESCRIPTION dans l'environnement.
RACE_NAME = os.environ.get("PLAN_RACE_NAME", "Marathon")
PLAN_DESCRIPTION = os.environ.get(
    "PLAN_DESCRIPTION",
    f"Coach {RACE_NAME} (modele de 16 semaines a personnaliser)",
)
PLAN_BASIS = "Adapte sur les 10 derniers entrainements charges"
def _configured_date(name: str, fallback: date) -> date:
    value = os.environ.get(name, "").strip()
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD format") from exc


_today = date.today()
_next_thursday = _today + timedelta(days=(3 - _today.weekday()) % 7)
PLAN_START = _configured_date("PLAN_START_DATE", _next_thursday)
RACE_DAY = _configured_date("PLAN_RACE_DATE", PLAN_START + timedelta(days=108))
if RACE_DAY <= PLAN_START:
    raise ValueError("PLAN_RACE_DATE must be after PLAN_START_DATE")
PLAN_END = RACE_DAY


def _easy(minutes: int, strides: int = 0) -> dict[str, Any]:
    return {"kind": "easy", "category": "easy", "minutes": minutes, "strides": strides}


def _long(minutes: int, strides: int = 0) -> dict[str, Any]:
    return {"kind": "long", "category": "long", "minutes": minutes, "strides": strides}


def _rest() -> dict[str, Any]:
    return {"kind": "rest", "category": "rest"}


def _intervals(
    title: str,
    reps: int,
    distance_m: int,
    pace_range: str,
    recovery: str,
    *,
    tag: str,
    warmup_minutes: int = 12,
    cooldown_minutes: int = 8,
    add_strides: int = 0,
) -> dict[str, Any]:
    return {
        "kind": "intervals",
        "category": "quality",
        "title": title,
        "reps": reps,
        "distance_m": distance_m,
        "pace_range": pace_range,
        "recovery": recovery,
        "warmup_minutes": warmup_minutes,
        "cooldown_minutes": cooldown_minutes,
        "add_strides": add_strides,
        "tag": tag,
    }


def _custom(
    title: str,
    *,
    category: str,
    warmup: str,
    main: str,
    cooldown: str,
    tag: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "custom",
        "category": category,
        "title": title,
        "warmup": warmup,
        "main": main,
        "cooldown": cooldown,
        "tag": tag,
    }


def _race(title: str) -> dict[str, Any]:
    return {"kind": "race", "category": "race", "title": title}


def _planned(
    title: str,
    main: str,
    *,
    category: str,
    tag: str | None = None,
    warmup: str = "Depart tranquille, montee en allure sur 5' (pas d'echauffement separe)",
    cooldown: str = "2-3' de marche",
) -> dict[str, Any]:
    return _custom(
        title,
        category=category,
        warmup=warmup,
        main=main,
        cooldown=cooldown,
        tag=tag,
    )


def _easy_plan(title: str, main: str, *, tag: str = "easy") -> dict[str, Any]:
    return _planned(title, main, category="easy", tag=tag)


def _quality_plan(title: str, main: str, *, tag: str = "quality") -> dict[str, Any]:
    return _planned(
        title,
        main,
        category="quality",
        tag=tag,
        warmup="12' facile + 3 lignes de 20''",
        cooldown="5' tres facile",
    )


def _long_plan(title: str, main: str, *, tag: str = "marathon-long") -> dict[str, Any]:
    return _planned(
        title,
        main,
        category="long",
        tag=tag,
        warmup="10' tres facile (montee progressive en allure)",
        cooldown="5' tres facile + hydratation",
    )


def _build_calendar() -> dict[str, dict[str, Any]]:
    # Calendrier de base du bloc marathon (16 semaines, course ancree a RACE_DAY).
    # Sert d'exemple : adapte les dates, allures et seances a ton propre objectif.
    calendar: dict[str, dict[str, Any]] = {
        (PLAN_START + timedelta(days=0)).isoformat(): _easy_plan("Footing facile + lignes", f"40' a {EASY_PACE} + 4 lignes de 20'' relachees"),
        (PLAN_START + timedelta(days=1)).isoformat(): _rest(),
        (PLAN_START + timedelta(days=2)).isoformat(): _easy_plan("Footing facile", f"45' a {EASY_PACE}"),
        (PLAN_START + timedelta(days=3)).isoformat(): _long_plan("Sortie longue facile", "60-70' facile, 12-13 km sans bloc rapide"),
    }

    weeks = {
        PLAN_START + timedelta(days=4): [
            _rest(),
            _quality_plan("6 x 400 m VO2", "6 x 400 m a 4:00/km, recup 1'30 trot", tag="vo2"),
            _easy_plan("Footing facile + lignes", "50' a 6:15-6:45/km + 5 lignes de 20''"),
            # Exemple d'une 2e qualite absorbee dans la semaine : le moteur
            # adaptatif allege alors la fin de semaine au lieu de la repeter.
            _quality_plan("Tempo 9 km", "9 km a 4:30/km, seance absorbee: ne pas repeter", tag="tempo"),
            _easy_plan("Footing de recuperation", "30-40' tres facile a 6:35-7:05/km", tag="recovery"),
            # Exemple d'une sortie longue placee un jour en avance : le dimanche
            # suivant bascule alors en recuperation (voir _reschedule_missed_key).
            _long_plan("SL 16 km + AM (1 j d'avance)", "16 km facile dont les 4 derniers km a 5:25-5:35/km"),
            _easy_plan("Recuperation (SL faite la veille)", "Repos, marche ou 30-40' tres facile a 6:35-7:05/km", tag="recovery"),
        ],
        PLAN_START + timedelta(days=11): [
            _rest(),
            _quality_plan("Seuil 3 x 8'", "3 x 8' a 4:20-4:25/km, recup 2' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "50' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "65' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 18 km avec AM", "18 km dont les 6 derniers a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=18): [
            _rest(),
            _quality_plan("Seuil 4 x 6'", "4 x 6' a 4:20/km, recup 90'' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "55' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "65' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 20 km avec AM", "20 km, dont les 8 derniers km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=25): [
            _rest(),
            _quality_plan("5 x 3' VO2", "5 x 3' a 4:00/km, recup 2' trot", tag="vo2"),
            _easy_plan("Footing facile", "45' a 6:15-6:45/km"),
            _rest(),
            _easy_plan("Footing facile + lignes", "45' a 6:15-6:45/km + lignes"),
            _easy_plan("Repos ou footing court", "Repos ou 30' tres facile a 6:35-7:05/km"),
            _long_plan("SL 15 km facile", "15 km facile a 6:15-6:50/km"),
        ],
        PLAN_START + timedelta(days=32): [
            _rest(),
            _quality_plan("Seuil 5 x 6'", "5 x 6' a 4:18-4:22/km, recup 2' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "55' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "70' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 22 km avec AM", "22 km, dont les 8 derniers km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=39): [
            _rest(),
            _quality_plan("AM 5 x 2 km", "5 x 2 km a 5:30/km, recup 1' trot", tag="marathon-pace"),
            _easy_plan("Footing facile", "55' a 6:15-6:45/km"),
            _rest(),
            _easy_plan("Endurance moyenne + lignes", "70' a 5:55-6:15/km + lignes", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 25 km avec AM", "25 km, dont 2 x 6 km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=46): [
            _rest(),
            _quality_plan("Seuil 3 x 10'", "3 x 10' a 4:22/km, recup 3' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "60' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "70' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 27 km steady", "27 km en steady a 5:15-5:30/km"),
        ],
        PLAN_START + timedelta(days=53): [
            _rest(),
            _quality_plan("6 x 400 m + 4 x 200 m", "6 x 400 m a 4:00/km + 4 x 200 m a 3:50/km, recup complete", tag="vo2"),
            _easy_plan("Footing facile", "50' a 6:15-6:45/km"),
            _rest(),
            _easy_plan("Footing facile + lignes", "50' a 6:15-6:45/km + lignes"),
            _easy_plan("Repos ou footing court", "Repos ou 30' tres facile a 6:35-7:05/km"),
            _long_plan("SL 18 km facile", "18 km facile a 6:15-6:50/km"),
        ],
        PLAN_START + timedelta(days=60): [
            _rest(),
            _quality_plan("AM 2 x 5 km", "2 x 5 km a 5:30/km, recup 2' trot", tag="marathon-pace"),
            _easy_plan("Footing facile + lignes", "60' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "70' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "45' a 6:15-6:45/km"),
            _long_plan("SL 28 km avec AM", "28 km, dont 10-12 km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=67): [
            _rest(),
            _quality_plan("Seuil 4 x 8'", "4 x 8' a 4:20/km, recup 2' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "60' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "75' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "45' a 6:15-6:45/km"),
            _long_plan("SL 30 km avec AM", "30 km, dont les 10 derniers km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=74): [
            _rest(),
            _quality_plan("AM 3 x 4 km", "3 x 4 km a 5:30/km, recup 90'' trot", tag="marathon-pace"),
            _easy_plan("Footing facile + lignes", "60' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Endurance moyenne", "70' a 5:55-6:15/km", tag="steady"),
            _easy_plan("Footing court", "40' a 6:15-6:45/km"),
            _long_plan("SL 32 km avec AM", "32 km, dont 3 x 6 km a 5:25-5:35/km repartis"),
        ],
        PLAN_START + timedelta(days=81): [
            _rest(),
            _quality_plan("Seuil leger 3 x 6'", "3 x 6' a 4:22/km, recup 2' trot", tag="threshold"),
            _easy_plan("Footing facile + lignes", "50' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Footing pre-course", "40' a 6:15-6:45/km + 4 lignes"),
            _easy_plan("Repos ou footing court", "Repos ou 30' tres facile a 6:35-7:05/km"),
            _long_plan("Semi-marathon test", f"Semi-marathon test: effort controle a {SEMI_PACE}, ou 21 km dont 12 km a AM", tag="race-test"),
        ],
        PLAN_START + timedelta(days=88): [
            _rest(),
            _quality_plan("AM 4 x 2 km", "4 x 2 km a 5:30/km, recup 1' trot", tag="marathon-pace"),
            _easy_plan("Footing facile", "50' a 6:15-6:45/km"),
            _rest(),
            _easy_plan("Endurance moyenne + lignes", "55' a 5:55-6:15/km + lignes", tag="steady"),
            _easy_plan("Footing court", "35' a 6:15-6:45/km"),
            _long_plan("SL 20-22 km facile", "20-22 km facile, derniere vraie sortie longue"),
        ],
        PLAN_START + timedelta(days=95): [
            _rest(),
            _quality_plan("AM 3 x 2 km", "3 x 2 km a 5:30/km, recup 1' trot", tag="marathon-pace"),
            _easy_plan("Footing facile + lignes", "45' a 6:15-6:45/km + lignes"),
            _rest(),
            _easy_plan("Footing facile", "40' a 6:15-6:45/km"),
            _rest(),
            _long_plan("14-16 km dont 5 km AM", "14-16 km dont 5 km a 5:25-5:35/km"),
        ],
        PLAN_START + timedelta(days=102): [
            _rest(),
            _easy_plan("Footing facile + lignes", "35' facile a 6:15-6:45/km + 4 lignes"),
            _quality_plan("Rappel AM 3 x 1 km", "30' facile dont 3 x 1 km a 5:30/km, recup 2' facile", tag="marathon-pace"),
            _easy_plan("Repos ou footing tres court", "Repos ou 25' tres facile a 6:35-7:05/km"),
            _easy_plan("Footing tres facile + lignes", "25' tres facile + 3 lignes de 20''"),
            _rest(),
            _race(RACE_NAME),
        ],
    }
    for monday, sessions in weeks.items():
        for offset, session in enumerate(sessions):
            calendar[(monday + timedelta(days=offset)).isoformat()] = deepcopy(session)
    return calendar


# Le plan source etait cale avec les sorties longues le dimanche. A partir de
# la premiere semaine non figee par des seances deja realisees, on normalise le
# calendrier de base sur des SL le samedi : la case affichee le jour J reprend
# ce que le plan initial prevoyait en J+1 (SL du dimanche -> samedi, qualite du
# mardi -> lundi, etc.). Contraintes :
#  - la course reste ancree a RACE_DAY ; la veille devient repos ;
#  - les jours anterieurs a SATURDAY_LONG_RUN_START ne bougent pas, pour preserver
#    les seances de la premiere semaine a leur date d'origine.
SATURDAY_LONG_RUN_START = PLAN_START + timedelta(days=11)


def _shift_calendar_to_saturday_long_runs(calendar: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recale le calendrier de base sur des sorties longues le samedi.

    Formulation "pull" : la nouvelle case du jour D reprend l'ancienne seance de
    J+1. La seance exactement a SATURDAY_LONG_RUN_START (repos du lundi) est
    ainsi absorbee, sans collision avec la derniere journee conservee.
    """
    shifted: dict[str, dict[str, Any]] = {}
    race_iso = None
    for iso, session in calendar.items():
        if session.get("kind") == "race":
            race_iso = iso
    for iso in calendar:
        day = date.fromisoformat(iso)
        if day < SATURDAY_LONG_RUN_START:
            shifted[iso] = deepcopy(calendar[iso])  # passe : inchange
            continue
        nxt = calendar.get((day + timedelta(days=1)).isoformat())
        if nxt is None or nxt.get("kind") == "race":
            # Veille de course (ou fin de plan) : jour de repos.
            shifted[iso] = _rest()
        else:
            shifted[iso] = deepcopy(nxt)
    if race_iso is not None:
        shifted[race_iso] = deepcopy(calendar[race_iso])  # marathon ancre
    return shifted


PLAN_CALENDAR = _shift_calendar_to_saturday_long_runs(_build_calendar())
TAPER_START = RACE_DAY - timedelta(days=13)


def _parse_day(value: str | date | datetime | None) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return date.today()
    return date.fromisoformat(str(value)[:10])


def _fmt_duration(seconds: int | float | None) -> str:
    total = int(round(seconds or 0))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}h{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_pace(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "-"
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _fmt_short_day(day_iso: str) -> str:
    current = date.fromisoformat(day_iso)
    names = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    months = ["janv", "fevr", "mars", "avr", "mai", "juin", "juil", "aout", "sept", "oct", "nov", "dec"]
    return f"{names[current.weekday()]} {current.day} {months[current.month - 1]}"


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def normalize_recent_training_runs(
    raw_runs: Any,
    target_day: str | date | datetime | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Normalize client-provided recent runs to the planner's run shape."""
    if not isinstance(raw_runs, list):
        return []

    target_iso = _parse_day(target_day).isoformat()
    runs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()

    for item in raw_runs:
        if not isinstance(item, dict):
            continue

        start = str(item.get("start_date_local") or item.get("date") or "").strip()
        run_day = str(item.get("date") or start[:10]).strip()[:10]
        if not run_day:
            continue
        try:
            date.fromisoformat(run_day)
        except ValueError:
            continue
        if run_day > target_iso:
            continue

        distance_m = _coerce_float(item.get("distance_m"))
        distance_km = _coerce_float(item.get("distance_km"))
        if distance_m is None:
            distance_m = (distance_km or 0) * 1000.0
        if distance_km is None:
            distance_km = round(distance_m / 1000.0, 2)

        moving_time = _coerce_int(item.get("moving_time"))
        pace = _coerce_float(item.get("pace_sec_per_km"))
        if pace is None and distance_m > 0 and moving_time > 0:
            pace = moving_time / (distance_m / 1000.0)

        run_id = item.get("id")
        key = (
            str(run_id) if run_id is not None else "",
            start or run_day,
            round(distance_m, 1),
        )
        if key in seen:
            continue
        seen.add(key)

        runs.append({
            "id": run_id,
            "name": item.get("name") or "Run",
            "start_date_local": start or run_day,
            "date": run_day,
            "distance_m": distance_m,
            "distance_km": round(distance_km, 2),
            "moving_time": moving_time,
            "pace_sec_per_km": pace,
            "average_heartrate": _coerce_float(item.get("average_heartrate")),
            "max_heartrate": _coerce_float(item.get("max_heartrate")),
        })

    runs.sort(key=lambda run: str(run.get("start_date_local") or run.get("date") or ""), reverse=True)
    return runs[:max(0, limit)]


def _session_title(session: dict[str, Any]) -> str:
    kind = session.get("kind")
    if kind == "easy":
        return "Footing facile + lignes" if session.get("strides") else "Footing facile"
    if kind == "long":
        return session.get("title") or "Sortie longue"
    if kind == "rest":
        return "Repos"
    if kind == "intervals":
        return session.get("title") or "Seance qualite"
    if kind == "custom":
        return session.get("title") or "Seance du jour"
    if kind == "race":
        return session.get("title") or "Course"
    return "Seance du jour"


def _render_session(session: dict[str, Any]) -> dict[str, str]:
    kind = session.get("kind")
    if kind == "easy":
        # Footing entierement lent (allure de depart >= 5'10) : le footing sert
        # d'echauffement, pas de bloc dedie a part.
        warmup = "Depart tranquille, montee en allure sur 5' (pas d'echauffement separe)"
        main_minutes = max(15, int(session["minutes"]) - 8)
        main = f"{main_minutes}' a {session.get('pace_range', EASY_PACE)}"
        if session.get("strides"):
            main += f" + {session['strides']} lignes de 20'' a {STRIDES_PACE}, recup 40'' trot"
        cooldown = "2-3' de marche"
        return {"title": _session_title(session), "warmup": warmup, "main": main, "cooldown": cooldown}
    if kind == "long":
        warmup = "10' tres facile (montee progressive en allure)"
        main_minutes = max(30, int(session["minutes"]) - 15)
        main = f"{main_minutes}' a {session.get('pace_range', EASY_PACE)}"
        if session.get("strides"):
            main += f" + {session['strides']} lignes de 20'' a {STRIDES_PACE} en fin de seance"
        cooldown = "5' tres facile"
        return {"title": _session_title(session), "warmup": warmup, "main": main, "cooldown": cooldown}
    if kind == "rest":
        return {
            "title": "Repos",
            "warmup": "Aucun",
            "main": "Repos complet ou 20-30' de marche tres facile",
            "cooldown": "Mobilite legere si besoin",
        }
    if kind == "intervals":
        warmup = f"{session['warmup_minutes']}' footing + 3 lignes de 20''"
        main = (
            f"{session['reps']} x {session['distance_m']} m a {session['pace_range']}, "
            f"recup {session['recovery']}"
        )
        if session.get("add_strides"):
            main += f" + {session['add_strides']} lignes de 20'' a {STRIDES_PACE}"
        cooldown = f"{session['cooldown_minutes']}' tres facile"
        return {"title": _session_title(session), "warmup": warmup, "main": main, "cooldown": cooldown}
    if kind == "custom":
        return {
            "title": _session_title(session),
            "warmup": session.get("warmup", "-"),
            "main": session.get("main", "-"),
            "cooldown": session.get("cooldown", "-"),
        }
    if kind == "race":
        return {
            "title": _session_title(session),
            "warmup": "5-10' tres facile + mobilite courte",
            "main": "Marathon: depart prudent, puis stabiliser l'allure cible configuree. Accelerer seulement si les sensations et la FC restent maitrisees.",
            "cooldown": "5-10' de marche et ravitaillement",
        }
    return {"title": "Seance du jour", "warmup": "-", "main": "-", "cooldown": "-"}


def _lighten_easy(session: dict[str, Any], *, long_day: bool = False) -> dict[str, Any]:
    out = deepcopy(session)
    if out.get("kind") in {"easy", "long"}:
        delta = 20 if long_day else 10
        out["kind"] = "easy"
        out["category"] = "easy"
        out["minutes"] = max(30, int(out.get("minutes", 45)) - delta)
        out["pace_range"] = RECOVERY_PACE
        out["strides"] = 0
    elif out.get("kind") == "custom":
        out["category"] = "easy"
        out["title"] = "Footing de recuperation"
        out["warmup"] = "Depart tranquille, montee en allure sur 5' (pas d'echauffement separe)"
        out["main"] = f"35-45' facile a {RECOVERY_PACE}"
        out["cooldown"] = "2-3' de marche"
    return out


def _lightened_quality_main(main: str) -> str:
    def reduce_reps(match: re.Match[str]) -> str:
        reps = max(2, int(round(int(match.group(1)) * 0.75)))
        return f"{reps} x {match.group(2)}"

    reduced = re.sub(
        r"\b(\d+)\s*x\s*((?:\d+(?:[.,]\d+)?\s*km)|(?:\d+\s*m)|(?:\d+\s*'))",
        reduce_reps,
        main,
        count=1,
    )
    return f"Version allegee (~-25% de volume) : {reduced}"


def _lighten_quality(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("kind") == "intervals":
        out = deepcopy(session)
        out["reps"] = max(2, int(round(out["reps"] * 0.75)))
        if out.get("distance_m", 0) >= 1000:
            out["recovery"] = "2' trot"
        return out
    if session.get("kind") == "custom" and session.get("category") == "quality":
        out = deepcopy(session)
        out["title"] = f"{session.get('title', 'Seance cle')} (allegee)"
        out["main"] = _lightened_quality_main(session.get("main", "-"))
        return out
    if session.get("kind") == "custom":
        return _lighten_easy(session)
    return _easy(40)


def _reschedule_missed_key(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("tag") in {"marathon-long", "specific-long"}:
        return _custom(
            "Sortie longue marathon allegee",
            category="quality",
            warmup="10' tres facile (montee progressive en allure)",
            main=f"75-85' dont 2 x 15' a {GOAL_PACE}, recup 5' facile",
            cooldown="5' tres facile",
            tag="marathon-long-rescheduled",
        )
    if session.get("kind") == "custom" and session.get("category") == "quality":
        # Decaler reellement la seance cle, en version allegee, plutot que de
        # la remplacer par de la recuperation.
        out = deepcopy(session)
        out["title"] = f"{session.get('title', 'Seance cle')} (allegee, decalee)"
        # Reduire reellement les repetitions : sinon le texte annonce "-25%" en
        # gardant le volume plein, et l'estimation affichee reste celle de la
        # seance complete (titre incoherent avec le contenu).
        out["main"] = _lightened_quality_main(session.get("main", "-"))
        return out
    return _lighten_quality(session)


def _looks_long(run: dict[str, Any]) -> bool:
    return (run.get("distance_km") or 0) >= 13 or (run.get("moving_time") or 0) >= 70 * 60


def _looks_like_sl_effort(run: dict[str, Any]) -> bool:
    """Magnitude d'une VRAIE sortie longue (pour juger qu'une SL a ete faite en
    avance). Un medium-long de 13-15 km ne doit pas etre pris pour la SL du plan
    (souvent 18-32 km) et faire sauter la sortie longue prevue."""
    return (run.get("distance_km") or 0) >= 16 or (run.get("moving_time") or 0) >= 95 * 60


def _looks_quality(run: dict[str, Any]) -> bool:
    pace = run.get("pace_sec_per_km") or 0
    avg_hr = run.get("average_heartrate") or 0
    max_hr = run.get("max_heartrate") or 0
    # Allure globale soutenue tenue, ou effort globalement dur (FC moyenne haute).
    if pace > 0 and pace <= 285:
        return True
    if avg_hr >= 152:
        return True
    # Un simple pic de FC ne suffit PAS : un footing facile fini en acceleration
    # (max >= 170 mais FC moyenne basse et allure lente) n'est pas une qualite.
    # On exige que la course ait aussi ete globalement soutenue (FC moyenne +
    # allure) pour eviter ces faux positifs qui faisaient sauter seuils et SL.
    if max_hr >= 170 and avg_hr >= 143 and 0 < pace <= 315:
        return True
    return False


def _is_hard_or_long_run(run: dict[str, Any]) -> bool:
    return _looks_long(run) or _looks_quality(run)


# Intensite relative de chaque type d'effort reellement couru.
_EFFORT_RANK = {"easy": 1, "steady": 2, "quality": 3, "long": 3}


def _classify_effort(run: dict[str, Any]) -> tuple[str, str]:
    """Qualifie ce qui a reellement ete couru, independamment du plan."""
    if _looks_long(run):
        return "long", "Sortie longue deja faite"
    if _looks_quality(run):
        return "quality", "Seance qualite deja faite"
    pace = run.get("pace_sec_per_km") or 0
    if 0 < pace <= 320:
        return "steady", "Endurance moyenne deja faite"
    return "easy", "Footing deja fait"


def _is_true_quality_run(run: dict[str, Any]) -> bool:
    return _classify_effort(run)[0] == "quality"


def _effort_vs_plan(run: dict[str, Any], session: dict[str, Any]) -> str:
    """Compare l'effort reel a la seance prevue, dans les deux sens.

    Retourne 'harder' (effort plus gros que prevu), 'lighter' (effort plus
    leger que prevu, seance cle non couverte incluse) ou 'matching'.
    """
    effort_kind, _ = _classify_effort(run)
    actual_rank = _EFFORT_RANK.get(effort_kind, 1)
    category = session.get("category")
    if category in {"quality", "long"}:
        if _planned_key_was_completed(session, run):
            return "matching"
        if category == "long" and effort_kind == "long":
            return "lighter"
        planned_rank = _EFFORT_RANK.get(category, 3)
        return "harder" if actual_rank >= planned_rank else "lighter"
    if category == "rest":
        # Un footing leger sur jour de repos ne declenche rien.
        return "harder" if actual_rank >= 2 else "matching"
    planned_rank = 2 if session.get("tag") == "steady" else 1
    if actual_rank > planned_rank:
        return "harder"
    if actual_rank < planned_rank:
        return "lighter"
    return "matching"


def _complete_today(run: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    effort_kind, done_title = _classify_effort(run)
    details = [f"{run['distance_km']:.1f} km en {_fmt_duration(run.get('moving_time'))}"]
    if run.get("pace_sec_per_km"):
        details.append(f"{_fmt_pace(run['pace_sec_per_km'])}/km")
    if run.get("average_heartrate"):
        details.append(f"{int(round(run['average_heartrate']))} bpm")
    summary = ", ".join(details)
    direction = _effort_vs_plan(run, session)
    if direction == "lighter" and session.get("category") == "long" and effort_kind == "long":
        done_title = "Sortie longue partielle deja faite"
    planned_title = _session_title(session)
    if session.get("kind") == "rest":
        if direction == "harder":
            main = (
                f"Le plan prevoyait du repos, mais la seance reelle est la : {summary}. "
                "Je requalifie le jour sur ce qui a ete couru ; pas de charge supplementaire aujourd'hui."
            )
        else:
            main = f"Jour de repos converti en footing leger ({summary}) : acceptable, on en reste la."
    elif direction == "lighter" and session.get("category") in {"quality", "long"}:
        main = (
            f"Seance reelle plus legere que prevu ({summary}) : la seance cle "
            f"({planned_title}) n'est pas faite. Ne l'empile pas aujourd'hui ; "
            "elle sera decalee en version allegee dans les 48 h si la fraicheur le permet."
        )
    elif direction == "harder":
        main = (
            f"Seance reelle ({summary}) plus exigeante que la seance prevue "
            f"({planned_title}) : c'est elle qui compte. "
            "Pas de charge supplementaire aujourd'hui."
        )
    elif direction == "lighter":
        main = (
            f"Seance reelle ({summary}) plus legere que la seance prevue "
            f"({planned_title}) : on en reste la pour aujourd'hui, sans compenser."
        )
    else:
        main = (
            f"Seance du jour deja couverte: {summary}. "
            "N'ajoute pas de charge supplementaire aujourd'hui."
        )
    completed = {
        "kind": "custom",
        "category": "done",
        "title": done_title,
        "warmup": "Seance deja lancee aujourd'hui",
        "main": main,
        "cooldown": "5-10' de marche ou mobilite legere",
    }
    if session.get("kind") == "easy" and session.get("strides") and effort_kind in {"easy", "steady"}:
        completed["main"] += " Option seulement si jambes tres fraiches: 4-6 lignes de 20''."
    return completed


def _has_elevated_easy_hr(run: dict[str, Any]) -> bool:
    pace = run.get("pace_sec_per_km") or 0
    avg_hr = run.get("average_heartrate") or 0
    return 315 <= pace <= 365 and avg_hr >= 152


def _looks_progressive(run: dict[str, Any]) -> bool:
    pace = run.get("pace_sec_per_km") or 0
    avg_hr = run.get("average_heartrate") or 0
    return pace > 0 and pace <= 295 and (avg_hr == 0 or avg_hr <= 150)


def _planned_key_was_completed(session: dict[str, Any], run: dict[str, Any] | None) -> bool:
    if not run:
        return False
    if session.get("category") == "long" or session.get("tag") in {"specific-long", "marathon-long", "medium-long"}:
        return _planned_long_was_completed(session, run)
    if session.get("tag") == "marathon-pace":
        return _planned_marathon_pace_was_completed(session, run)
    if session.get("category") == "quality":
        return _planned_quality_was_completed(session, run)
    return _looks_quality(run)


def _planned_am_km(session: dict[str, Any]) -> float | None:
    """Volume d'allure marathon reellement prevu par la seance (en km)."""
    main = session.get("main") or ""
    reps = re.search(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*km", main)
    if reps:
        return int(reps.group(1)) * float(reps.group(2).replace(",", "."))
    single = re.search(r"(\d+(?:[.,]\d+)?)\s*km[^.]*?\d:\d{2}", main)
    if single:
        return float(single.group(1).replace(",", "."))
    return None


def _planned_marathon_pace_was_completed(session: dict[str, Any], run: dict[str, Any]) -> bool:
    """Un bloc AM n'est couvert que par un effort reellement a allure marathon.

    Les seuils absolus precedents se trompaient dans les
    deux sens : une sortie longue lente de 20 km validait "AM 5 x 2 km", tandis
    qu'un rappel de taper "3 x 1 km" courru pile n'etait jamais reconnu.
    """
    pace = run.get("pace_sec_per_km") or 0
    if not 0 < pace <= MARATHON_PACE_MAX_SEC:
        return False
    distance_km = run.get("distance_km") or 0
    planned_km = _planned_am_km(session)
    if planned_km:
        return distance_km >= planned_km * LONG_COMPLETION_RATIO
    return distance_km >= 8


def _planned_quality_was_completed(session: dict[str, Any], run: dict[str, Any]) -> bool:
    """Qualite couverte seulement si le volume approche celui prevu."""
    if not _is_true_quality_run(run):
        return False
    _, planned_minutes = _estimate_effort(session)
    moving_time = run.get("moving_time") or 0
    if planned_minutes and moving_time:
        return moving_time >= planned_minutes * 60 * QUALITY_COMPLETION_RATIO
    return True


def _planned_long_was_completed(session: dict[str, Any], run: dict[str, Any]) -> bool:
    planned_km, planned_minutes = _estimate_effort(session)
    distance_km = run.get("distance_km") or 0
    moving_time = run.get("moving_time") or 0

    if planned_km and distance_km:
        return distance_km >= planned_km * LONG_COMPLETION_RATIO
    if planned_minutes and moving_time:
        return moving_time >= planned_minutes * 60 * LONG_COMPLETION_RATIO
    return _looks_like_sl_effort(run)


def _has_recent_true_quality_before(
    reference_day: date,
    recent_runs: list[dict[str, Any]],
    *,
    max_age: int = 3,
) -> bool:
    for run in recent_runs:
        age = _run_age(reference_day, run)
        if age is not None and 1 <= age <= max_age and _is_true_quality_run(run):
            return True
    return False


def _key_miss_block_reason(
    missed_day: date,
    missed_run: dict[str, Any] | None,
    recent_runs: list[dict[str, Any]],
) -> str | None:
    # Une charge alternative le jour meme compte deja dans les jambes. Une SL
    # normale 2-3 jours avant ne doit pas, elle, bloquer un seuil a recaler.
    if missed_run is not None and _is_hard_or_long_run(missed_run):
        return "alternate_load"
    if _has_recent_true_quality_before(missed_day, recent_runs):
        return "recent_quality"
    return None


def _is_target_wake_sleep(latest_sleep: dict[str, Any] | None, target_day: date) -> bool:
    """Garmin sleep dates are wake-up days; only that exact day can steer J."""
    if not latest_sleep or not latest_sleep.get("date"):
        return False
    try:
        sleep_day = _parse_day(latest_sleep["date"])
    except (TypeError, ValueError):
        return False
    return sleep_day == target_day


def _latest_sleep_flags(latest_sleep: dict[str, Any] | None, target_day: date) -> dict[str, bool]:
    """Seul un sommeil VRAIMENT mauvais (score < 60 ou < 5h30) modifie la seance.

    Le palier intermediaire "cautious" a ete retire : un sommeil moyen ne doit
    pas alleger une seance cle en pleine prepa.
    """
    if not _is_target_wake_sleep(latest_sleep, target_day):
        return {"poor": False}
    score = latest_sleep.get("sleep_score") if latest_sleep else None
    duration = latest_sleep.get("sleep_duration_seconds") if latest_sleep else None
    poor = (score is not None and score < 60) or (duration is not None and duration < 19800)
    return {"poor": poor}


def _summarize_runs(recent_runs: list[dict[str, Any]], latest_sleep: dict[str, Any] | None, target_day: date) -> str:
    if not recent_runs:
        text = "Aucun run recent charge."
    else:
        chunks = []
        for run in recent_runs[:3]:
            part = f"{_fmt_short_day(run['date'])}: {run['distance_km']:.1f} km a {_fmt_pace(run.get('pace_sec_per_km'))}/km"
            if run.get("average_heartrate"):
                part += f", {int(round(run['average_heartrate']))} bpm"
            chunks.append(part)
        total_km = sum((run.get("distance_km") or 0) for run in recent_runs[:10])
        suffix = f" | 10 derniers runs: {total_km:.1f} km" if len(recent_runs) > 3 else ""
        text = " | ".join(chunks) + suffix
    if _is_target_wake_sleep(latest_sleep, target_day) and latest_sleep.get("sleep_score") is not None:
        text += f". Sommeil {latest_sleep['sleep_score']}/100"
        if latest_sleep.get("sleep_quality"):
            text += f" ({str(latest_sleep['sleep_quality']).lower()})"
    return text


def _run_age(day: date, run: dict[str, Any]) -> int | None:
    try:
        return (day - _parse_day(run.get("date"))).days
    except (TypeError, ValueError):
        return None


def _recent_context(recent_runs: list[dict[str, Any]], day: date) -> dict[str, Any]:
    runs = []
    for run in recent_runs[:10]:
        age = _run_age(day, run)
        if age is None or age < 0:
            continue
        runs.append((age, run))
    runs.sort(key=lambda item: item[0])

    runs_7 = [run for age, run in runs if age <= 6]
    latest_age = runs[0][0] if runs else None
    last_long = next(((age, run) for age, run in runs if _looks_long(run)), None)
    # "Derniere qualite" = une vraie seance de qualite/tempo, PAS une sortie
    # longue : une SL avec un simple pic de FC ne doit pas etre comptee comme
    # une qualite (sinon elle annule a tort un seuil planifie 1-3 jours apres).
    # _classify_effort renvoie "long" en priorite -> une SL n'est jamais "quality".
    last_quality = next(((age, run) for age, run in runs if _is_true_quality_run(run)), None)
    longest_recent_km = max((run.get("distance_km") or 0 for _, run in runs), default=0)
    km_7 = sum((run.get("distance_km") or 0) for run in runs_7)
    km_10 = sum((run.get("distance_km") or 0) for _, run in runs)
    hard_or_long_last_2 = any(age <= 2 and _is_hard_or_long_run(run) for age, run in runs)
    elevated_easy_hr = any(age <= 4 and _has_elevated_easy_hr(run) for age, run in runs)

    return {
        "runs": runs,
        "runs_7": runs_7,
        "latest_age": latest_age,
        "last_long_age": last_long[0] if last_long else None,
        "last_quality_age": last_quality[0] if last_quality else None,
        "longest_recent_km": longest_recent_km,
        "km_7": km_7,
        "km_10": km_10,
        "run_count_7": len(runs_7),
        "hard_or_long_last_2": hard_or_long_last_2,
        "elevated_easy_hr": elevated_easy_hr,
    }


def _phase_for(day: date) -> str:
    if day >= RACE_DAY:
        return "race"
    if day >= RACE_DAY - timedelta(days=6):
        return "race_week"
    if day >= RACE_DAY - timedelta(days=20):
        return "taper"
    if day >= RACE_DAY - timedelta(days=34):
        return "peak"
    if day >= PLAN_START + timedelta(days=32):
        return "specific"
    if day >= PLAN_START + timedelta(days=4):
        return "base"
    return "reprise"


def _adaptive_easy(minutes: int = 45, *, strides: bool = False) -> dict[str, Any]:
    suffix = " + 4-6 lignes de 20'' relachees" if strides else ""
    title = "Footing facile + lignes" if strides else "Footing facile"
    return _easy_plan(title, f"{minutes}' a {EASY_PACE}{suffix}")


def _adaptive_recovery(minutes: int = 35) -> dict[str, Any]:
    return _easy_plan("Footing de recuperation", f"{minutes}' tres facile a {RECOVERY_PACE}", tag="recovery")


def _adaptive_steady(minutes: int = 60) -> dict[str, Any]:
    return _easy_plan("Endurance moyenne", f"{minutes}' a {STEADY_PACE}", tag="steady")


def _adaptive_quality(day: date, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    week_index = max(0, (day - PLAN_START).days // 7)
    if phase == "race_week":
        return _quality_plan("Rappel allure marathon", "30' facile dont 3 x 1 km a 5:30/km, recup 2' facile", tag="marathon-pace")
    if phase == "taper":
        return _quality_plan("Allure marathon controlee", "3 x 2 km a 5:30/km, recup 1' trot", tag="marathon-pace")
    if phase in {"specific", "peak"}:
        if week_index % 2 == 0:
            return _quality_plan("Bloc allure marathon", "2 x 5 km a 5:30/km, recup 2' trot", tag="marathon-pace")
        return _quality_plan("Seuil controle", "3 x 10' a 4:18-4:28/km, recup 3' trot", tag="threshold")
    if week_index % 2 == 0:
        return _quality_plan("Seuil progressif", "3 x 8' a 4:18-4:28/km, recup 2' trot", tag="threshold")
    return _quality_plan("Rappel vitesse", "6 x 400 m a 3:55-4:08/km, recup 1'30 trot", tag="vo2")


def _adaptive_long(ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    longest = ctx["longest_recent_km"] or 0
    if phase == "reprise":
        return _long_plan("Sortie longue facile", "60-70' facile, 12-13 km sans bloc rapide")
    if phase == "base":
        target = min(20, max(16, int(round(longest + 2)) if longest else 16))
        # Blocs AM des la phase de base (base acquise, demande du 17 juil) :
        # 4 km a 16 km, 6 a 18, 8 a 20.
        block = max(4, min(8, target - 12))
        return _long_plan(f"SL {target} km avec AM", f"{target} km dont les {block} derniers a 5:25-5:35/km")
    if phase == "specific":
        target = min(30, max(22, int(round(longest + 2)) if longest else 22))
        if ctx["km_7"] >= 62:
            target = max(20, target - 4)
        am_block = "8-10 km" if target < 26 else "10-12 km"
        return _long_plan(f"SL {target} km avec AM", f"{target} km dont {am_block} a 5:25-5:35/km")
    if phase == "peak":
        if ctx["last_long_age"] is None or ctx["last_long_age"] >= 7:
            return _long_plan("SL pic marathon", "28-32 km avec 3 blocs de 5-6 km a 5:25-5:35/km")
        return _long_plan("Semi test ou 21 km AM", "21 km dont 12 km a allure marathon, ou semi test controle")
    if phase == "taper":
        return _long_plan("Derniere SL allegee", "16-22 km facile, avec 5 km maximum a allure marathon si jambes fraiches")
    return _adaptive_easy(35)


def _adaptive_schedule_for(day: date, recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    if day == RACE_DAY:
        return _race(RACE_NAME)
    if day > RACE_DAY:
        return _adaptive_recovery(30)

    ctx = _recent_context(recent_runs, day)
    phase = _phase_for(day)
    weekday = day.weekday()
    fatigued = (
        ctx["hard_or_long_last_2"]
        or ctx["elevated_easy_hr"]
        or ctx["run_count_7"] >= 6
        or ctx["km_7"] >= 75
    )

    if fatigued and weekday != 5:
        return _adaptive_recovery(35)

    missed_long = ctx["last_long_age"] is None or ctx["last_long_age"] > 10
    if missed_long and not fatigued and weekday == 5:
        return _long_plan("Sortie longue reportee", "75-95' facile a 6:15-6:50/km, sans bloc rapide")

    if weekday == 0:
        if ctx["last_quality_age"] is None or ctx["last_quality_age"] >= 5:
            return _adaptive_quality(day, ctx, phase)
        return _adaptive_steady(55)
    if weekday == 1:
        if ctx["last_quality_age"] is None or ctx["last_quality_age"] >= 5:
            return _adaptive_quality(day, ctx, phase)
        return _adaptive_easy(45 if phase in {"reprise", "taper", "race_week"} else 55, strides=not fatigued)
    if weekday == 2:
        return _adaptive_easy(45 if phase in {"reprise", "taper", "race_week"} else 55, strides=not fatigued)
    if weekday == 3:
        if ctx["run_count_7"] <= 2 and ctx["latest_age"] is not None and ctx["latest_age"] >= 2:
            return _adaptive_easy(35)
        return _rest()
    if weekday == 4:
        return _adaptive_easy(30 if phase == "race_week" else 40)
    if weekday == 5:
        if fatigued:
            return _adaptive_recovery(40)
        return _adaptive_long(ctx, phase)
    if weekday == 6:
        return _rest()
    return _adaptive_easy(45)


def _schedule_for(day: date, recent_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    planned = PLAN_CALENDAR.get(day.isoformat())
    if planned is not None:
        return deepcopy(planned)
    if recent_runs is not None:
        return _adaptive_schedule_for(day, recent_runs)
    return _adaptive_schedule_for(day, [])


def _is_shiftable_advance_target(session: dict[str, Any]) -> bool:
    """Seances que l'on peut considerer comme avancees d'un jour."""
    if session.get("category") == "quality":
        return True
    return session.get("tag") == "steady"


def _run_covers_shiftable_session(run: dict[str, Any], session: dict[str, Any]) -> bool:
    if not _is_shiftable_advance_target(session):
        return False
    if session.get("category") == "quality":
        if _classify_effort(run)[0] == "long":
            return False
        return _planned_key_was_completed(session, run)
    effort_kind, _ = _classify_effort(run)
    pace = run.get("pace_sec_per_km") or 0
    distance_km = run.get("distance_km") or 0
    moving_time = run.get("moving_time") or 0
    return (
        effort_kind == "steady"
        or (
            effort_kind == "easy"
            and 0 < pace <= 335
            and (distance_km >= 9.0 or moving_time >= 50 * 60)
        )
    )


def _advanced_session_from_run(
    run: dict[str, Any] | None,
    recent_runs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Detecte si un run couvre la seance planifiee le lendemain."""
    if not run:
        return None
    try:
        run_day = _parse_day(run.get("date"))
    except (TypeError, ValueError):
        return None

    current_session = _schedule_for(run_day, recent_runs)
    target_day = run_day + timedelta(days=1)
    target_session = _schedule_for(target_day, recent_runs)
    if not _run_covers_shiftable_session(run, target_session):
        return None
    if _run_covers_shiftable_session(run, current_session):
        return None
    return {
        "run_day": run_day,
        "target_day": target_day,
        "target_session": target_session,
    }


def _next_long_day(start: date, recent_runs: list[dict[str, Any]]) -> date | None:
    for offset in range(0, 21):
        day = start + timedelta(days=offset)
        if _schedule_for(day, recent_runs).get("category") == "long":
            return day
    return None


def _active_advance_for_day(
    day: date,
    recent_runs: list[dict[str, Any]],
    as_of: date,
) -> dict[str, Any] | None:
    candidates = []
    for run in recent_runs[:10]:
        try:
            run_day = _parse_day(run.get("date"))
        except (TypeError, ValueError):
            continue
        if run_day > as_of:
            continue
        advance = _advanced_session_from_run(run, recent_runs)
        if advance is None:
            continue
        next_long = _next_long_day(advance["target_day"], recent_runs)
        if day < advance["target_day"]:
            continue
        if next_long is not None and day >= next_long:
            continue
        candidates.append({**advance, "next_long_day": next_long})

    if not candidates:
        return None
    candidates.sort(key=lambda item: item["run_day"], reverse=True)
    return candidates[0]


def _advanced_shift_for_day(
    day: date,
    recent_runs: list[dict[str, Any]],
    as_of: date,
) -> tuple[dict[str, Any], str] | None:
    advance = _active_advance_for_day(day, recent_runs, as_of)
    if advance is None:
        return None

    next_long = advance.get("next_long_day")
    pulled_day = day + timedelta(days=1)
    advanced_title = _session_title(advance["target_session"])
    long_note = ""
    if next_long is not None:
        long_note = f" La prochaine SL ({_fmt_short_day(next_long.isoformat())}) reste a sa date."

    if next_long is not None and pulled_day >= next_long:
        return _adaptive_recovery(35), (
            f"{advanced_title} courue avec 1 jour d'avance le "
            f"{_fmt_short_day(advance['run_day'].isoformat())} : veille de SL gardee legere."
            f"{long_note}"
        )

    shifted_session = _schedule_for(pulled_day, recent_runs)
    return shifted_session, (
        f"{advanced_title} courue avec 1 jour d'avance le "
        f"{_fmt_short_day(advance['run_day'].isoformat())} : j'avance le reste "
        f"de la semaine d'un jour jusqu'a la prochaine SL.{long_note}"
    )


def _is_taper(day: date) -> bool:
    return day >= TAPER_START


def _was_unplanned_hard(run: dict[str, Any], recent_runs: list[dict[str, Any]]) -> bool:
    """Vrai si ce run reel est dur/long alors que le plan du jour ne l'etait pas."""
    if not _is_hard_or_long_run(run):
        return False
    try:
        run_day = _parse_day(run.get("date"))
    except (TypeError, ValueError):
        return False
    planned = _schedule_for(run_day, recent_runs)
    return planned.get("category") not in {"quality", "long", "race"}


def _reconcile_preview_with_reality(
    day: date,
    base_session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    as_of: date,
) -> tuple[dict[str, Any], str] | None:
    """Ajuste une seance planifiee quand les runs reels contredisent deja le plan.

    Le calendrier issu du PDF sert de structure, mais la realite prime, dans
    les deux sens : un effort plus gros que prevu allege la suite, et une
    seance cle remplacee par plus leger est decalee en version allegee.
    """
    if day >= TAPER_START:
        return None
    category = base_session.get("category")
    if category not in {"quality", "easy", "rest", "long"}:
        return None

    advanced_shift = _advanced_shift_for_day(day, recent_runs, as_of)
    if advanced_shift is not None:
        return advanced_shift

    # Sens "moins que prevu" : la seance cle de la veille (jour termine avec
    # un run enregistre, donc jugeable) n'a pas ete couverte -> decalee ici.
    prev_day = day - timedelta(days=1)
    if category in {"easy", "rest"} and prev_day == as_of:
        prev_run = next(
            (run for run in recent_runs if run.get("date") == prev_day.isoformat()),
            None,
        )
        prev_session = _schedule_for(prev_day, recent_runs)
        if (
            prev_run is not None
            and prev_session.get("category") in {"quality", "long"}
            and not _planned_key_was_completed(prev_session, prev_run)
        ):
            block_reason = _key_miss_block_reason(prev_day, prev_run, recent_runs)
            if block_reason == "alternate_load":
                return _adaptive_recovery(35), (
                    "Seance cle de la veille non couverte, mais l'effort reel etait "
                    "deja chargeant : recuperation, pas de rattrapage empile."
                )
            if block_reason == "recent_quality":
                return deepcopy(base_session), (
                    "Seance cle de la veille non couverte, mais une vraie qualite "
                    "recente pese deja : je garde le programme leger prevu."
                )
            return _reschedule_missed_key(prev_session), (
                "Seance cle de la veille remplacee par plus leger : "
                "je la decale ici en version allegee."
            )
    if category == "rest":
        return None

    ctx = _recent_context(recent_runs, day)
    if category == "long":
        # Regle du plan : une VRAIE SL courue avec 1-2 jours d'avance couvre la
        # SL planifiee -> le jour initialement prevu devient recuperation. Un
        # simple medium-long (13-15 km) ne compte pas comme la SL du plan.
        if any(age <= 2 and _planned_long_was_completed(base_session, run) for age, run in ctx["runs"]):
            return _adaptive_recovery(35), (
                "Sortie longue deja courue en avance : le jour prevu passe en recuperation."
            )
        return None
    unplanned_hard_yesterday = any(
        age <= 1 and _was_unplanned_hard(run, recent_runs)
        for age, run in ctx["runs"]
    )
    if category == "quality":
        quality_age = ctx["last_quality_age"]
        if quality_age is not None and quality_age <= 3:
            note = (
                f"Qualite deja faite il y a {quality_age} jour(s) : "
                "je remplace la qualite prevue par du footing facile."
            )
            return _adaptive_easy(45), note
        if unplanned_hard_yesterday:
            return _adaptive_recovery(35), (
                "Effort non planifie la veille : la qualite prevue bascule en recuperation."
            )
        return None
    if unplanned_hard_yesterday:
        return _adaptive_recovery(35), (
            "Effort non planifie la veille : la seance prevue bascule en footing de recuperation."
        )
    return None


def build_daily_training_guidance(
    target_day: str | date | datetime | None,
    recent_runs: list[dict[str, Any]] | None,
    latest_sleep: dict[str, Any] | None = None,
    *,
    as_of_day: str | date | datetime | None = None,
    apply_adjustments: bool = True,
) -> dict[str, Any]:
    day = _parse_day(target_day)
    as_of = _parse_day(as_of_day) if as_of_day is not None else day
    recent_runs = list(recent_runs or [])
    base_session = _schedule_for(day, recent_runs)
    session = deepcopy(base_session)
    today_iso = day.isoformat()
    yesterday_iso = (day - timedelta(days=1)).isoformat()
    sleep_flags = _latest_sleep_flags(latest_sleep, day)
    context = _recent_context(recent_runs, day)

    today_run = next((run for run in recent_runs if run.get("date") == today_iso), None)
    yesterday_run = next((run for run in recent_runs if run.get("date") == yesterday_iso), None)

    yesterday_session = _schedule_for(day - timedelta(days=1), recent_runs)
    had_yesterday_key = yesterday_session.get("category") in {"quality", "long"}
    missed_yesterday_key = (
        day == as_of
        and had_yesterday_key
        and not _planned_key_was_completed(yesterday_session, yesterday_run)
        and not _is_taper(day)
    )
    missed_yesterday_block_reason = (
        _key_miss_block_reason(day - timedelta(days=1), yesterday_run, recent_runs)
        if missed_yesterday_key
        else None
    )
    yesterday_hard = bool(yesterday_run and _is_hard_or_long_run(yesterday_run))
    recent_long = context["last_long_age"] is not None and context["last_long_age"] <= 2
    recent_sl = (
        base_session.get("category") == "long"
        and any(age <= 2 and _planned_long_was_completed(base_session, run) for age, run in context["runs"])
    )
    recent_true_quality = context["last_quality_age"] is not None and context["last_quality_age"] <= 2
    elevated_easy_hr = context["elevated_easy_hr"]
    positive_trend = any(age <= 5 and _looks_progressive(run) for age, run in context["runs"])

    status = "rest" if base_session.get("kind") == "rest" else "scheduled"
    adjustment = "Rien a changer."
    rescheduled_missed_key = False
    shifted_from_advance = None
    if apply_adjustments and not today_run:
        shifted_from_advance = _advanced_shift_for_day(day, recent_runs, as_of)

    if apply_adjustments and today_run and day <= as_of:
        status = "done"
        session = _complete_today(today_run, base_session)
        direction = _effort_vs_plan(today_run, base_session)
        advanced = _advanced_session_from_run(today_run, recent_runs)
        if advanced is not None:
            adjustment = (
                f"{_session_title(advanced['target_session'])} etait prevue demain : "
                "elle est absorbee avec 1 jour d'avance. J'avance les prochains "
                "jours d'un cran jusqu'a la prochaine SL, qui reste a sa date."
            )
        elif base_session.get("kind") == "rest" and direction == "harder":
            adjustment = (
                "Seance non prevue par le plan absorbee aujourd'hui : "
                "pas de rattrapage, les prochains jours restent legers."
            )
        elif direction == "lighter" and base_session.get("category") in {"quality", "long"}:
            adjustment = (
                "Seance cle prevue non couverte aujourd'hui : pas de rattrapage empile, "
                "je la decale en version allegee sur le prochain jour leger."
            )
        elif direction == "harder":
            adjustment = (
                "La seance reelle est plus grosse que prevu : je m'aligne sur ce qui "
                "a ete couru, pas de second bloc aujourd'hui."
            )
        elif direction == "lighter":
            adjustment = (
                "Seance plus legere que prevu : pas de compensation aujourd'hui, "
                "on garde le cap sur la prochaine seance cle."
            )
        else:
            adjustment = "Seance deja faite aujourd'hui : pas de second bloc a ajouter."
    elif apply_adjustments and shifted_from_advance is not None:
        session, adjustment = shifted_from_advance
        status = "rest" if session.get("kind") == "rest" else "scheduled"
    elif apply_adjustments and missed_yesterday_key and missed_yesterday_block_reason == "alternate_load":
        status = "scheduled"
        session = _adaptive_recovery(35)
        adjustment = "Seance cle non couverte hier, mais l'effort reel etait deja chargeant : recuperation aujourd'hui, pas de rattrapage empile."
    elif apply_adjustments and missed_yesterday_key and missed_yesterday_block_reason == "recent_quality":
        adjustment = "Seance cle manquee hier, mais une vraie qualite recente charge deja les jambes : je garde le programme leger prevu."
    elif apply_adjustments and missed_yesterday_key and base_session.get("category") in {"easy", "rest"}:
        status = "scheduled"
        session = _reschedule_missed_key(_schedule_for(day - timedelta(days=1), recent_runs))
        rescheduled_missed_key = True
        adjustment = "Seance cle manquee hier : je la decale aujourd'hui en version allegee, sans empiler la charge."
    elif apply_adjustments and (
        sleep_flags["poor"]
        or elevated_easy_hr
        or yesterday_hard
        or recent_long
        or recent_true_quality
    ):
        status = "scheduled"
        if base_session.get("category") == "quality":
            # Prepa marathon : une sortie longue recente (ou de la veille) ne doit
            # PAS faire sauter un seuil planifie. On ne deload la qualite que sur
            # un vrai signal de fatigue : sommeil bas, FC easy elevee, ou une
            # VRAIE seance de qualite recente (last_quality_age exclut deja les SL).
            if sleep_flags["poor"] or elevated_easy_hr or recent_true_quality:
                session = _easy(40)
                session["pace_range"] = RECOVERY_PACE
                adjustment = "Recuperation encore limitee : je remplace la qualite par 40' facile pour privilegier la fraicheur."
            else:
                # Seule une sortie longue recente a declenche la branche : en prepa
                # on garde le seuil, on ne l'annule pas pour une SL.
                adjustment = "Sortie longue recente, mais la seance de qualite prevue reste : en prepa on ne l'annule pas pour une SL."
        else:
            genuine_fatigue = sleep_flags["poor"] or elevated_easy_hr
            if base_session.get("category") == "long" and recent_sl:
                session = _adaptive_recovery(35)
                adjustment = "Sortie longue deja courue en avance : le jour prevu passe en recuperation."
            elif base_session.get("category") == "long" and not genuine_fatigue:
                # Prepa marathon : un run long/moyen recent ne doit pas faire
                # sauter la sortie longue prevue. On la garde (sauf vraie fatigue
                # physiologique : sommeil bas ou FC elevee sur footing facile).
                adjustment = "Charge recente presente, mais la sortie longue prevue reste : on ne l'annule pas pour un run recent."
            else:
                session = _lighten_easy(base_session, long_day=recent_long or yesterday_hard)
                adjustment = "Charge recente encore presente : j'allege legerement la seance du jour pour rester prudent."
    elif apply_adjustments and positive_trend and base_session.get("tag") == "goal" and not _is_taper(day):
        status = "scheduled"
        session = deepcopy(base_session)
        if session.get("kind") == "intervals":
            session["pace_range"] = GOAL_PACE_TIGHT
        adjustment = "Rien a changer. Si les jambes sont tres souples, vise le bas de la fourchette d'allure sans forcer."
    elif apply_adjustments and _is_taper(day) and base_session.get("category") == "quality":
        adjustment = "Semaine d'affutage : on garde la seance telle quelle, sans ajouter de charge."

    if not apply_adjustments:
        # Mode apercu (J+1 et suivants) : la structure vient du plan, mais les runs
        # reellement effectues priment sur le calendrier fige.
        reconciled = _reconcile_preview_with_reality(day, base_session, recent_runs, as_of)
        if reconciled is not None:
            session, adjustment = reconciled
            status = "rest" if session.get("kind") == "rest" else "scheduled"

    rendered = _render_session(session)
    if _is_taper(day) and day < RACE_DAY and status != "done":
        rendered["main"] += " Priorite a la fraicheur."

    # Cibles FC (et allures) de la seance reellement retenue : la FC est
    # l'indicateur prioritaire a cibler sur les footings, repos et sorties
    # longues. Le frontend convertit les % de FCmax en bpm avec la FC max
    # reelle des 90 derniers jours.
    paces, hr = _session_paces_hr(session)

    status_label = {
        "done": "Deja fait",
        "scheduled": "A faire",
        "rest": "Repos",
    }.get(status, "A faire")

    # Distance et temps global de la seance, ajoutes dans le titre pour voir
    # d'un coup d'oeil le volume des prochains jours.
    est_km, est_minutes = _estimate_effort(session)
    title = _title_with_effort(rendered["title"], est_km, est_minutes)

    return {
        "date": today_iso,
        "dateLabel": _fmt_short_day(today_iso),
        "planSource": PLAN_SOURCE,
        "planDescription": PLAN_DESCRIPTION,
        "planBasis": PLAN_BASIS,
        "status": status,
        "statusLabel": status_label,
        "title": title,
        "category": session.get("category"),
        "tag": session.get("tag"),
        "estimatedKm": est_km,
        "estimatedMinutes": est_minutes,
        "estimatedDuration": _fmt_clock(est_minutes) if est_minutes else None,
        "observations": _summarize_runs(recent_runs, latest_sleep, day),
        "adjustment": adjustment,
        "session": {
            "warmup": rendered["warmup"],
            "main": rendered["main"],
            "cooldown": rendered["cooldown"],
        },
        "paces": paces,
        "hr": hr,
        "sleep": latest_sleep if _is_target_wake_sleep(latest_sleep, day) else None,
        "recentRuns": recent_runs[:10],
        "baseTitle": _session_title(base_session),
        "rescheduledMissedKey": rescheduled_missed_key,
        "workoutEligible": _is_workout_export_eligible(session),
    }


def build_three_day_training_guidance(
    target_day: str | date | datetime | None,
    recent_runs: list[dict[str, Any]] | None,
    latest_sleep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return today's adaptive guidance plus the next seven scheduled sessions."""
    anchor = _parse_day(target_day)
    recent_runs = list(recent_runs or [])
    sessions = []
    for offset in range(8):
        relative_label = "Aujourd'hui" if offset == 0 else f"J+{offset}"
        guidance = build_daily_training_guidance(
            anchor + timedelta(days=offset),
            recent_runs,
            latest_sleep,
            as_of_day=anchor,
            apply_adjustments=offset == 0,
        )
        guidance["relativeLabel"] = relative_label
        sessions.append(guidance)

    if sessions[0].get("rescheduledMissedKey") and sessions[0].get("title", "").startswith("Sortie longue marathon allegee"):
        recovery = _easy(35)
        recovery["pace_range"] = RECOVERY_PACE
        rec_km, rec_minutes = _estimate_effort(recovery)
        sessions[1].update({
            "title": _title_with_effort("Footing de recuperation", rec_km, rec_minutes),
            "estimatedKm": rec_km,
            "estimatedDuration": _fmt_clock(rec_minutes) if rec_minutes else None,
            "adjustment": "La sortie longue marathon a ete decalee : le lendemain reste court et sans lignes avant de remettre de la qualite.",
            "session": _render_session(recovery),
            "workoutEligible": _is_workout_export_eligible(recovery),
        })

    data_through = max(
        (run.get("start_date_local") or run.get("date") or "" for run in recent_runs),
        default="",
    )
    current = sessions[0]
    return {
        **current,
        "planSource": PLAN_SOURCE,
        "planDescription": PLAN_DESCRIPTION,
        "planBasis": PLAN_BASIS,
        "planPeriod": {
            "start": PLAN_START.isoformat(),
            "end": PLAN_END.isoformat(),
        },
        "dataThrough": data_through,
        "sessions": sessions,
    }


# ── Detail des seances (page Plan dediee) ──────────────────────────────────
# Chaque allure d'entrainement porte une cible FC exprimee en % de FC max :
# le frontend convertit en bpm avec la FC max reelle des 90 derniers jours.

PACE_REFS = [
    {"key": "recovery", "label": "Recuperation", "pace": RECOVERY_PACE, "hrPct": [0.60, 0.70],
     "usage": "Lendemain de seance dure, fatigue, sommeil bas. Conversation complete sans effort."},
    {"key": "easy", "label": "Footing facile", "pace": EASY_PACE, "hrPct": [0.65, 0.75],
     "usage": "Volume de base, aisance respiratoire totale. La grande majorite du kilometrage."},
    {"key": "steady", "label": "Endurance moyenne", "pace": STEADY_PACE, "hrPct": [0.73, 0.80],
     "usage": "Steady, consolidation aerobie. Soutenu mais jamais dur."},
    {"key": "marathon", "label": "Allure marathon (AM)", "pace": GOAL_PACE, "hrPct": [0.80, 0.88],
     "usage": "Blocs specifiques et jour J. Allure d'exemple a personnaliser."},
    {"key": "semi", "label": "Allure semi", "pace": SEMI_PACE, "hrPct": [0.85, 0.90],
     "usage": "Semi test (fin sept-debut oct) ou blocs longs controles."},
    {"key": "threshold", "label": "Seuil", "pace": THRESHOLD_PACE, "hrPct": [0.88, 0.92],
     "usage": "Tempo et repetitions de 6-10 min. FC stable sur chaque bloc, pas de derive."},
    {"key": "vo2", "label": "VO2 / fractionne", "pace": VO2_PACE, "hrPct": [0.92, 0.97],
     "usage": "Rappel vitesse, volume limite. La FC monte en fin de repetition seulement."},
    {"key": "strides", "label": "Lignes droites", "pace": STRIDES_PACE, "hrPct": None,
     "usage": "20'' relachees, recup 40'' trot : trop court pour que la FC soit un repere."},
]

PHASE_LABELS = {
    "reprise": "Reprise fonciere",
    "base": "Base + premiers blocs AM",
    "specific": "Specifique marathon",
    "peak": "Pic + semi test",
    "taper": "Affutage",
    "race_week": "Semaine de course",
    "race": "Course",
}

CATEGORY_LABELS = {
    "easy": "Facile",
    "quality": "Qualite",
    "long": "Sortie longue",
    "rest": "Repos",
    "race": "Course",
}

WORKOUT_EXPORT_QUALITY_TAGS = {"threshold", "vo2", "marathon-pace", "race-test", "quality"}


def _is_workout_export_eligible(session: dict[str, Any]) -> bool:
    if session.get("category") == "long":
        return True
    return session.get("category") == "quality" and session.get("tag") in WORKOUT_EXPORT_QUALITY_TAGS

FUEL_STRATEGY = {
    "title": "Gels et glucides — exemple de strategie",
    "diagnosis": (
        "Tester progressivement la strategie de ravitaillement pendant les sorties longues. "
        "La tolerance digestive varie selon les personnes et doit etre validee avant la course."
    ),
    "raceTarget": "60 g de glucides/h minimum le jour J (viser 70-80 g/h si l'intestin suit).",
    "rules": [
        "Lire la ligne glucides de l'etiquette, pas le poids du sachet : un gel de 30-40 g n'apporte souvent que 20-25 g de glucides.",
        "60 g/h = 2 a 3 gels par heure (Maurten 100 = 25 g, SIS Go = ~22 g, Decathlon/Overstim's = 20-27 g).",
        "SL < 20 km : 30-40 g/h pour habituer l'intestin. SL >= 20 km : monter vers 60 g/h.",
        "Les 4-5 dernieres SL avant la course a 60 g/h ou plus, exactement comme le jour J.",
        "1er gel tot (30-45 min), puis un toutes les 25-30 min, sans attendre la faim.",
        "Toujours avec de l'eau, jamais sec ni avec une boisson sucree (bolus trop concentre = troubles intestinaux).",
        "Privilegier les formules glucose + fructose (2:1 ou 1:0.8) : meilleure absorption, indispensable au-dela de 60 g/h.",
        "Si les gels passent mal : alterner boisson d'effort ou pates de fruits. C'est le debit de glucides qui compte, pas le format.",
    ],
}

RACE_STRATEGY = [
    {"segment": "Km 1-5", "pace": "allure cible + 10-15 s/km", "hrPct": [0.76, 0.82],
     "detail": "Depart controle : conserver de la marge et laisser la frequence cardiaque monter progressivement."},
    {"segment": "Km 5-30", "pace": GOAL_PACE, "hrPct": [0.82, 0.88],
     "detail": "Installer l'allure validee a l'entrainement sans depasser la zone cardiaque cible."},
    {"segment": "Apres km 30", "pace": "tenir, puis accelerer si possible", "hrPct": [0.86, 0.92],
     "detail": "Tenir posture et cadence. Accelerer legerement seulement si les sensations le permettent."},
    {"segment": "Passage semi", "pace": "selon l'objectif configure", "hrPct": None,
     "detail": "Rien de nouveau le jour J : meme ravitaillement, meme timing et meme petit-dejeuner qu'a l'entrainement."},
]


def _hr_ref(key: str) -> dict[str, Any] | None:
    for ref in PACE_REFS:
        if ref["key"] == key:
            return ref
    return None


def _hr_target(key: str, label: str, note: str = "") -> dict[str, Any] | None:
    ref = _hr_ref(key)
    if not ref or not ref.get("hrPct"):
        return None
    return {
        "label": label,
        "pctMin": ref["hrPct"][0],
        "pctMax": ref["hrPct"][1],
        "note": note,
    }


def _pace_chip(label: str, value: str, note: str = "") -> dict[str, str]:
    chip = {"label": label, "value": value}
    if note:
        chip["note"] = note
    return chip


def _fmt_clock(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}"


def _title_with_effort(title: str, km: float | None, minutes: int | None) -> str:
    """Ajoute distance et temps global de la seance au titre (Aujourd'hui/J+1/J+2)."""
    parts: list[str] = []
    if km:
        parts.append(f"~{round(km)} km")
    if minutes:
        parts.append(_fmt_clock(int(minutes)))
    if not parts:
        return title
    return f"{title} · {' · '.join(parts)}"


def _parse_first_km(text: str) -> float | None:
    # Premier "NN km" du texte en ignorant les repetitions type "5 x 2 km".
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+(?:[.,]\d+)?))?\s*km", text):
        prefix = text[max(0, match.start() - 8):match.start()]
        if re.search(r"x\s*$", prefix.strip().lower()):
            continue
        low = float(match.group(1).replace(",", "."))
        high = float(match.group(2).replace(",", ".")) if match.group(2) else low
        return (low + high) / 2
    return None


def _parse_main_minutes(text: str) -> int | None:
    # "40' a ...", "60-70' facile", "30' facile dont ..."
    match = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*'", text)
    if not match:
        return None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    return (low + high) // 2


def _session_text(session: dict[str, Any]) -> str:
    return f"{session.get('title', '')} {session.get('main', '')}"


def _has_am_block(session: dict[str, Any]) -> bool:
    text = _session_text(session)
    return (
        session.get("tag") == "marathon-pace"
        or "allure marathon" in text.lower()
        or " AM" in f" {text}"
    )


def _estimate_effort(session: dict[str, Any]) -> tuple[float | None, int | None]:
    """Retourne (km estimes, minutes estimees) pour l'affichage et le timing des gels."""
    kind = session.get("kind")
    category = session.get("category")
    if kind == "rest":
        return None, None
    if kind == "race":
        return 42.2, 200
    if kind in {"easy", "long"} and session.get("minutes"):
        minutes = int(session["minutes"])
        return round(minutes / 5.5, 1), minutes
    text = _session_text(session)
    if category == "long":
        km = _parse_first_km(text)
        if km:
            pace = 5.15 if _has_am_block(session) else 5.4
            return km, int(round(km * pace + 10))
        minutes = _parse_main_minutes(session.get("main", ""))
        if minutes:
            return round(minutes / 5.4, 1), minutes + 15
        return None, None
    if kind == "custom":
        main = session.get("main", "")
        if category == "quality":
            # Echauffement 12' + retour au calme 5' autour du corps de seance.
            # Si le corps de seance annonce une duree totale ("75-85' dont ..."),
            # elle inclut deja tout : on la prend telle quelle.
            head = main.split(" x ", 1)[0] if " x " in main else ""
            lead_total = _parse_main_minutes(head) if head else None
            if lead_total and lead_total >= 30:
                return None, lead_total
            reps_time = re.search(r"(\d+)\s*x\s*(\d+)\s*'", main)
            reps_km = re.search(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*km\b", main)
            reps_dist = re.search(r"(\d+)\s*x\s*(\d+)\s*m\b", main)
            if reps_time:
                n, mins = int(reps_time.group(1)), int(reps_time.group(2))
                work = n * mins + max(0, n - 1) * 2
            elif reps_km:
                n, dist_km = int(reps_km.group(1)), float(reps_km.group(2).replace(",", "."))
                work = int(round(n * (dist_km * 4.62 + 1.5)))
            elif reps_dist:
                n, dist = int(reps_dist.group(1)), int(reps_dist.group(2))
                work = int(round(n * (dist / 1000 * 4.1 + 1.5)))
            else:
                parsed = _parse_main_minutes(main)
                work = parsed if parsed and parsed >= 15 else None
            return None, (work + 17) if work else None
        minutes = _parse_main_minutes(main)
        if minutes is None or minutes < 15:
            return None, None
        return round(minutes / 5.5, 1), minutes + 3
    return None, None


def _fuel_plan(session: dict[str, Any], km: float | None, minutes: int | None) -> dict[str, Any] | None:
    kind = session.get("kind")
    category = session.get("category")

    if kind == "race":
        gels = []
        for i, race_km in enumerate([5, 10, 15, 20, 25, 30, 35], start=1):
            note = "gel cafeine possible" if race_km in {25, 30} else ""
            gels.append({
                "label": f"Gel {i}",
                "at": f"km {race_km}",
                "clock": _fmt_clock(int(round(race_km * 4.8))),
                "note": note,
            })
        return {
            "carbTarget": "60 g/h minimum (70-80 g/h si l'intestin suit)",
            "before": "Petit-dejeuner rode a l'entrainement 3h avant + 1 gel 15 min avant le depart.",
            "gels": gels,
            "notes": [
                "Un gel toutes les 25-30 min des le km 5, sans attendre la faim : au km 30 il est trop tard.",
                "Toujours avec de l'eau aux ravitaillements, jamais sec.",
                "Complement possible : boisson d'effort aux ravitos pour atteindre 60-70 g/h.",
                "Total attendu : 9-11 gels en comptant celui du depart — panaches gels + boisson.",
            ],
        }

    if category != "long" or not minutes or minutes < 65:
        return None

    is_race_like = bool(km and km >= 20)
    interval = 25 if is_race_like else 30
    first_gel = 40
    last_useful = minutes - 20
    gels = []
    minute = first_gel
    index = 1
    while minute <= last_useful:
        entry = {
            "label": f"Gel {index}",
            "at": _fmt_clock(minute),
            "clock": _fmt_clock(minute),
        }
        if km and minutes:
            entry["kmApprox"] = round(km * minute / minutes)
            entry["at"] = f"{_fmt_clock(minute)} (~km {entry['kmApprox']})"
        gels.append(entry)
        minute += interval
        index += 1

    if not gels:
        return None

    if is_race_like:
        carb_target = "60 g/h — repetition du protocole course"
        notes = [
            "SL >= 20 km : meme debit de glucides que le jour J (2-3 gels/h + eau).",
            "Memes gels que ceux prevus pour le marathon : on rode l'intestin ET le produit.",
            "Toujours avec de l'eau, jamais sec.",
        ]
    else:
        carb_target = "30-40 g/h — entrainement digestif"
        notes = [
            "Objectif : habituer l'intestin, pas la performance. 1er gel tot, sans attendre la faim.",
            "Toujours avec de l'eau, jamais sec.",
        ]
    return {"carbTarget": carb_target, "before": None, "gels": gels, "notes": notes}


def _session_paces_hr(session: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    kind = session.get("kind")
    category = session.get("category")
    tag = (session.get("tag") or "").strip()
    text = _session_text(session)
    paces: list[dict] = []
    hr: list[dict] = []

    def add_strides_if_any():
        if "ligne" in text.lower() or session.get("strides"):
            paces.append(_pace_chip("Lignes droites", STRIDES_PACE, "20'' relachees, recup 40'' trot"))

    if kind == "rest":
        # Jour de repos : pas d'allure imposée, mais une FC plafond utile si un
        # footing très léger est fait quand même — la FC pilote la régénération.
        hr_recovery = _hr_target(
            "recovery",
            "Repos actif (optionnel)",
            "Repos complet idéalement. Si tu bouges, reste sous cette zone : marche ou trot, jamais au-delà.",
        )
        if hr_recovery:
            hr.append(hr_recovery)
        return paces, hr

    if kind == "race":
        for seg in RACE_STRATEGY[:3]:
            paces.append(_pace_chip(seg["segment"], seg["pace"]))
            if seg.get("hrPct"):
                hr.append({
                    "label": seg["segment"],
                    "pctMin": seg["hrPct"][0],
                    "pctMax": seg["hrPct"][1],
                    "note": "",
                })
        return paces, hr

    if tag == "recovery":
        paces.append(_pace_chip("Recuperation", RECOVERY_PACE, "conversation complete, aucun chrono"))
        hr.append(_hr_target("recovery", "Recuperation", "Si la FC depasse cette zone : marcher, c'est un jour de regeneration."))
        return paces, hr

    if tag == "steady":
        paces.append(_pace_chip("Endurance moyenne", STEADY_PACE, "soutenu mais jamais dur"))
        hr.append(_hr_target("steady", "Endurance moyenne", "Derive FC < 5 bpm entre le debut et la fin : sinon ralentir."))
        add_strides_if_any()
        return paces, hr

    if tag == "race-test":
        paces.append(_pace_chip("Semi test", SEMI_PACE, "effort controle pour valider la cible marathon"))
        hr.append(_hr_target("semi", "Semi test", "FC de course controlee : c'est un test, pas une course a bloc."))
        return paces, hr

    if category == "quality":
        if tag == "vo2":
            paces.append(_pace_chip("Repetitions VO2", VO2_PACE, "reguliere sur toutes les reps, pas de sprint"))
            paces.append(_pace_chip("Recuperation entre reps", "trot tres facile", "laisser la FC redescendre"))
            hr.append(_hr_target("vo2", "Fin de repetition", "La FC n'atteint la zone qu'en fin de rep : piloter a l'allure, pas a la FC."))
            hr.append(_hr_target("easy", "Entre les repetitions", "Redescendre sous 75% avant de repartir."))
        elif tag == "threshold":
            paces.append(_pace_chip("Blocs seuil", THRESHOLD_PACE, "meme allure du premier au dernier bloc"))
            paces.append(_pace_chip("Recuperation entre blocs", "trot facile", ""))
            hr.append(_hr_target("threshold", "Pendant les blocs", "FC stable sur chaque bloc : si elle derive, l'allure est trop rapide."))
        elif tag == "marathon-pace":
            paces.append(_pace_chip("Blocs allure marathon", GOAL_PACE, "allure d'exemple a personnaliser"))
            hr.append(_hr_target("marathon", "Pendant les blocs AM", "Memoriser la FC a l'allure cible pour calibrer le jour J."))
        elif tag == "tempo":
            paces.append(_pace_chip("Tempo", "4:30/km", "entre seuil et allure marathon"))
            hr.append(_hr_target("semi", "Tempo", "Controle : sous la FC de seuil."))
        else:
            paces.append(_pace_chip("Corps de seance", session.get("pace_range", THRESHOLD_PACE)))
        paces.append(_pace_chip("Echauffement / retour au calme", "6:35-7:05/km", "tres facile"))
        hr_easy = _hr_target("easy", "Echauffement / retour au calme", "")
        if hr_easy:
            hr.append(hr_easy)
        return [p for p in paces if p], [h for h in hr if h]

    if category == "long":
        if _has_am_block(session):
            paces.append(_pace_chip("Partie facile", "6:15-6:50/km", "aisance totale, on economise pour le bloc"))
            paces.append(_pace_chip("Bloc allure marathon", GOAL_PACE, "allure d'exemple a personnaliser"))
            hr.append(_hr_target("easy", "Partie facile", "Rester bas : le bloc AM doit demarrer frais."))
            hr.append(_hr_target("marathon", "Bloc AM", "Noter la FC moyenne du bloc : c'est la reference jour J."))
        else:
            paces.append(_pace_chip("Sortie longue facile", "6:15-6:50/km", "volume, pas d'intensite"))
            hr.append(_hr_target("easy", "Toute la sortie", "Derive FC en fin de sortie normale si elle reste sous ~80%."))
        return [p for p in paces if p], [h for h in hr if h]

    # Footing facile par defaut
    paces.append(_pace_chip("Footing facile", "6:15-6:45/km", "aisance respiratoire totale"))
    hr.append(_hr_target("easy", "Footing", "Pouvoir parler en phrases completes tout du long."))
    add_strides_if_any()
    return [p for p in paces if p], [h for h in hr if h]


def _session_details(session: dict[str, Any]) -> dict[str, Any]:
    rendered = _render_session(session)
    km, minutes = _estimate_effort(session)
    paces, hr = _session_paces_hr(session)
    category = session.get("category", "easy")
    return {
        "title": rendered["title"],
        "kind": session.get("kind"),
        "category": category,
        "categoryLabel": CATEGORY_LABELS.get(category, category),
        "tag": session.get("tag"),
        "keySession": category in {"quality", "long", "race"},
        # Seules les SL et seances structurees utiles sur montre sont envoyables
        # vers Garmin Connect : pas les footings, pas l'endurance moyenne.
        "workoutEligible": _is_workout_export_eligible(session),
        "structure": {
            "warmup": rendered["warmup"],
            "main": rendered["main"],
            "cooldown": rendered["cooldown"],
        },
        "paces": paces,
        "hr": hr,
        "fuel": _fuel_plan(session, km, minutes),
        "estimatedKm": km,
        "estimatedMinutes": minutes,
        "estimatedDuration": _fmt_clock(minutes) if minutes else None,
    }


def build_workout_export(day: str | date | datetime | None) -> dict[str, Any] | None:
    """Structure + meta d'une seance Garmin, ou None si non eligible.

    Se base sur le calendrier fige (meme source que la page Plan). Seules les
    sorties longues et les seances qualite sont envoyables.
    """
    target = _parse_day(day)
    session = _schedule_for(target)
    category = session.get("category")
    if not _is_workout_export_eligible(session):
        return None
    rendered = _render_session(session)
    km, minutes = _estimate_effort(session)
    return {
        "date": target.isoformat(),
        "title": rendered["title"],
        "category": category,
        "tag": session.get("tag"),
        "structure": {
            "warmup": rendered["warmup"],
            "main": rendered["main"],
            "cooldown": rendered["cooldown"],
        },
        "estimatedKm": km,
        "estimatedMinutes": minutes,
    }


def build_plan_overview(target_day: str | date | datetime | None = None) -> dict[str, Any]:
    """Plan complet, semaine par semaine, avec le detail de chaque seance."""
    today = _parse_day(target_day)
    weeks_map: dict[date, list[tuple[date, dict[str, Any]]]] = {}
    for iso in sorted(PLAN_CALENDAR):
        day = date.fromisoformat(iso)
        monday = day - timedelta(days=day.weekday())
        weeks_map.setdefault(monday, []).append((day, PLAN_CALENDAR[iso]))

    weeks = []
    for index, monday in enumerate(sorted(weeks_map), start=1):
        sunday = monday + timedelta(days=6)
        sessions = []
        for day, session in weeks_map[monday]:
            details = _session_details(session)
            sessions.append({
                "date": day.isoformat(),
                "dayLabel": _fmt_short_day(day.isoformat()),
                "isToday": day == today,
                "isPast": day < today,
                **details,
            })
        phase = _phase_for(sunday if sunday < RACE_DAY else monday)
        km_total = sum(s["estimatedKm"] or 0 for s in sessions)
        weeks.append({
            "index": index,
            "start": monday.isoformat(),
            "end": sunday.isoformat(),
            "label": f"Semaine {index} — du {_fmt_short_day(monday.isoformat())} au {_fmt_short_day(sunday.isoformat())}",
            "phase": phase,
            "phaseLabel": PHASE_LABELS.get(phase, phase),
            "estimatedKm": int(round(km_total)),
            "isCurrent": monday <= today <= sunday,
            "isPast": sunday < today,
            "sessions": sessions,
        })

    return {
        "planSource": PLAN_SOURCE,
        "planDescription": PLAN_DESCRIPTION,
        "planBasis": PLAN_BASIS,
        "generatedFor": today.isoformat(),
        "raceDay": RACE_DAY.isoformat(),
        "taperStart": TAPER_START.isoformat(),
        "daysToRace": max(0, (RACE_DAY - today).days),
        "paceRefs": PACE_REFS,
        "raceStrategy": RACE_STRATEGY,
        "fuelStrategy": FUEL_STRATEGY,
        "weeks": weeks,
    }
