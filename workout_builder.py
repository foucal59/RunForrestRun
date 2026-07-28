"""Construit une seance structuree envoyable a Garmin Connect.

Garmin Connect n'importe aucun fichier de seance (ni TCX ni FIT) : l'import ne
gere que des activites. La seule voie fiable est l'API workout-service, appelee
via garminconnect (`api.upload_workout(payload)`), qui cree la seance
directement dans le compte de l'athlete (visible dans « Entrainements »,
planifiable, synchronisee sans fil sur la montre).

Ce module :
  1. parse le texte d'une seance (« 3 x 8' recup 2' trot », « 18 km dont les 6
     derniers a 5:30 », etc.) en une liste de steps ;
  2. rend le JSON attendu par le workout-service (dict pret pour upload_workout).

Reserve aux SL et seances qualite (seuil, VO2, allure marathon, tempo). Les
footings et l'endurance moyenne restent des efforts libres, non exportes.
"""

from __future__ import annotations

import re
import sys
from typing import Any


# Allures par defaut (sec/km) quand le texte n'en donne pas.
_DEFAULT_EASY_SEC = 390  # ~6:30/km
_DEFAULT_RECOVERY_SEC = 410  # ~6:50/km

# ── Identifiants Garmin (workout-service) ───────────────────────────────────
_SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
_STEP_TYPES = {
    "warmup": 1, "cooldown": 2, "interval": 3,
    "recovery": 4, "rest": 5, "repeat": 6,
}
_COND_DISTANCE = {"conditionTypeId": 1, "conditionTypeKey": "distance", "displayOrder": 1, "displayable": True}
_COND_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
_COND_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
_TARGET_PACE = {"workoutTargetTypeId": 5, "workoutTargetTypeKey": "pace.zone", "displayOrder": 5}
_TARGET_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}


def is_workout_eligible(category: str | None) -> bool:
    """Seules les SL et les seances qualite meritent une seance structuree."""
    return category in {"long", "quality"}


# ── Parsing des allures et durees ──────────────────────────────────────────

def _pace_to_seconds(text: str) -> int | None:
    """'4:20' -> 260 secondes/km."""
    match = re.match(r"^\s*(\d+):(\d{1,2})\s*$", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _parse_pace_range(text: str) -> tuple[int, int] | None:
    """1re allure (ou fourchette) 'M:SS[-M:SS]/km' -> (sec_lent, sec_rapide)."""
    match = re.search(r"(\d+:\d{2})(?:\s*-\s*(\d+:\d{2}))?\s*/?\s*km", text)
    if not match:
        return None
    a = _pace_to_seconds(match.group(1))
    if a is None:
        return None
    b = _pace_to_seconds(match.group(2)) if match.group(2) else a
    if b is None:
        b = a
    return max(a, b), min(a, b)


def _pace_seconds_to_speed(slow_sec: int, fast_sec: int) -> tuple[float, float]:
    """(sec/km lent, sec/km rapide) -> (m/s bas, m/s haut). One=bas, Two=haut."""
    return round(1000.0 / slow_sec, 3), round(1000.0 / fast_sec, 3)


# Jeton de duree : min'sec ("1'30"), secondes ("90''"), minutes ("2'").
# Apostrophes finales facultatives (0 a 2) : '{0,2} et non ''? qui en imposerait une.
_DUR_TOKEN = r"\d+'\d{1,2}'{0,2}|\d+''|\d+'"


def _parse_duration_seconds(text: str) -> int | None:
    text = text.strip()
    sec_only = re.match(r"^(\d+)\s*''\s*$", text)
    if sec_only:
        return int(sec_only.group(1))
    min_sec = re.match(r"^(\d+)\s*'\s*(\d{1,2})\s*'{0,2}\s*$", text)
    if min_sec:
        return int(min_sec.group(1)) * 60 + int(min_sec.group(2))
    min_only = re.match(r"^(\d+)\s*'\s*$", text)
    if min_only:
        return int(min_only.group(1)) * 60
    return None


def _first_minutes(text: str) -> int | None:
    match = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*'(?!')", text)
    if not match:
        return None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    return (low + high) // 2


def _parse_recovery_seconds(main: str, default: int = 120) -> int:
    match = re.search(r"recup(?:[ée]ration)?\s+(" + _DUR_TOKEN + r")", main)
    if match:
        secs = _parse_duration_seconds(match.group(1))
        if secs:
            return secs
    return default  # recup complete par defaut


# ── Modele de step interne ───────────────────────────────────────────────────

class _Step:
    def __init__(self, name: str, *, role: str, seconds: int | None = None,
                 meters: int | None = None, pace: tuple[int, int] | None = None) -> None:
        self.name = name
        self.role = role  # "warmup" | "interval" | "recovery" | "cooldown"
        self.seconds = seconds
        self.meters = meters
        self.pace = pace  # (slow_sec, fast_sec) ou None


class _Repeat:
    def __init__(self, reps: int, children: list[_Step]) -> None:
        self.reps = reps
        self.children = children


# ── Parsing d'une seance en steps ────────────────────────────────────────────

_REP_PATTERN = re.compile(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(km|m|')(?!')")


def _distance_step(name: str, km: float, pace: tuple[int, int] | None) -> _Step | None:
    meters = int(round(km * 1000))
    if meters <= 0:
        return None
    return _Step(name, role="interval", meters=meters, pace=pace)


def _split_repeated_long_blocks(
    total_km: float,
    reps: int,
    block_km: float,
    am_pace: tuple[int, int] | None,
    easy_pace: tuple[int, int],
) -> list[Any]:
    am_total = reps * block_km
    if reps <= 1 or am_total <= 0 or am_total >= total_km:
        step = _distance_step("Allure marathon", min(am_total, total_km), am_pace)
        return [step] if step else []

    easy_chunk = (total_km - am_total) / (reps + 1)
    steps: list[Any] = []
    for _ in range(reps):
        easy = _distance_step("Facile", easy_chunk, easy_pace)
        if easy:
            steps.append(easy)
        am = _distance_step("Allure marathon", block_km, am_pace)
        if am:
            steps.append(am)
    easy = _distance_step("Facile", easy_chunk, easy_pace)
    if easy:
        steps.append(easy)
    return steps


def _build_long_steps(main: str) -> list[Any]:
    steps: list[Any] = []
    total = re.search(r"(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+(?:[.,]\d+)?))?\s*km", main)
    if not total:
        minutes = _first_minutes(main)
        if minutes:
            pace = _parse_pace_range(main) or (_DEFAULT_EASY_SEC, _DEFAULT_EASY_SEC)
            steps.append(_Step("Sortie longue", role="interval", seconds=minutes * 60, pace=pace))
        return steps

    low = float(total.group(1).replace(",", "."))
    high = float(total.group(2).replace(",", ".")) if total.group(2) else low
    total_km = (low + high) / 2

    # Bloc allure marathon en fin de sortie. Formes : "dont les 6 derniers a
    # 5:25-5:35", "dont 10-12 km a ...", "dont 2 x 6 km a ...".
    am_pace = None
    am_km = None
    tail = main.split("dont", 1)[1] if "dont" in main else ""
    if tail and (_parse_pace_range(tail) or re.search(r"allure marathon|\bAM\b", tail)):
        am_pace = _parse_pace_range(tail) or (335, 330)  # ~5:35-5:30
        rep = re.search(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*km", tail)
        last = re.search(r"les?\s+(\d+)(?:\s*-\s*(\d+))?\s*derniers?", tail)
        simple = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*km", tail)
        if rep:
            am_km = int(rep.group(1)) * float(rep.group(2).replace(",", "."))
        elif last:
            a, b = int(last.group(1)), int(last.group(2) or last.group(1))
            am_km = (a + b) / 2
        elif simple:
            a, b = int(simple.group(1)), int(simple.group(2) or simple.group(1))
            am_km = (a + b) / 2

    easy_pace = (405, 375)  # 6:45-6:15
    if am_km and 0 < am_km < total_km:
        if rep:
            steps.extend(_split_repeated_long_blocks(
                total_km,
                int(rep.group(1)),
                float(rep.group(2).replace(",", ".")),
                am_pace,
                easy_pace,
            ))
        else:
            steps.append(_Step("Facile", role="interval", meters=int(round((total_km - am_km) * 1000)), pace=easy_pace))
            steps.append(_Step("Allure marathon", role="interval", meters=int(round(am_km * 1000)), pace=am_pace))
    else:
        pace = _parse_pace_range(main) or easy_pace
        steps.append(_Step("Sortie longue", role="interval", meters=int(round(total_km * 1000)), pace=pace))
    return steps


def _build_quality_steps(main: str) -> list[Any]:
    steps: list[Any] = []
    reps = list(_REP_PATTERN.finditer(main))
    if reps:
        full_recovery = _parse_recovery_seconds(main)
        full_pace = _parse_pace_range(main)
        for index, rep in enumerate(reps):
            next_start = reps[index + 1].start() if index + 1 < len(reps) else len(main)
            segment = main[rep.end():next_start]
            segment_recovery = _parse_recovery_seconds(segment, default=0) or full_recovery
            work_pace = _parse_pace_range(segment) or full_pace
            steps.append(_repeat_from_match(rep, work_pace, segment_recovery))
        return steps

    pace = _parse_pace_range(main)
    km = re.search(r"(\d+(?:[.,]\d+)?)\s*km", main)
    if km:
        steps.append(_Step("Bloc", role="interval",
                           meters=int(round(float(km.group(1).replace(",", ".")) * 1000)), pace=pace))
        return steps
    minutes = _first_minutes(main)
    if minutes:
        steps.append(_Step("Bloc", role="interval", seconds=minutes * 60, pace=pace))
    return steps


def _repeat_from_match(rep: re.Match[str], work_pace: tuple[int, int] | None, recovery_seconds: int) -> _Repeat:
    n = int(rep.group(1))
    value = float(rep.group(2).replace(",", "."))
    unit = rep.group(3)
    if unit == "'":
        work = _Step("Effort", role="interval", seconds=int(value * 60), pace=work_pace)
    elif unit == "km":
        work = _Step("Effort", role="interval", meters=int(round(value * 1000)), pace=work_pace)
    else:  # "m"
        work = _Step("Effort", role="interval", meters=int(round(value)), pace=work_pace)
    recovery = _Step("Recuperation", role="recovery", seconds=recovery_seconds)
    return _Repeat(n, [work, recovery])


def _build_steps(structure: dict[str, str], category: str) -> list[Any]:
    main_txt = structure.get("main", "") or ""
    if category == "long":
        # La distance totale englobe deja echauffement/retour au calme.
        return _build_long_steps(main_txt)

    steps: list[Any] = []
    wu_min = _first_minutes(structure.get("warmup", "") or "") or 12
    steps.append(_Step("Echauffement", role="warmup", seconds=wu_min * 60, pace=(_DEFAULT_EASY_SEC, 320)))
    steps.extend(_build_quality_steps(main_txt))
    cd_min = _first_minutes(structure.get("cooldown", "") or "") or 5
    steps.append(_Step("Retour au calme", role="cooldown", seconds=cd_min * 60,
                       pace=(_DEFAULT_RECOVERY_SEC, _DEFAULT_EASY_SEC)))
    return steps


# ── Rendu JSON workout-service ───────────────────────────────────────────────

def _step_dict(step: _Step, order: int) -> dict[str, Any]:
    st_id = _STEP_TYPES[step.role]
    out: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": st_id, "stepTypeKey": step.role, "displayOrder": st_id},
        "stepName": step.name,
    }
    if step.meters is not None:
        out["endCondition"] = dict(_COND_DISTANCE)
        out["endConditionValue"] = float(step.meters)
    else:
        out["endCondition"] = dict(_COND_TIME)
        out["endConditionValue"] = float(step.seconds or 0)
    if step.pace:
        low, high = _pace_seconds_to_speed(step.pace[0], step.pace[1])
        out["targetType"] = dict(_TARGET_PACE)
        out["targetValueOne"] = low   # m/s bas (allure lente)
        out["targetValueTwo"] = high  # m/s haut (allure rapide)
    else:
        out["targetType"] = dict(_TARGET_NONE)
    return out


def _render_steps(steps: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    order = 1
    for node in steps:
        if isinstance(node, _Repeat):
            group_order = order
            order += 1
            children = []
            for child in node.children:
                children.append(_step_dict(child, order))
                order += 1
            out.append({
                "type": "RepeatGroupDTO",
                "stepOrder": group_order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                "numberOfIterations": node.reps,
                "workoutSteps": children,
                "endCondition": dict(_COND_ITER),
                "endConditionValue": float(node.reps),
                "smartRepeat": False,
            })
        else:
            out.append(_step_dict(node, order))
            order += 1
    return out


def build_garmin_workout(structure: dict[str, str], *, title: str, category: str,
                         est_minutes: int | None = None) -> dict[str, Any]:
    """JSON pret pour `garminconnect.Garmin.upload_workout`."""
    steps = _build_steps(structure, category)
    if not steps:
        steps = [_Step("Seance", role="interval", seconds=45 * 60)]
        print(f"[workout_builder] steps non parses pour '{title}', fallback applique", file=sys.stderr)
    return {
        "workoutName": (title or "Seance").strip()[:80],
        "sportType": dict(_SPORT_RUNNING),
        "estimatedDurationInSecs": int((est_minutes or 45) * 60),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(_SPORT_RUNNING),
            "workoutSteps": _render_steps(steps),
        }],
        "author": {},
    }
