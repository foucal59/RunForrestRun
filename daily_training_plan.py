from __future__ import annotations

import contextvars
import math
import re
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from runner_profile import PACE_TARGETS, PROFILE, fmt_clock

# ── Allures d'entrainement ──────────────────────────────────────────────────
# Aucune allure n'est ecrite en dur : toutes se deduisent de l'objectif du
# coureur (`runner_profile.derive_paces`). Un plan dont les allures sont figees
# dans le code n'est le plan que d'une seule personne — et se perime des que
# celle-ci progresse.
EASY_PACE = PROFILE.pace("easy")
RECOVERY_PACE = PROFILE.pace("recovery")
STEADY_PACE = PROFILE.pace("steady")
THRESHOLD_PACE = PROFILE.pace("threshold")
GOAL_PACE = PROFILE.pace("marathon")
GOAL_PACE_TIGHT = PROFILE.pace_target("marathon")
VO2_PACE = PROFILE.pace("vo2")
STRIDES_PACE = PROFILE.pace("strides")
SEMI_PACE = PROFILE.pace("semi")
LONG_COMPLETION_RATIO = 0.85
# Une seance qualite n'est "couverte" que si le run atteint 70% du volume prevu :
# un 3 km rapide ne remplace pas un seuil de 4 x 6'.
QUALITY_COMPLETION_RATIO = 0.70
# Fenetre de reconnaissance d'un bloc a allure marathon dans un run reel, calee
# sur l'allure objectif du coureur. Trop lent = sortie longue facile, trop
# rapide = seuil : ni l'un ni l'autre ne valide le travail specifique demande.
MARATHON_PACE_MIN_SEC, MARATHON_PACE_MAX_SEC = PROFILE.marathon_pace_window
# Une sortie longue peut etre courue plusieurs jours avant la date prevue (week-end
# deplace, meteo, voyage). La fenetre reelle est bornee par la SL planifiee
# precedente ; ce plafond evite qu'un run du debut de semaine de plan fasse
# sauter la SL qui vient.
LONG_ADVANCE_MAX_DAYS = 4
# Un fractionne courru en montagne (ou par forte chaleur) a une allure moyenne
# lente et une FC moyenne basse : les moyennes de l'activite ne le distinguent
# pas d'un footing. Seules les laps gardent l'alternance effort/recup qui signe
# la seance. Ces seuils decrivent une VRAIE rep de travail, pas un km d'auto-lap.
INTERVAL_REP_MIN_SECONDS = 60
INTERVAL_REP_MAX_SECONDS = 15 * 60
INTERVAL_MIN_BLOCKS = 3
INTERVAL_MIN_WORK_SECONDS = 6 * 60
# Une rep est "dure" si elle est nettement plus rapide que la moyenne de la
# sortie, ou nettement plus haute en FC.
INTERVAL_REP_PACE_RATIO = 0.88
INTERVAL_REP_HR_MARGIN = 6
# Un dossard, ce n'est pas seulement la distance officielle : l'echauffement
# et le retour au calme font partie de la seance et de la semaine. Ces deux
# constantes cadrent a la fois le texte affiche et l'estimation de volume.
RACE_WARMUP_MINUTES = 20
RACE_COOLDOWN_MINUTES = 12
PLAN_SOURCE = "plan-genere"
PLAN_DESCRIPTION = PROFILE.description
PLAN_BASIS = "Adapte sur les 10 derniers entrainements charges"
RACE_NAME = PROFILE.race_name
RACE_DAY = PROFILE.race_date
PLAN_START = PROFILE.plan_start
PLAN_END = RACE_DAY
# Lundi de la S1. Les jours anterieurs sont la REPRISE, hors numerotation :
# compter la reprise comme "Semaine 1" decalerait tous les libelles de +1 par
# rapport a la periodisation annoncee (S1-Sn, la derniere etant la course).
PLAN_WEEK_ONE = PROFILE.week_one_monday
TAPER_START = PROFILE.taper_start


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


def _race_plan(title: str, main: str, *, tag: str) -> dict[str, Any]:
    """Course qui n'est pas l'objectif du plan (dossard prepa, 10 km, semi).

    Le kind reste "custom" : le kind "race" est cable en dur sur le marathon
    (42,2 km, 200 min, strategie de course, gels tous les 5 km) et rendrait
    n'importe quel autre dossard absurde. Seule la CATEGORIE est "race" : badge
    rouge sur la page Plan, seance cle, jamais exportee en seance structuree
    Garmin (on ne programme pas une course sur la montre), et jamais reecrite
    par l'adaptation automatique.
    """
    return _custom(
        title,
        category="race",
        warmup=f"{RACE_WARMUP_MINUTES}' footing progressif + 4 lignes de 20'' + gammes courtes",
        main=main,
        cooldown=f"{RACE_COOLDOWN_MINUTES}' tres facile",
        tag=tag,
    )


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


# ── Generation du calendrier ────────────────────────────────────────────────
#
# Le calendrier n'est plus une liste de dates ecrite a la main : c'est une trame
# de periodisation CALCULEE depuis le profil du coureur (date de course, nombre
# de semaines, jour de sortie longue, bornes de volume). La version precedente
# encodait un bloc marathon reel, date au jour, avec ses notes retrospectives :
# elle n'etait rejouable par personne d'autre, et perimee le lendemain de la
# course.
#
# Trame produite, identique dans son esprit a un plan marathon classique :
#   reprise (demi-semaine)  -> mise en route, aucune intensite
#   base (S1..Sb)           -> fonciere + premiers rappels de vitesse
#   specifique (Sb+1..Sn-t-1) -> seuil et blocs a allure marathon dans la SL
#   rodage (Sn-t)           -> semi test, le point d'appui de la cible chrono
#   affutage (t dernieres)  -> volume qui tombe, allure specifique qui reste
#
# Une semaine sur quatre est une DECHARGE : c'est elle qui permet a la moyenne
# de monter. La semaine qui precede le semi test en est toujours une.

# Part des semaines de construction consacree a la fonciere. 0.36 sur un bloc de
# 11 semaines donne 4 semaines de base et 7 de specifique, la repartition usuelle.
BASE_PHASE_SHARE = 0.36

# Une decharge ramene la sortie longue a ce ratio du dernier palier atteint,
# sans jamais descendre sous la SL de depart.
DELOAD_LONG_RATIO = 0.65

# Affutage : ce qu'il reste de la SL de pic, semaine par semaine en partant de
# la plus eloignee de la course. Le volume tombe, l'allure specifique reste.
TAPER_LONG_RATIOS = (0.70, 0.50, 0.35)
TAPER_AM_KM = (8, 5, 3)

# Volume des footings, en minutes, du debut a la fin de la rampe.
EASY_MINUTES_RANGE = (45, 60)
STEADY_MINUTES_RANGE = (60, 80)
RECOVERY_MINUTES_RANGE = (30, 40)

# Facteur de volume applique aux footings hors sortie longue. Une decharge ou une
# semaine d'affutage qui garderait le volume des semaines de charge n'en serait
# pas une : c'est le total hebdomadaire qui doit tomber, pas seulement la SL.
DELOAD_VOLUME_FACTOR = 0.80
TAPER_VOLUME_FACTORS = (0.75, 0.55, 0.45)

# Un bloc a allure marathon a besoin d'une mise en route : la dose ne depasse
# jamais la SL moins cette reserve.
LONG_AM_WARMUP_KM = 8


def _lerp(low: float, high: float, position: float) -> float:
    return low + (high - low) * position


def _phase_of_week(week_num: int, build_weeks: int, base_weeks: int, plan_weeks: int) -> str:
    """Phase d'une semaine numerotee (S1 = 1, la course est en S`plan_weeks`)."""
    if week_num >= plan_weeks:
        return "race_week"
    if week_num > build_weeks + 1:
        return "taper"
    if week_num == build_weeks + 1:
        return "peak"
    if week_num <= base_weeks:
        return "base"
    return "specific"


def _plan_shape() -> dict[str, Any]:
    """Decoupage en phases et rampes, deduit du seul cadrage du profil."""
    plan_weeks = PROFILE.plan_weeks
    taper_weeks = PROFILE.taper_weeks
    # Les semaines de construction : tout sauf l'affutage et la semaine de rodage.
    build_weeks = max(2, plan_weeks - taper_weeks - 1)
    base_weeks = max(1, min(build_weeks - 1, round(build_weeks * BASE_PHASE_SHARE)))

    # Decharge toutes les 4 semaines, et systematiquement la derniere semaine de
    # construction : le semi test du rodage se court sur des jambes fraiches.
    deloads = {
        week
        for week in range(1, build_weeks + 1)
        if week % 4 == 0 or week == build_weeks
    }
    ramp = [week for week in range(1, build_weeks + 1) if week not in deloads]

    long_km: dict[int, int] = {}
    long_am_km: dict[int, int] = {}
    easy_scale: dict[int, float] = {}
    last_ramp_long = PROFILE.long_start_km

    # Les deux premieres semaines de rampe restent sans allure marathon : on
    # installe d'abord le volume, l'allure specifique vient ensuite.
    am_ramp = ramp[2:]

    for index, week in enumerate(ramp):
        position = index / (len(ramp) - 1) if len(ramp) > 1 else 1.0
        long_km[week] = int(round(_lerp(PROFILE.long_start_km, PROFILE.long_peak_km, position)))
        easy_scale[week] = position
        last_ramp_long = long_km[week]
        if week in am_ramp:
            am_position = am_ramp.index(week) / (len(am_ramp) - 1) if len(am_ramp) > 1 else 1.0
            dose = int(round(_lerp(PROFILE.long_am_start_km, PROFILE.long_am_peak_km, am_position)))
            long_am_km[week] = max(0, min(dose, long_km[week] - LONG_AM_WARMUP_KM))
        else:
            long_am_km[week] = 0

    # Les decharges se calent sur le dernier palier atteint AVANT elles.
    reached = PROFILE.long_start_km
    for week in range(1, build_weeks + 1):
        if week in deloads:
            long_km[week] = max(PROFILE.long_start_km, int(round(reached * DELOAD_LONG_RATIO)))
            long_am_km[week] = 0
            easy_scale[week] = easy_scale.get(week - 1, 0.0) * 0.7
        else:
            reached = long_km[week]

    return {
        "planWeeks": plan_weeks,
        "taperWeeks": taper_weeks,
        "buildWeeks": build_weeks,
        "baseWeeks": base_weeks,
        "deloads": deloads,
        "longKm": long_km,
        "longAmKm": long_am_km,
        "easyScale": easy_scale,
        "peakLongKm": PROFILE.long_peak_km,
    }


PLAN_SHAPE = _plan_shape()


def _scaled(bounds: tuple[int, int], position: float, factor: float = 1.0) -> int:
    """Volume d'un footing a ce stade de la rampe, arrondi a 5 minutes."""
    minutes = _lerp(bounds[0], bounds[1], max(0.0, min(1.0, position))) * factor
    return max(20, int(round(minutes / 5.0) * 5))


def _volume_factor(week_num: int, phase: str, shape: dict[str, Any]) -> float:
    """Part du volume de reference que garde cette semaine."""
    if phase == "taper":
        rank = week_num - (shape["buildWeeks"] + 2)
        return TAPER_VOLUME_FACTORS[min(max(rank, 0), len(TAPER_VOLUME_FACTORS) - 1)]
    if phase == "peak":
        return DELOAD_VOLUME_FACTOR
    if week_num in shape["deloads"]:
        return DELOAD_VOLUME_FACTOR
    return 1.0


def _quality_for_week(week_num: int, phase: str, shape: dict[str, Any]) -> dict[str, Any]:
    """Seance dure de la semaine. Le type suit la phase, jamais le hasard."""
    if phase == "race_week":
        return _quality_plan(
            "Rappel allure marathon",
            f"30' facile dont 3 x 1 km a {GOAL_PACE_TIGHT}, recup 2' facile",
            tag="marathon-pace",
        )
    if phase == "taper":
        return _quality_plan(
            "Allure marathon controlee",
            f"3 x 2 km a {GOAL_PACE_TIGHT}, recup 1' trot",
            tag="marathon-pace",
        )
    if phase == "peak":
        # Le semi test est la seance dure de la semaine : le mardi reste leger.
        return _quality_plan(
            "Seuil leger",
            f"3 x 6' a {THRESHOLD_PACE}, recup 2' trot (veille de test allegee)",
            tag="threshold",
        )
    if week_num in shape["deloads"]:
        return _quality_plan(
            "Rappel allure marathon leger",
            f"3 x 2 km a {GOAL_PACE_TIGHT}, recup 2' trot, sans accelerer",
            tag="marathon-pace",
        )
    if phase == "base":
        # Alternance vitesse / seuil : la base installe la cylindree avant que le
        # specifique ne monopolise les seances dures.
        if week_num % 2 == 1:
            reps = 5 + week_num // 2
            return _quality_plan(
                f"{reps} x 400 m VO2",
                f"{reps} x 400 m a {VO2_PACE}, recup 1'30 trot",
                tag="vo2",
            )
        return _quality_plan(
            "Seuil 3 x 8'",
            f"3 x 8' a {THRESHOLD_PACE}, recup 2' trot",
            tag="threshold",
        )

    # Specifique : le seuil porte le bloc, avec un rappel de vitesse et un bloc
    # a allure marathon inseres regulierement pour ne perdre ni l'un ni l'autre.
    slot = (week_num - shape["baseWeeks"] - 1) % 4
    if slot == 1:
        return _quality_plan(
            "Bloc allure marathon",
            f"5 x 2 km a {GOAL_PACE_TIGHT}, recup 1' trot",
            tag="marathon-pace",
        )
    if slot == 3:
        return _quality_plan(
            "Rappel vitesse",
            f"6 x 400 m a {VO2_PACE} + 4 x 200 m relaches, recup complete",
            tag="vo2",
        )
    minutes = 6 if slot == 0 else 10
    reps = 5 if slot == 0 else 3
    return _quality_plan(
        f"Seuil {reps} x {minutes}'",
        f"{reps} x {minutes}' a {THRESHOLD_PACE}, recup 2' trot",
        tag="threshold",
    )


def _long_for_week(week_num: int, phase: str, shape: dict[str, Any]) -> dict[str, Any]:
    """Sortie longue de la semaine, avec sa dose eventuelle d'allure marathon."""
    if phase == "peak":
        semi_target = fmt_clock(PROFILE.projected("semi"))
        return _long_plan(
            "Semi-marathon test",
            f"Semi test : viser {semi_target} ({SEMI_PACE}), ou 21 km dont 15 km a {GOAL_PACE}. "
            "C'est ce chrono qui arrete la cible du jour J.",
            tag="race-test",
        )
    if phase == "taper":
        # Rang de la semaine d'affutage, la plus eloignee de la course d'abord.
        rank = week_num - (shape["buildWeeks"] + 2)
        ratio = TAPER_LONG_RATIOS[min(rank, len(TAPER_LONG_RATIOS) - 1)]
        target = max(10, int(round(shape["peakLongKm"] * ratio)))
        am_km = min(TAPER_AM_KM[min(rank, len(TAPER_AM_KM) - 1)], target - 6)
        if am_km <= 0:
            return _long_plan(f"Sortie longue allegee {target} km", f"{target} km facile a {EASY_PACE}")
        return _long_plan(
            f"SL {target} km dont {am_km} km AM",
            f"{target} km dont {am_km} km a {GOAL_PACE}",
        )

    target = shape["longKm"][week_num]
    am_km = shape["longAmKm"][week_num]
    if not am_km:
        label = "SL de decharge" if week_num in shape["deloads"] else "Sortie longue facile"
        return _long_plan(f"{label} {target} km", f"{target} km facile a {EASY_PACE}, sans bloc rapide")

    # Au-dela d'une certaine dose, le bloc continu est coupe en deux : courir
    # l'allure jambes videes est l'objectif, pas d'empiler des kilometres.
    if am_km >= 14:
        half = am_km // 2
        main = (
            f"{target} km dont 2 x {half} km a {GOAL_PACE}, 1 km de trot entre les blocs, "
            "ravitaillement comme le jour J"
        )
    else:
        main = f"{target} km dont les {am_km} derniers a {GOAL_PACE}"
    return _long_plan(f"SL {target} km avec AM", main)


def _week_sessions(week_num: int, phase: str, shape: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Les 7 jours d'une semaine, indexes par jour de la semaine (0 = lundi).

    Le gabarit se DEDUIT des trois jours choisis dans le profil (repos, qualite,
    sortie longue) : la veille de la SL s'allege, le lendemain recupere, et les
    jours restants portent le volume. Changer `longRunWeekday` dans le profil
    deplace tout le reste sans toucher au code.
    """
    long_wd = PROFILE.long_run_weekday
    quality_wd = PROFILE.quality_weekday
    rest_wd = PROFILE.rest_weekday
    position = shape["easyScale"].get(week_num, 1.0)
    factor = _volume_factor(week_num, phase, shape)
    # Une semaine allegee ne se contente pas de raccourcir : elle rend des jours.
    light = factor < 1.0

    eve_wd = (long_wd - 1) % 7
    # Le lendemain de la sortie longue, modulo la semaine. Quand la SL tombe le
    # dernier jour (dimanche), ce lendemain revient au LUNDI de la meme semaine :
    # c'est voulu, puisque ce lundi suit bien une sortie longue — celle de la
    # semaine precedente. Le gabarit se repete a l'identique chaque semaine.
    after_wd = (long_wd + 1) % 7

    sessions: dict[int, dict[str, Any]] = {}
    sessions[long_wd] = _long_for_week(week_num, phase, shape)
    if quality_wd not in sessions:
        sessions[quality_wd] = _quality_for_week(week_num, phase, shape)

    # Semaine de forte charge : le jour de repos devient un footing de volume et
    # le lendemain de SL un vrai repos. La semaine reste a six sorties, sans
    # jamais enchainer trois semaines sans jour off.
    high_volume = (
        phase == "specific"
        and week_num not in shape["deloads"]
        and shape["longKm"].get(week_num, 0) >= PROFILE.long_peak_km - 6
    )

    if rest_wd not in sessions:
        if high_volume:
            sessions[rest_wd] = _easy_plan(
                "Footing de volume",
                f"{_scaled(RECOVERY_MINUTES_RANGE, position)}' tres facile a {RECOVERY_PACE}",
                tag="recovery",
            )
        else:
            sessions[rest_wd] = _rest()

    if after_wd not in sessions:
        if high_volume:
            sessions[after_wd] = _rest()
        else:
            sessions[after_wd] = _easy_plan(
                "Footing de recuperation",
                f"{_scaled(RECOVERY_MINUTES_RANGE, position, factor)}' tres facile "
                f"a {RECOVERY_PACE}, ou repos",
                tag="recovery",
            )

    if eve_wd not in sessions:
        # La veille de la sortie longue est le premier jour qu'une semaine legere
        # rend : c'est celui dont l'absence ne coute aucune adaptation.
        sessions[eve_wd] = _rest() if light else _easy_plan(
            "Footing court",
            f"{_scaled((30, 40), position, factor)}' a {EASY_PACE} "
            "(allegement avant la sortie longue)",
        )

    # Les jours restants portent le volume : un footing avec lignes, puis de
    # l'endurance moyenne.
    fillers = [
        _easy_plan(
            "Footing facile + lignes",
            f"{_scaled(EASY_MINUTES_RANGE, position, factor)}' a {EASY_PACE} "
            "+ 5 lignes de 20'' relachees",
        ),
        # L'endurance moyenne est de l'intensite douce : une semaine legere la
        # remplace par du footing plutot que de la raccourcir.
        _easy_plan(
            "Footing facile",
            f"{_scaled(EASY_MINUTES_RANGE, position, factor)}' a {EASY_PACE}",
        )
        if light
        else _easy_plan(
            "Endurance moyenne",
            f"{_scaled(STEADY_MINUTES_RANGE, position, factor)}' a {STEADY_PACE}",
            tag="steady",
        ),
    ]
    for weekday in range(7):
        if weekday in sessions:
            continue
        sessions[weekday] = fillers.pop(0) if fillers else _easy_plan(
            "Footing facile",
            f"{_scaled(EASY_MINUTES_RANGE, position, factor)}' a {EASY_PACE}",
        )
    return sessions


def _race_week_sessions(shape: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Semaine de course : rien ne s'y gagne, tout peut s'y perdre.

    Quatre sorties courtes au maximum avant le depart. Le rappel d'allure
    marathon reste, parce qu'il rassure les jambes sans les fatiguer ; le volume,
    lui, ne sert plus a rien a ce stade.
    """
    race_wd = PROFILE.race_date.weekday()
    quality_wd = PROFILE.quality_weekday
    sessions: dict[int, dict[str, Any]] = {race_wd: _race(PROFILE.race_name)}

    # Le rappel d'allure ne tient que s'il reste au moins quatre jours de
    # recuperation avant le depart.
    if quality_wd < race_wd - 3:
        sessions[quality_wd] = _quality_for_week(shape["planWeeks"], "race_week", shape)

    # Les roles se lisent en jours AVANT la course, pas en jours de la semaine :
    # c'est la seule facon d'obtenir le meme affutage quel que soit le jour ou
    # tombe le depart.
    for weekday in range(7):
        if weekday in sessions:
            continue
        days_before = race_wd - weekday
        if days_before < 0:
            # Apres la course : repos, sans discussion.
            sessions[weekday] = _rest()
        elif days_before == 1:
            sessions[weekday] = _easy_plan(
                "Footing tres facile + lignes",
                f"25' tres facile a {RECOVERY_PACE} + 3 lignes de 20'' (veille de course)",
            )
        elif days_before in (3, 4):
            sessions[weekday] = _easy_plan(
                "Footing facile + lignes",
                f"30' facile a {EASY_PACE} + 4 lignes de 20''",
            )
        else:
            # J-2 est un repos sec, et le debut de semaine n'a plus rien a
            # apporter : c'est la que la fraicheur se fabrique.
            sessions[weekday] = _rest()
    return sessions


def _build_calendar() -> dict[str, dict[str, Any]]:
    """Le plan complet, jour par jour, genere depuis le profil du coureur.

    Source unique de verite du site ET du coach matinal
    (`scripts/seance_du_jour.py` lit ce module, il ne recopie aucun calendrier).
    Les ajustements decides au jour le jour n'ont pas leur place ici : ils
    passent par la table `plan_overrides` (voir `set_plan_overrides`).
    """
    shape = PLAN_SHAPE
    calendar: dict[str, dict[str, Any]] = {}

    # Reprise : la demi-semaine avant la S1. Mise en route, aucune intensite,
    # une sortie longue courte pour poser l'habitude du jour choisi.
    week_one = PROFILE.week_one_monday
    reprise_days = []
    day = PROFILE.plan_start
    while day < week_one:
        reprise_days.append(day)
        day += timedelta(days=1)

    # UNE seule sortie longue dans la reprise : celle du jour choisi si la
    # periode le contient, sinon le dernier jour. Tester les deux conditions
    # jour par jour en posait deux quand la reprise se terminait la veille de
    # la S1 sur le jour de sortie longue.
    reprise_long_day = next(
        (day for day in reprise_days if day.weekday() == PROFILE.long_run_weekday),
        reprise_days[-1] if reprise_days else None,
    )
    for day in reprise_days:
        if day == reprise_long_day:
            session = _long_plan(
                "Sortie longue facile",
                f"60-70' facile a {EASY_PACE}, sans bloc rapide",
            )
        elif day.weekday() == PROFILE.rest_weekday:
            session = _rest()
        else:
            session = _easy_plan("Footing facile", f"45' a {EASY_PACE}")
        calendar[day.isoformat()] = session

    for week_num in range(1, shape["planWeeks"] + 1):
        monday = week_one + timedelta(weeks=week_num - 1)
        phase = _phase_of_week(week_num, shape["buildWeeks"], shape["baseWeeks"], shape["planWeeks"])
        sessions = (
            _race_week_sessions(shape)
            if phase == "race_week"
            else _week_sessions(week_num, phase, shape)
        )
        for weekday, session in sessions.items():
            calendar[(monday + timedelta(days=weekday)).isoformat()] = deepcopy(session)
    return calendar


PLAN_CALENDAR = _build_calendar()


# ── Ajustements du coach (table plan_overrides) ──
#
# Le calendrier ci-dessus est fige dans le code : sans ce mecanisme, un
# ajustement decide par le coach (SKILL.md, tache du matin) n'atteint jamais le
# site. Les overrides sont charges depuis la base par l'appelant HTTP et poses
# ici pour la duree de la requete. ContextVar plutot qu'un global : chaque
# requete asyncio garde ses propres valeurs.
_PLAN_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]]] = contextvars.ContextVar(
    "plan_overrides",
    default={},
)

# Champs libres autorises dans un ajustement du coach.
_OVERRIDE_TEXT_FIELDS = ("title", "warmup", "main", "cooldown")
_OVERRIDE_CATEGORIES = {"easy", "quality", "long", "rest", "race"}


def set_plan_overrides(overrides: dict[str, Any] | None) -> None:
    """Pose les ajustements du coach pour la duree du traitement en cours.

    `overrides` est indexe par jour ISO ; chaque valeur est soit une seance deja
    normalisee, soit le payload brut stocke en base (normalise a la volee).
    """
    normalized: dict[str, dict[str, Any]] = {}
    for day_iso, payload in (overrides or {}).items():
        session = normalize_plan_override(payload)
        if session is None:
            continue
        normalized[str(day_iso)[:10]] = session
    _PLAN_OVERRIDES.set(normalized)


def active_plan_overrides() -> dict[str, dict[str, Any]]:
    """Ajustements actuellement poses (lecture seule)."""
    return _PLAN_OVERRIDES.get()


def normalize_plan_override(payload: Any) -> dict[str, Any] | None:
    """Transforme un ajustement du coach en seance exploitable par le plan.

    Accepte le format minimal que le coach peut ecrire ({"kind": "rest"} ou
    titre + contenu) et renvoie None si le payload est inutilisable, pour qu'un
    ajustement mal forme n'efface jamais la seance planifiee.
    """
    if not isinstance(payload, dict):
        return None
    session = payload.get("session") if isinstance(payload.get("session"), dict) else payload

    if session.get("kind") == "rest" or session.get("category") == "rest":
        out = _rest()
        out["overrideNote"] = payload.get("note") or session.get("note")
        return out

    title = str(session.get("title") or "").strip()
    main = str(session.get("main") or "").strip()
    if not title or not main:
        return None

    category = str(session.get("category") or "easy").strip()
    if category not in _OVERRIDE_CATEGORIES:
        category = "easy"
    builder = {
        "quality": _quality_plan,
        "long": _long_plan,
    }.get(category)
    if builder is not None:
        out = builder(title, main)
    else:
        out = _easy_plan(title, main)
    out["category"] = category
    if session.get("tag"):
        out["tag"] = str(session["tag"])
    for field in _OVERRIDE_TEXT_FIELDS:
        value = session.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
    out["overrideNote"] = payload.get("note") or session.get("note")
    return out


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


def _advance_label(days: int) -> str:
    if days <= 1:
        return "avec 1 jour d'avance"
    return f"avec {days} jours d'avance"


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
            # Rarement fournies par l'UI ; le serveur les rattache depuis la DB.
            "laps": item.get("laps") if isinstance(item.get("laps"), list) else [],
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
            "main": (
                f"Marathon : premiers kilometres volontairement freines, puis installer "
                f"{GOAL_PACE_TIGHT} (cible {PROFILE.goal_label}). Ne rien tenter de plus vite "
                "avant le 30e kilometre."
            ),
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


def _lap_pace(lap: dict[str, Any]) -> float | None:
    distance_m = lap.get("distance_m") or 0
    moving_time = lap.get("moving_time") or 0
    if distance_m <= 0 or moving_time <= 0:
        return None
    return moving_time / (distance_m / 1000.0)


_INTERVAL_STRUCTURE_CACHE: dict[tuple[Any, int, int], tuple[int, float]] = {}


def work_lap_blocks(run: dict[str, Any]) -> list[list[int]]:
    """Blocs de travail deduits des laps, en index dans `run["laps"]`.

    Un bloc = une ou plusieurs laps dures consecutives, separees des suivantes
    par une lap de recuperation. `_interval_structure` utilise cette decoupe pour
    savoir si une seance cle a deja ete couverte.
    """
    laps = run.get("laps") or []
    run_pace = run.get("pace_sec_per_km") or 0
    run_hr = run.get("average_heartrate") or 0
    hard: list[int] = []
    for index, lap in enumerate(laps):
        moving_time = lap.get("moving_time") or 0
        if not INTERVAL_REP_MIN_SECONDS <= moving_time <= INTERVAL_REP_MAX_SECONDS:
            continue
        pace = _lap_pace(lap)
        lap_hr = lap.get("average_heartrate") or 0
        faster = pace is not None and run_pace > 0 and pace <= run_pace * INTERVAL_REP_PACE_RATIO
        hotter = lap_hr > 0 and run_hr > 0 and lap_hr >= run_hr + INTERVAL_REP_HR_MARGIN
        if faster or hotter:
            hard.append(index)

    blocks: list[list[int]] = []
    for index in hard:
        if blocks and index == blocks[-1][-1] + 1:
            blocks[-1].append(index)
        else:
            blocks.append([index])
    return blocks


def _interval_structure(run: dict[str, Any]) -> tuple[int, float]:
    """(nombre de blocs de travail, secondes de travail) deduits des laps.

    Retourne (0, 0.0) quand la sortie n'a pas de structure d'intervalles.
    Exiger plusieurs blocs SEPARES ecarte le footing auto-lape ou la derive
    cardiaque ferait passer les derniers kilometres pour des reps : ceux-la
    forment un seul bloc contigu en fin de sortie.
    """
    laps = run.get("laps") or []
    if len(laps) < INTERVAL_MIN_BLOCKS:
        return 0, 0.0

    cache_key = (run.get("id"), len(laps), int(run.get("moving_time") or 0))
    if run.get("id") is not None and cache_key in _INTERVAL_STRUCTURE_CACHE:
        return _INTERVAL_STRUCTURE_CACHE[cache_key]

    blocks = work_lap_blocks(run)
    work_seconds = float(sum(
        laps[index].get("moving_time") or 0
        for block in blocks
        for index in block
    ))

    if len(blocks) < INTERVAL_MIN_BLOCKS or work_seconds < INTERVAL_MIN_WORK_SECONDS:
        result = (0, 0.0)
    else:
        result = (len(blocks), work_seconds)

    if run.get("id") is not None:
        _INTERVAL_STRUCTURE_CACHE[cache_key] = result
        print(
            f"[plan-intervals] run {run.get('id')} ({run.get('date')}): "
            f"{len(laps)} laps, {len(blocks)} bloc(s) durs, "
            f"{int(work_seconds)}s de travail -> structure="
            f"{'oui' if result[0] else 'non'}",
            file=sys.stderr,
        )
    return result


def _looks_quality(run: dict[str, Any]) -> bool:
    # Structure d'intervalles reelle : elle prime sur les moyennes, qui ne
    # voient pas un fractionne courru en cote ou avec de longues recups.
    if _interval_structure(run)[0]:
        return True
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


def _complete_today(
    run: dict[str, Any],
    session: dict[str, Any],
    *,
    force_matching: bool = False,
) -> dict[str, Any]:
    effort_kind, done_title = _classify_effort(run)
    details = [f"{run['distance_km']:.1f} km en {_fmt_duration(run.get('moving_time'))}"]
    if run.get("pace_sec_per_km"):
        details.append(f"{_fmt_pace(run['pace_sec_per_km'])}/km")
    if run.get("average_heartrate"):
        details.append(f"{int(round(run['average_heartrate']))} bpm")
    summary = ", ".join(details)
    # `matched_session_for_run` peut rattacher une qualite a la seance du
    # lendemain grace aux laps, meme quand l'allure moyenne (echauffement et
    # recuperations inclus) ne ressemble pas a l'allure cible. Dans ce cas le
    # rattachement est deja la decision : ne pas requalifier ensuite la sortie
    # comme "plus exigeante" sur sa seule moyenne.
    direction = "matching" if force_matching else _effort_vs_plan(run, session)
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
        # La page Plan additionne les seances faites et celles qui restent. Sans
        # ces valeurs, toute sortie terminee disparaissait du total hebdomadaire.
        "actualDistanceKm": run.get("distance_km"),
        "actualMinutes": (
            int(round((run.get("moving_time") or 0) / 60))
            if run.get("moving_time")
            else None
        ),
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
    single = re.search(r"(\d+(?:[.,]\d+)?)\s*km[^.]*?4:3[57]", main)
    if single:
        return float(single.group(1).replace(",", "."))
    return None


def _planned_marathon_pace_was_completed(session: dict[str, Any], run: dict[str, Any]) -> bool:
    """Un bloc AM n'est couvert que par un effort reellement a allure marathon.

    Les seuils absolus precedents (>= 8 km, <= 5:10/km) se trompaient dans les
    deux sens : une sortie longue lente de 20 km validait "AM 5 x 2 km", tandis
    qu'un rappel de taper "3 x 1 km" courru pile n'etait jamais reconnu.
    """
    pace = run.get("pace_sec_per_km") or 0
    if not MARATHON_PACE_MIN_SEC <= pace <= MARATHON_PACE_MAX_SEC:
        return False
    distance_km = run.get("distance_km") or 0
    planned_km = _planned_am_km(session)
    if planned_km:
        return distance_km >= planned_km * LONG_COMPLETION_RATIO
    return distance_km >= 8


def _planned_work_seconds(session: dict[str, Any]) -> float:
    """Volume de travail effectif prevu ("5 x 3'" -> 900 s), 0 si non exprime."""
    total = 0.0
    for reps, minutes in re.findall(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*'", session.get("main") or ""):
        total += int(reps) * float(minutes.replace(",", ".")) * 60
    return total


def _planned_quality_was_completed(session: dict[str, Any], run: dict[str, Any]) -> bool:
    """Qualite couverte seulement si le volume approche celui prevu."""
    if not _is_true_quality_run(run):
        return False
    _, planned_minutes = _estimate_effort(session)
    moving_time = run.get("moving_time") or 0
    if not moving_time:
        distance_km = run.get("distance_km") or 0
        pace = run.get("pace_sec_per_km") or 0
        if distance_km and pace:
            moving_time = distance_km * pace
    if planned_minutes:
        if moving_time and moving_time >= planned_minutes * 60 * QUALITY_COMPLETION_RATIO:
            return True
        # Echauffement ecourte ou recups marchees : le temps total peut rester
        # court alors que tout le travail demande a ete fait. Les laps le disent.
        planned_work = _planned_work_seconds(session)
        work_seconds = _interval_structure(run)[1]
        return bool(planned_work) and work_seconds >= planned_work * QUALITY_COMPLETION_RATIO

    # Quelques seances historiques sont exprimees en distance continue
    # ("Tempo 9 km") et n'ont donc pas d'estimation temporelle structuree.
    planned_km = _parse_first_km(_session_text(session))
    if planned_km:
        return (run.get("distance_km") or 0) >= planned_km * QUALITY_COMPLETION_RATIO

    # Sans volume planifie ni volume reel comparables, ne jamais conclure par
    # defaut que la seance est couverte.
    return False


def _recent_quality_covering_session(
    session: dict[str, Any],
    runs: list[tuple[int, dict[str, Any]]],
    *,
    max_age: int,
) -> tuple[int, dict[str, Any]] | None:
    """Derniere vraie qualite dont la charge est comparable a la seance prevue."""
    return next(
        (
            (age, run)
            for age, run in runs
            if age <= max_age and _planned_quality_was_completed(session, run)
        ),
        None,
    )


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
    reference_session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    *,
    max_age: int = 3,
) -> bool:
    for run in recent_runs:
        age = _run_age(reference_day, run)
        if age is None or not 1 <= age <= max_age:
            continue
        if reference_session.get("category") == "quality":
            if _planned_quality_was_completed(reference_session, run):
                return True
        elif _is_true_quality_run(run):
            return True
    return False


def _key_miss_block_reason(
    missed_day: date,
    missed_session: dict[str, Any],
    missed_run: dict[str, Any] | None,
    recent_runs: list[dict[str, Any]],
) -> str | None:
    # Une charge alternative le jour meme compte deja dans les jambes. Une SL
    # normale 2-3 jours avant ne doit pas, elle, bloquer un seuil a recaler.
    if missed_run is not None and _is_hard_or_long_run(missed_run):
        return "alternate_load"
    if _has_recent_true_quality_before(missed_day, missed_session, recent_runs):
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
    """Phase de periodisation d'un jour, deduite de sa position dans le bloc."""
    if day >= RACE_DAY:
        return "race"
    if day < PLAN_WEEK_ONE:
        return "reprise"
    week_num = (day - PLAN_WEEK_ONE).days // 7 + 1
    if week_num > PLAN_SHAPE["planWeeks"]:
        return "race"
    return _phase_of_week(
        week_num,
        PLAN_SHAPE["buildWeeks"],
        PLAN_SHAPE["baseWeeks"],
        PLAN_SHAPE["planWeeks"],
    )


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
        return _quality_plan(
            "Rappel allure marathon",
            f"30' facile dont 3 x 1 km a {GOAL_PACE_TIGHT}, recup 2' facile",
            tag="marathon-pace",
        )
    if phase == "taper":
        return _quality_plan(
            "Allure marathon controlee",
            f"3 x 2 km a {GOAL_PACE_TIGHT}, recup 1' trot",
            tag="marathon-pace",
        )
    if phase in {"specific", "peak"}:
        if week_index % 2 == 0:
            return _quality_plan(
                "Bloc allure marathon",
                f"2 x 5 km a {GOAL_PACE_TIGHT}, recup 2' trot",
                tag="marathon-pace",
            )
        return _quality_plan(
            "Seuil controle", f"3 x 10' a {THRESHOLD_PACE}, recup 3' trot", tag="threshold"
        )
    if week_index % 2 == 0:
        return _quality_plan(
            "Seuil progressif", f"3 x 8' a {THRESHOLD_PACE}, recup 2' trot", tag="threshold"
        )
    return _quality_plan(
        "Rappel vitesse", f"6 x 400 m a {VO2_PACE}, recup 1'30 trot", tag="vo2"
    )


def _adaptive_long(ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    longest = ctx["longest_recent_km"] or 0
    start_km = PROFILE.long_start_km
    peak_km = PROFILE.long_peak_km
    if phase == "reprise":
        return _long_plan(
            "Sortie longue facile", f"60-70' facile a {EASY_PACE}, sans bloc rapide"
        )
    if phase == "base":
        target = min(start_km + 4, max(start_km, int(round(longest + 2)) if longest else start_km))
        block = max(4, min(PROFILE.long_am_start_km + 2, target - 12))
        return _long_plan(
            f"SL {target} km avec AM", f"{target} km dont les {block} derniers a {GOAL_PACE}"
        )
    if phase == "specific":
        floor_km = max(start_km, peak_km - 8)
        target = min(peak_km, max(floor_km, int(round(longest + 2)) if longest else floor_km))
        if ctx["km_7"] >= 62:
            target = max(start_km, target - 4)
        am_block = max(6, min(PROFILE.long_am_peak_km, target - LONG_AM_WARMUP_KM))
        return _long_plan(
            f"SL {target} km avec AM", f"{target} km dont {am_block} km a {GOAL_PACE}"
        )
    if phase == "peak":
        if ctx["last_long_age"] is None or ctx["last_long_age"] >= 7:
            blocks = max(2, PROFILE.long_am_peak_km // 6)
            return _long_plan(
                "SL pic marathon",
                f"{peak_km - 2}-{peak_km + 2} km avec {blocks} blocs de 5-6 km a {GOAL_PACE}",
            )
        return _long_plan(
            "Semi test ou 21 km AM",
            f"21 km dont 12 km a {GOAL_PACE}, ou semi test controle a {SEMI_PACE}",
        )
    if phase == "taper":
        return _long_plan(
            "Derniere SL allegee",
            f"{max(12, round(peak_km * 0.55))}-{round(peak_km * 0.7)} km facile, "
            f"avec 5 km maximum a {GOAL_PACE} si les jambes sont fraiches",
        )
    return _adaptive_easy(35)


def _adaptive_schedule_for(day: date, recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    if day == RACE_DAY:
        return _race(PROFILE.race_name)
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
        return _long_plan("Sortie longue reportee", "75-95' facile a 5:20-5:50/km, sans bloc rapide")

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
    override = _PLAN_OVERRIDES.get().get(day.isoformat())
    if override is not None:
        return deepcopy(override)
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
        # Dimanche de la semaine du run : borne de validite du decalage.
        "week_end": run_day + timedelta(days=6 - run_day.weekday()),
    }


def _next_long_day(start: date, recent_runs: list[dict[str, Any]]) -> date | None:
    for offset in range(0, 21):
        day = start + timedelta(days=offset)
        if _schedule_for(day, recent_runs).get("category") == "long":
            return day
    return None


def _previous_long_day(day: date, recent_runs: list[dict[str, Any]]) -> date | None:
    """Jour de la sortie longue planifiee juste avant `day`."""
    for offset in range(1, 15):
        candidate = day - timedelta(days=offset)
        if _schedule_for(candidate, recent_runs).get("category") == "long":
            return candidate
    return None


def _covers_long_in_advance(
    long_day: date,
    long_session: dict[str, Any],
    run_day: date,
    run: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> bool:
    """Ce run couvre-t-il la SL planifiee le `long_day`, courue en avance ?

    La fenetre n'est pas un simple nombre de jours : elle s'arrete a la SL
    planifiee precedente, sinon la sortie longue de la semaine d'avant
    annulerait celle qui vient.
    """
    if long_session.get("category") != "long":
        return False
    advance_days = (long_day - run_day).days
    if not 1 <= advance_days <= LONG_ADVANCE_MAX_DAYS:
        return False
    previous_long = _previous_long_day(long_day, recent_runs)
    if previous_long is not None and run_day <= previous_long:
        return False
    return _planned_long_was_completed(long_session, run)


def _long_run_done_in_advance(
    day: date,
    session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]] | None:
    """Vraie SL deja courue en avance pour la sortie longue planifiee le `day`."""
    if session.get("category") != "long":
        return None
    for age, run in _recent_context(recent_runs, day)["runs"]:
        if _covers_long_in_advance(day, session, day - timedelta(days=age), run, recent_runs):
            return age, run
    return None


def _key_absorbed_in_advance(
    day: date,
    session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> bool:
    """Cette seance cle a-t-elle deja ete absorbee par un run en avance ?

    Une SL courue le vendredi pour le samedi n'est pas une seance MANQUEE : le
    samedi est deja passe en recuperation via `_long_run_done_in_advance`. Sans
    ce test, le lendemain voyait "seance cle manquee hier" et decalait une SL
    allegee de 16 km 48 h apres une SL de 25 km. Le cas est tombe deux fois :
    corrige a la main par un override coach le 16 aout 2026 (SL courue ven 14),
    puis reapparu a l'identique le 23 (SL courue ven 21).

    Le detour par la qualite n'est pas necessaire ici : une qualite courue en
    avance est deja neutralisee par `_key_miss_block_reason` ("recent_quality").
    """
    if session.get("category") != "long":
        return False
    return _long_run_done_in_advance(day, session, recent_runs) is not None


def _advanced_long_target(
    run_day: date,
    run: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> tuple[date, dict[str, Any]] | None:
    """Prochaine sortie longue planifiee que ce run vient d'absorber en avance."""
    for offset in range(1, LONG_ADVANCE_MAX_DAYS + 1):
        target_day = run_day + timedelta(days=offset)
        target_session = _schedule_for(target_day, recent_runs)
        if target_session.get("category") != "long":
            continue
        if _covers_long_in_advance(target_day, target_session, run_day, run, recent_runs):
            return target_day, target_session
        # Ne pas regarder au-dela de la premiere SL rencontree : si ce run ne la
        # couvre pas, il ne couvre pas non plus celle de la semaine suivante.
        return None
    return None


def matched_session_for_run(
    run: dict[str, Any],
    recent_runs: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, date, int]:
    """Seance couverte par un run : (seance, jour prevu, jours d'avance).

    Une sortie longue courue le vendredi pour le samedi couvre la sortie longue,
    pas le footing d'allegement de la veille. On reutilise les detecteurs d'avance
    du plan pour que toutes les vues racontent la meme histoire.
    """
    try:
        run_day = _parse_day(run.get("date") or (run.get("start_date_local") or "")[:10])
    except (TypeError, ValueError):
        return None, date.today(), 0
    context = list(recent_runs or [])

    long_target = _advanced_long_target(run_day, run, context)
    if long_target is not None:
        target_day, target_session = long_target
        return target_session, target_day, (target_day - run_day).days

    advanced = _advanced_session_from_run(run, context)
    if advanced is not None:
        return advanced["target_session"], advanced["target_day"], 1

    base = _schedule_for(run_day, context)

    # Filet de rattachement du run. Les detecteurs ci-dessus s'appuient sur les
    # moyennes de la sortie : un "AM 5 x 2 km" echauffement et retour au calme
    # inclus sort a 5:04/km, hors de la fenetre allure marathon, donc le plan ne
    # le voit pas couvrir la seance du lendemain. Ce filet reconnait cette seance
    # a un jour pres. Borne serree : rien de consistant prevu le jour du run,
    # vraie qualite courue, qualite planifiee le lendemain.
    if base.get("category") in {"rest", "easy"} and _is_true_quality_run(run):
        next_day = run_day + timedelta(days=1)
        next_session = _schedule_for(next_day, context)
        if next_session.get("category") == "quality":
            print(
                f"[plan-session] run du {run_day} rattache a la qualite du "
                f"{next_day} (courue 1 j en avance)",
                file=sys.stderr,
            )
            return next_session, next_day, 1

    return base, run_day, 0


def _recovery_already_served_by_advanced_long(
    day: date,
    base_session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> date | None:
    """La recuperation de ce jour a-t-elle deja ete posee la veille ?

    Le gabarit place la recuperation le lendemain de la sortie longue. Quand la
    SL est courue en avance, le jour de SL prevu bascule lui-meme en
    recuperation (`_long_run_done_in_advance`) : le lendemain, deja a J+2 de la
    vraie SL, repetait mot pour mot la meme seance — deux « Footing de
    recuperation · ~6 km · 38 min » d'affilee. Il redevient un footing facile,
    ce qui rend aussi le volume que la veille de SL allegee avait coute.

    Retourne le jour de la vraie SL, ou None si la regle ne s'applique pas.
    """
    if base_session.get("tag") != "recovery":
        return None
    if day.isoformat() in _PLAN_OVERRIDES.get():
        # Un ajustement du coach prime sur l'adaptation automatique.
        return None
    previous = day - timedelta(days=1)
    previous_session = _schedule_for(previous, recent_runs)
    advance = _long_run_done_in_advance(previous, previous_session, recent_runs)
    if advance is None:
        return None
    return previous - timedelta(days=advance[0])


def _advanced_long_recovery_shift(
    day: date,
    base_session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Seance + note quand la recuperation d'apres-SL a deja ete servie la veille.

    Partagee par le jour courant et l'apercu : les deux vues doivent raconter la
    meme chose sur ce jour, sinon le coach et le dashboard divergent des que la
    date change.
    """
    real_long_day = _recovery_already_served_by_advanced_long(day, base_session, recent_runs)
    if real_long_day is None:
        return None
    return _adaptive_easy(45), (
        f"Sortie longue courue le {_fmt_short_day(real_long_day.isoformat())} : la "
        f"recuperation d'apres-SL tombe le {_fmt_short_day((day - timedelta(days=1)).isoformat())}. "
        "Ce jour est deja a J+2, donc footing facile plutot qu'une deuxieme recuperation."
    )


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
        # Le decalage ne vaut que pour "le reste de la semaine" du run, comme le
        # dit la note affichee. La borne "prochaine SL" ne suffit pas : quand un
        # override coach annule les SL suivantes (week-end rando, montagne), la
        # prochaine SL planifiee saute deux semaines plus loin et le decalage
        # d'un jour survivait jusque-la. Un run du lun 27 juil decalait encore
        # les 12-14 aout, alors que le coach matinal lisait la trame non
        # decalee : c'est exactement le desaccord d'un jour entre les deux.
        if day > advance["week_end"]:
            if day == advance["week_end"] + timedelta(days=1):
                # Une seule ligne par run candidat : ce helper est appele pour
                # chaque jour du plan, un log par jour noierait backend.log.
                print(
                    f"[plan-advance] decalage du {advance['run_day']} borne au "
                    f"{advance['week_end']} (fin de sa semaine) : il ne s'applique "
                    f"plus a partir du {day}",
                    file=sys.stderr,
                )
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
    advanced_title = _session_title(advance["target_session"])
    if advance["target_session"].get("category") == "quality":
        # Une qualite avancee echange sa place avec la seance legere qui la
        # precedait. En S6, le lundi etait repos : le mardi devient donc repos.
        # A partir de S7, le lundi porte un footing de volume : c'est ce footing
        # qui passe au mardi. On conserve ainsi le volume et six sorties sans
        # imposer un repos systematique apres chaque qualite avancee.
        if day == advance["target_day"]:
            replacement = _quality_advance_replacement(
                advance["run_day"],
                recent_runs,
            )
            return replacement, _quality_advance_adjustment(
                advanced_title,
                advance["run_day"],
                replacement,
            )
        return None

    pulled_day = day + timedelta(days=1)
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


def _quality_advance_replacement(
    run_day: date,
    recent_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Seance de J a deplacer a J+1 quand la qualite de J+1 est courue a J.

    Seuls un repos ou un footing vraiment leger se deplacent sans risque. Une
    autre seance cle ou une endurance moyenne ne doit jamais etre empilee au
    lendemain d'une qualite : dans ce cas, le repli prudent reste le repos.
    """
    displaced = _schedule_for(run_day, recent_runs)
    if displaced.get("category") == "rest":
        return _rest()
    if displaced.get("category") == "easy" and displaced.get("tag") != "steady":
        return deepcopy(displaced)
    return _rest()


def _quality_advance_adjustment(
    advanced_title: str,
    run_day: date,
    replacement: dict[str, Any],
) -> str:
    run_label = _fmt_short_day(run_day.isoformat())
    if replacement.get("category") == "rest":
        swap = "le repos prevu la veille est deplace aujourd'hui"
    else:
        swap = (
            f"la seance legere prevue la veille ({_session_title(replacement)}) "
            "est deplacee aujourd'hui"
        )
    return (
        f"{advanced_title} courue avec 1 jour d'avance le {run_label} : {swap}. "
        "Le reste de la semaine ne bouge pas."
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
            and not _key_absorbed_in_advance(prev_day, prev_session, recent_runs)
        ):
            block_reason = _key_miss_block_reason(
                prev_day,
                prev_session,
                prev_run,
                recent_runs,
            )
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
        # Regle du plan : une VRAIE SL courue en avance dans la semaine de plan
        # couvre la SL planifiee -> le jour initialement prevu devient
        # recuperation. Un simple medium-long (13-15 km) ne compte pas comme la
        # SL du plan, et la SL de la semaine precedente est exclue par la borne.
        advance = _long_run_done_in_advance(day, base_session, recent_runs)
        if advance is not None:
            advance_days = advance[0]
            return _adaptive_recovery(35), (
                f"Sortie longue deja courue {_advance_label(advance_days)} "
                f"({_fmt_short_day((day - timedelta(days=advance_days)).isoformat())}) : "
                "le jour prevu passe en recuperation."
            )
        return None
    unplanned_hard_yesterday = any(
        age <= 1 and _was_unplanned_hard(run, recent_runs)
        for age, run in ctx["runs"]
    )
    if category == "quality":
        covering_quality = _recent_quality_covering_session(
            base_session,
            ctx["runs"],
            max_age=3,
        )
        if covering_quality is not None:
            quality_age = covering_quality[0]
            if quality_age == 1:
                run_day = day - timedelta(days=1)
                replacement = _quality_advance_replacement(run_day, recent_runs)
                return replacement, _quality_advance_adjustment(
                    _session_title(base_session),
                    run_day,
                    replacement,
                )
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
    recovery_shift = _advanced_long_recovery_shift(day, base_session, recent_runs)
    if recovery_shift is not None:
        return recovery_shift
    return None


def _completed_day_adjustment(
    day: date,
    run: dict[str, Any],
    base_session: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    *,
    same_day: bool = True,
    plan_reference: tuple[dict[str, Any] | None, date, int] | None = None,
) -> str:
    """Note d'ajustement d'un jour deja couru.

    Partagee par le cockpit et la page Plan : les deux vues doivent raconter la
    meme chose sur un jour donne. `same_day` distingue le jour courant d'un jour
    passe relu depuis la page Plan (le texte ne peut pas dire "aujourd'hui").
    """
    when = "aujourd'hui" if same_day else "ce jour-la"
    matched_session, matched_day, advance_days = (
        plan_reference or matched_session_for_run(run, recent_runs)
    )
    if matched_session is not None and advance_days > 0:
        if matched_session.get("category") == "quality":
            replacement = _quality_advance_replacement(day, recent_runs)
            if replacement.get("category") == "rest":
                swap = "Le repos prevu ce jour est deplace au lendemain."
            else:
                swap = (
                    f"La seance legere prevue ce jour ({_session_title(replacement)}) "
                    "est deplacee au lendemain."
                )
            return (
                f"{_session_title(matched_session)} etait prevue le lendemain : "
                f"elle est absorbee avec 1 jour d'avance. {swap} "
                "Le reste de la semaine garde ses dates."
            )
        if matched_session.get("category") == "long":
            target_label = _fmt_short_day(matched_day.isoformat())
            return (
                f"{_session_title(matched_session)} etait prevue {target_label} : elle est "
                f"absorbee {_advance_label(advance_days)}. Le {target_label} "
                "passe en recuperation, les jours d'ici la restent legers."
            )
        return (
            f"{_session_title(matched_session)} etait prevue le lendemain : "
            "elle est absorbee avec 1 jour d'avance. J'avance les prochains "
            "jours d'un cran jusqu'a la prochaine SL, qui reste a sa date."
        )
    direction = _effort_vs_plan(run, base_session)
    if base_session.get("kind") == "rest" and direction == "harder":
        return (
            f"Seance non prevue par le plan absorbee {when} : "
            "pas de rattrapage, les prochains jours restent legers."
        )
    if direction == "lighter" and base_session.get("category") in {"quality", "long"}:
        return (
            f"Seance cle prevue non couverte {when} : pas de rattrapage empile, "
            "je la decale en version allegee sur le prochain jour leger."
        )
    if direction == "harder":
        return (
            "La seance reelle est plus grosse que prevu : je m'aligne sur ce qui "
            f"a ete couru, pas de second bloc {when}."
        )
    if direction == "lighter":
        return (
            f"Seance plus legere que prevu : pas de compensation {when}, "
            "on garde le cap sur la prochaine seance cle."
        )
    return f"Seance deja faite {when} : pas de second bloc a ajouter."


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
    # Un override coach explicite (table plan_overrides) doit primer sur TOUTE
    # adaptation automatique. On le detecte ici : _schedule_for l'a deja renvoye
    # comme base_session, mais sans ce marqueur les branches d'auto-adaptation
    # (rescheduling de seance cle manquee, allegement) ecrasent l'override quand
    # sa categorie est easy/rest. Cf. CLAUDE.md "un override du coach prime sur
    # l'adaptation automatique".
    coach_override = _PLAN_OVERRIDES.get().get(today_iso)
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
        and not _key_absorbed_in_advance(
            day - timedelta(days=1), yesterday_session, recent_runs
        )
        and not _is_taper(day)
    )
    missed_yesterday_block_reason = (
        _key_miss_block_reason(
            day - timedelta(days=1),
            yesterday_session,
            yesterday_run,
            recent_runs,
        )
        if missed_yesterday_key
        else None
    )
    yesterday_hard = bool(yesterday_run and _is_hard_or_long_run(yesterday_run))
    recent_long = context["last_long_age"] is not None and context["last_long_age"] <= 2
    sl_advance = _long_run_done_in_advance(day, base_session, recent_runs)
    recent_sl = sl_advance is not None
    # Une vraie qualite pese sur le lendemain. A J+2, elle ne doit alleger la
    # journee que si la veille a elle-meme ete chargeante : sinon le jour
    # intermediaire a DEJA servi de recuperation et on allegerait deux fois pour
    # la meme seance. Cas reel du 12 aout 2026 : seuil 5x6' le lundi 10, footing
    # recup de 40' le mardi 11 (override coach), et le site rabotait quand meme
    # le footing 55' + lignes du mercredi a 35-45' en allure de recuperation --
    # alors que le coach matinal annoncait la seance pleine.
    recent_true_quality = (
        context["last_quality_age"] is not None
        and (
            context["last_quality_age"] <= 1
            or (context["last_quality_age"] <= 2 and yesterday_hard)
        )
    )
    covering_quality = (
        _recent_quality_covering_session(base_session, context["runs"], max_age=2)
        if base_session.get("category") == "quality"
        else None
    )
    recent_covering_quality = covering_quality is not None
    elevated_easy_hr = context["elevated_easy_hr"]
    positive_trend = any(age <= 5 and _looks_progressive(run) for age, run in context["runs"])

    status = "rest" if base_session.get("kind") == "rest" else "scheduled"
    adjustment = "Rien a changer."
    rescheduled_missed_key = False
    shifted_from_advance = None
    # La recuperation d'apres-SL deja servie la veille valait pour l'apercu mais
    # pas pour le jour courant : le meme dimanche changeait de seance en passant
    # de "J+1 vu samedi" a "aujourd'hui". Une seule regle, les deux vues.
    recovery_shift = None
    if apply_adjustments and not today_run:
        shifted_from_advance = _advanced_shift_for_day(day, recent_runs, as_of)
        recovery_shift = _advanced_long_recovery_shift(day, base_session, recent_runs)

    if apply_adjustments and today_run and day <= as_of:
        status = "done"
        plan_reference = matched_session_for_run(today_run, recent_runs)
        completed_against = (
            plan_reference[0]
            if plan_reference[0] is not None and plan_reference[2] > 0
            else base_session
        )
        session = _complete_today(
            today_run,
            completed_against,
            force_matching=plan_reference[2] > 0,
        )
        adjustment = _completed_day_adjustment(
            day,
            today_run,
            base_session,
            recent_runs,
            plan_reference=plan_reference,
        )
    elif apply_adjustments and coach_override is not None:
        # Override coach explicite : priorite absolue, aucune auto-adaptation
        # (missed-key, allegement, avance) ne doit le remplacer.
        session = deepcopy(coach_override)
        status = "rest" if session.get("kind") == "rest" else "scheduled"
        adjustment = coach_override.get("overrideNote") or "Ajustement du coach applique."
    elif apply_adjustments and shifted_from_advance is not None:
        session, adjustment = shifted_from_advance
        status = "rest" if session.get("kind") == "rest" else "scheduled"
    elif apply_adjustments and recovery_shift is not None:
        session, adjustment = recovery_shift
        status = "scheduled"
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
    elif (
        apply_adjustments
        and base_session.get("category") == "quality"
        and covering_quality is not None
        and covering_quality[0] == 1
    ):
        # La qualite du mardi faite lundi echange sa place avec le contenu du
        # lundi : repos en S6, footing de volume a partir de S7.
        run_day = day - timedelta(days=1)
        session = _quality_advance_replacement(run_day, recent_runs)
        status = "rest" if session.get("kind") == "rest" else "scheduled"
        adjustment = _quality_advance_adjustment(
            _session_title(base_session),
            run_day,
            session,
        )
    elif apply_adjustments and (
        sleep_flags["poor"]
        or elevated_easy_hr
        or yesterday_hard
        or recent_long
        or recent_sl
        or (recent_covering_quality if base_session.get("category") == "quality" else recent_true_quality)
    ):
        status = "scheduled"
        if base_session.get("category") == "quality":
            # Prepa marathon : une sortie longue recente (ou de la veille) ne doit
            # PAS faire sauter un seuil planifie. On ne deload la qualite que sur
            # un vrai signal de fatigue : sommeil bas, FC easy elevee, ou une
            # qualite recente de volume comparable (les SL sont deja exclues).
            if sleep_flags["poor"] or elevated_easy_hr or recent_covering_quality:
                session = _easy(40)
                session["pace_range"] = RECOVERY_PACE
                adjustment = "Recuperation encore limitee : je remplace la qualite par 40' facile pour privilegier la fraicheur."
            else:
                # Seule une sortie longue recente a declenche la branche : en prepa
                # on garde le seuil, on ne l'annule pas pour une SL.
                adjustment = "Sortie longue recente, mais la seance de qualite prevue reste : en prepa on ne l'annule pas pour une SL."
        else:
            genuine_fatigue = sleep_flags["poor"] or elevated_easy_hr
            if base_session.get("category") == "long" and sl_advance is not None:
                advance_days, _advance_run = sl_advance
                session = _adaptive_recovery(35)
                adjustment = (
                    f"Sortie longue deja courue {_advance_label(advance_days)} "
                    f"({_fmt_short_day((day - timedelta(days=advance_days)).isoformat())}) : "
                    "le jour prevu passe en recuperation."
                )
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
    # Les seances terminees gardent leur titre stable ; distance et duree sont
    # exposees dans les champs dedies et dans la meta de la carte.
    title = (
        rendered["title"]
        if status == "done"
        else _title_with_effort(rendered["title"], est_km, est_minutes)
    )

    return {
        "date": today_iso,
        "dateLabel": _fmt_short_day(today_iso),
        "planSource": PLAN_SOURCE,
        "planDescription": PLAN_DESCRIPTION,
        "planBasis": PLAN_BASIS,
        "status": status,
        "statusLabel": status_label,
        "title": title,
        # Le kind distingue le marathon (kind "race", strategie et gels cables
        # dessus) d'une course secondaire (kind "custom", categorie "race").
        # Sans lui, un consommateur ne peut deduire le kind que de la categorie
        # et sert le protocole marathon sur un 10 km.
        "kind": session.get("kind"),
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
    overview = build_plan_overview(anchor, recent_runs)
    current_week = next(
        (week for week in overview["weeks"] if week["start"] <= anchor.isoformat() <= week["end"]),
        None,
    )
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
        "currentWeek": (
            {
                key: current_week[key]
                for key in (
                    "index", "start", "end", "label", "phase", "phaseLabel",
                    "estimatedKmMin", "estimatedKmMax", "plannedRunDaysMin",
                    "plannedRunDaysMax",
                )
            }
            if current_week
            else None
        ),
        "sessions": sessions,
    }


# ── Detail des seances (page Plan dediee) ──────────────────────────────────
# Chaque allure d'entrainement porte une cible FC exprimee en % de FC max :
# le frontend convertit en bpm avec la FC max reelle des 90 derniers jours.

# Les allures viennent du profil, les pourcentages de FC sont physiologiques :
# aucune des deux moities n'est ecrite en dur ici.
_PACE_USAGE = {
    "recovery": "Lendemain de seance dure, fatigue, sommeil bas. Conversation complete sans effort.",
    "easy": "Volume de base, aisance respiratoire totale. La grande majorite du kilometrage.",
    "steady": "Consolidation aerobie. Soutenu, mais jamais dur.",
    "marathon": f"Blocs specifiques et jour J. Cible d'entrainement {GOAL_PACE_TIGHT}.",
    "semi": "Semi test ou blocs longs controles.",
    "threshold": "Tempo et repetitions de 6-10 min. FC stable sur chaque bloc, pas de derive.",
    "vo2": "Rappel vitesse, volume limite. La FC monte en fin de repetition seulement.",
    "strides": "20'' relachees, recup 40'' trot : trop court pour que la FC soit un repere.",
}

_PACE_LABELS = {
    "recovery": "Recuperation",
    "easy": "Footing facile",
    "steady": "Endurance moyenne",
    "marathon": "Allure marathon (AM)",
    "semi": "Allure semi",
    "threshold": "Seuil",
    "vo2": "VO2 / fractionne",
    "strides": "Lignes droites",
}

PACE_REFS = [
    {
        "key": key,
        "label": _PACE_LABELS[key],
        "pace": PROFILE.pace(key),
        "hrPct": list(PACE_TARGETS[key]) if PACE_TARGETS.get(key) else None,
        "usage": _PACE_USAGE[key],
    }
    for key in _PACE_LABELS
]

PHASE_LABELS = {
    "reprise": "Reprise fonciere",
    "base": "Base + premiers blocs AM",
    "specific": "Specifique marathon",
    "deload": "Decharge",
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
    return bool(re.search(r"4:3[57]", text)) or " AM" in f" {text}"


def _estimate_effort(session: dict[str, Any]) -> tuple[float | None, int | None]:
    """Retourne (km estimes, minutes estimees) pour l'affichage et le timing des gels."""
    actual_km = _coerce_float(session.get("actualDistanceKm"))
    actual_minutes = _coerce_float(session.get("actualMinutes"))
    if actual_km is not None:
        return round(actual_km, 1), int(round(actual_minutes)) if actual_minutes else None
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
        if category == "race":
            # Un dossard, c'est l'echauffement + la course + le retour au calme.
            # Sans ce cadre, la page Plan ne compterait que la distance
            # officielle et sous-estimerait la semaine de plusieurs kilometres.
            race_km = _parse_first_km(main)
            race_minutes = _parse_main_minutes(main)
            around = RACE_WARMUP_MINUTES + RACE_COOLDOWN_MINUTES
            if race_km:
                return (
                    round(race_km + around / 5.5, 1),
                    (race_minutes + around) if race_minutes else None,
                )
        if category == "quality":
            # Echauffement 12' + retour au calme 5' autour du corps de seance.
            # Si le corps de seance annonce une duree totale ("75-85' dont ..."),
            # elle inclut deja tout : on la prend telle quelle.
            head = main.split(" x ", 1)[0] if " x " in main else ""
            lead_total = _parse_main_minutes(head) if head else None
            if lead_total and lead_total >= 30:
                return round(lead_total / 5.0, 1), lead_total
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
            total = (work + 17) if work else None
            # Les seances de qualite etaient les seules a renvoyer des minutes
            # mais aucun kilometre : le total de la page Plan les oubliait donc
            # entierement. 5:00/km est une estimation volontairement lisible du
            # mix echauffement + travail + recuperations + retour au calme.
            return (round(total / 5.0, 1), total) if total else (None, None)
        minutes = _parse_main_minutes(main)
        if minutes is None or minutes < 15:
            return None, None
        return round(minutes / 5.5, 1), minutes + 3
    return None, None


def _is_optional_session(session: dict[str, Any]) -> bool:
    """Vrai quand la seance peut explicitement etre remplacee par du repos."""
    if session.get("category") == "rest" or session.get("kind") == "rest":
        return False
    text = _session_text(session).lower()
    return "ou repos" in text or "repos ou" in text


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
        # Segments derives de la seule allure objectif : depart freine de 8 s/km,
        # allure cible sur le corps de course, puis tenir. Rien de propre a une
        # course ou a un coureur donne.
        goal = PROFILE.goal_pace
        paces.append(_pace_chip("Premiers kilometres", f"{_fmt_pace(goal + 8)}/km",
                                "se sentir freine : c'est le seul depart qui ne coute rien"))
        paces.append(_pace_chip("Corps de course", GOAL_PACE, f"cible {PROFILE.goal_label}"))
        paces.append(_pace_chip("Dernier tiers", "tenir, accelerer si possible",
                                "posture et cadence avant tout"))
        for label, key in (("Premiers kilometres", "steady"), ("Corps de course", "marathon"),
                           ("Dernier tiers", "threshold")):
            target = _hr_target(key, label)
            if target:
                hr.append(target)
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

    if tag == "race-10k":
        pace_10k = PROFILE.goal_pace * (10 / 42.195) ** 0.06
        paces.append(_pace_chip("Km 1-2", f"{_fmt_pace(pace_10k + 4)}/km",
                                "le seul vrai risque du jour est de partir trop vite"))
        paces.append(_pace_chip("Km 3-8", f"{_fmt_pace(pace_10k - 2)}-{_fmt_pace(pace_10k + 2)}/km",
                                "monter en allure km apres km, sans a-coup"))
        paces.append(_pace_chip("Km 9-10", "tout donner", "la seule portion ou l'on va se faire mal"))
        hr.append(_hr_target("threshold", "Premiers kilometres", "FC deja au-dessus au km 2 : l'allure est trop rapide, ralentir tout de suite."))
        hr.append(_hr_target("vo2", "Fin de course", "Zone atteinte seulement sur la fin : normal sur un 10 km, pas avant."))
        return paces, [h for h in hr if h]

    if tag == "race-test":
        paces.append(_pace_chip(
            "Semi test", SEMI_PACE,
            f"viser {fmt_clock(PROFILE.projected('semi'))} — c'est ce chrono qui decide la cible marathon",
        ))
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
            paces.append(_pace_chip("Blocs allure marathon", GOAL_PACE, f"cible {GOAL_PACE_TIGHT}, au metronome"))
            hr.append(_hr_target("marathon", "Pendant les blocs AM", f"C'est LA donnee a surveiller : memoriser la FC a {GOAL_PACE_TIGHT} pour le jour J."))
        elif tag == "tempo":
            paces.append(_pace_chip("Tempo", "4:30/km", "entre seuil et allure marathon"))
            hr.append(_hr_target("semi", "Tempo", "Controle : sous la FC de seuil."))
        else:
            paces.append(_pace_chip("Corps de seance", session.get("pace_range", THRESHOLD_PACE)))
        paces.append(_pace_chip("Echauffement / retour au calme", RECOVERY_PACE, "tres facile"))
        hr_easy = _hr_target("easy", "Echauffement / retour au calme", "")
        if hr_easy:
            hr.append(hr_easy)
        return [p for p in paces if p], [h for h in hr if h]

    if category == "long":
        if _has_am_block(session):
            paces.append(_pace_chip("Partie facile", "5:20-5:50/km", "aisance totale, on economise pour le bloc"))
            paces.append(_pace_chip("Bloc allure marathon", GOAL_PACE, f"cible {GOAL_PACE_TIGHT}"))
            hr.append(_hr_target("easy", "Partie facile", "Rester bas : le bloc AM doit demarrer frais."))
            hr.append(_hr_target("marathon", "Bloc AM", "Noter la FC moyenne du bloc : c'est la reference jour J."))
        else:
            paces.append(_pace_chip("Sortie longue facile", "5:20-5:50/km", "volume, pas d'intensite"))
            hr.append(_hr_target("easy", "Toute la sortie", "Derive FC en fin de sortie normale si elle reste sous ~80%."))
        return [p for p in paces if p], [h for h in hr if h]

    # Footing facile par defaut
    paces.append(_pace_chip("Footing facile", EASY_PACE, "aisance respiratoire totale"))
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
        "optional": _is_optional_session(session),
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


def _plan_overview_day(
    day: date,
    planned: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    today: date,
) -> tuple[dict[str, Any], str | None]:
    """Seance a afficher sur la page Plan + note d'ajustement eventuelle.

    Le calendrier reste la reference, mais les runs reellement enregistres
    priment : sans ca la page Plan continue d'annoncer une sortie longue deja
    courue quelques jours plus tot.
    """
    if not recent_runs:
        return planned, None

    run = next((r for r in recent_runs if r.get("date") == day.isoformat()), None)
    if run is not None and day <= today:
        plan_reference = matched_session_for_run(run, recent_runs)
        completed_against = (
            plan_reference[0]
            if plan_reference[0] is not None and plan_reference[2] > 0
            else planned
        )
        return (
            _complete_today(
                run,
                completed_against,
                force_matching=plan_reference[2] > 0,
            ),
            _completed_day_adjustment(
                day,
                run,
                planned,
                recent_runs,
                same_day=day == today,
                plan_reference=plan_reference,
            ),
        )
    # Les jours passes sans run doivent eux aussi etre reconciles. Sinon une SL
    # courue le vendredi restait comptee une seconde fois le samedi sur la page
    # Plan, des que l'on consultait la semaine apres coup.
    reconcile_as_of = today if day > today else day
    reconciled = _reconcile_preview_with_reality(day, planned, recent_runs, reconcile_as_of)
    if reconciled is not None:
        return reconciled
    return planned, None


def build_plan_overview(
    target_day: str | date | datetime | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan complet, semaine par semaine, avec le detail de chaque seance."""
    today = _parse_day(target_day)
    recent_runs = list(recent_runs or [])
    overrides = _PLAN_OVERRIDES.get()
    plan_days = sorted(set(PLAN_CALENDAR) | set(overrides))
    weeks_map: dict[date, list[tuple[date, dict[str, Any]]]] = {}
    for iso in plan_days:
        day = date.fromisoformat(iso)
        monday = day - timedelta(days=day.weekday())
        weeks_map.setdefault(monday, []).append((day, _schedule_for(day, recent_runs)))

    weeks = []
    for monday in sorted(weeks_map):
        sunday = monday + timedelta(days=6)
        # Meme numerotation que le coach : la reprise n'est pas "Semaine 1".
        index = (monday - PLAN_WEEK_ONE).days // 7 + 1
        sessions = []
        for day, planned in weeks_map[monday]:
            session, adjustment = _plan_overview_day(day, planned, recent_runs, today)
            details = _session_details(session)
            sessions.append({
                "date": day.isoformat(),
                "dayLabel": _fmt_short_day(day.isoformat()),
                "isToday": day == today,
                "isPast": day < today,
                "adjustment": adjustment,
                "adjusted": adjustment is not None,
                "plannedTitle": _session_title(planned) if adjustment else None,
                "coachOverride": day.isoformat() in overrides,
                "coachNote": planned.get("overrideNote"),
                **details,
            })
        week_num = (monday - PLAN_WEEK_ONE).days // 7 + 1
        phase = (
            "deload"
            if week_num in PLAN_SHAPE["deloads"]
            else _phase_for(sunday if sunday < RACE_DAY else monday)
        )
        km_max = sum(s["estimatedKm"] or 0 for s in sessions)
        km_min = sum(
            s["estimatedKm"] or 0
            for s in sessions
            if not s["optional"]
        )
        run_days_max = sum(s["category"] != "rest" for s in sessions)
        run_days_min = sum(
            s["category"] != "rest" and not s["optional"]
            for s in sessions
        )
        weeks.append({
            "index": index,
            "start": monday.isoformat(),
            "end": sunday.isoformat(),
            "label": (
                f"{'Semaine ' + str(index) if index >= 1 else 'Reprise'}"
                f" — du {_fmt_short_day(monday.isoformat())}"
                f" au {_fmt_short_day(sunday.isoformat())}"
            ),
            "phase": phase,
            "phaseLabel": PHASE_LABELS.get(phase, phase),
            # estimatedKm reste le maximum pour compatibilite API. Les deux
            # bornes permettent a l'UI de ne plus presenter un "ou repos" comme
            # un kilometrage obligatoire.
            "estimatedKm": int(km_max + 0.5),
            "estimatedKmMin": int(km_min + 0.5),
            "estimatedKmMax": int(km_max + 0.5),
            "plannedRunDaysMin": run_days_min,
            "plannedRunDaysMax": run_days_max,
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
        "weeks": weeks,
    }
