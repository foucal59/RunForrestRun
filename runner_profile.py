"""Profil du coureur : caracteristiques, allures derivees et cadrage du plan.

Ce module est la SOURCE UNIQUE des valeurs propres a une personne. Avant lui,
les allures d'entrainement, la FC max, la date de course et le calendrier
etaient ecrits en dur dans `daily_training_plan.py` : le depot ne servait qu'a
son auteur. Toute constante identifiante vit desormais ici, et se resout dans
cet ordre de priorite :

  1. variables d'environnement (`RUNNER_*`, `PLAN_*`) — le deploiement ;
  2. `runner_profile.json` (chemin via `RUNNER_PROFILE_FILE`) — le choix explicite
     du coureur, qui gagne toujours sur ce qui est observe ;
  3. snapshot observe depuis Garmin (`.runtime/runner-profile.json`, ecrit par le
     backend a chaque freshness-check depuis `activity_best_efforts` et la FC max
     des 90 derniers jours) — c'est ce qui rend le plan automatiquement juste
     sans aucune saisie ;
  4. valeurs de repli neutres, volontairement quelconques.

AUCUNE dependance hors stdlib : `scripts/coach_journal.py` promet dans son
en-tete de n'effectuer aucun acces reseau et lit un dump SQL, il ne peut pas
tirer pg8000 pour connaitre une allure.

Les allures ne sont jamais saisies : elles se DEDUISENT d'un seul nombre,
l'objectif marathon, par la formule de Riegel (exposant 1.06) puis par les
ecarts d'entrainement usuels. Un coureur ne renseigne donc que ce qu'il connait
(ses records, ou son objectif chrono), jamais huit fourchettes d'allure qu'il
faudrait maintenir coherentes a la main.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent

# ── Reperes de distance ──────────────────────────────────────────────────────
DISTANCES_KM = {
    "3k": 3.0,
    "5k": 5.0,
    "10k": 10.0,
    "semi": 21.0975,
    "marathon": 42.195,
}

# Exposant de Riegel. 1.06 est la valeur publiee et celle utilisee ailleurs dans
# le projet (projections de la page Performance) : garder le meme evite qu'une
# page annonce un chrono cible et le plan un autre.
RIEGEL_EXPONENT = 1.06

# Une projection de Riegel brute depuis un record court est optimiste : elle
# suppose une endurance specifique deja acquise. Le plan se cale donc un cran en
# dessous, et c'est cette marge qui rend l'objectif tenable.
DEFAULT_GOAL_MARGIN = 0.025

# Repli quand ni objectif ni record n'est connu : un marathon en 4 h (5:41/km).
# Volontairement quelconque — ce n'est le profil de personne.
FALLBACK_GOAL_SECONDS = 4 * 3600

# ── FC ──────────────────────────────────────────────────────────────────────
# Repli seulement : la FC max reelle est celle observee sur 90 jours
# (heart_rate_reference.max_hr_reference), et le frontend convertit les
# pourcentages de PACE_TARGETS en bpm avec cette valeur-la.
FALLBACK_MAX_HR = 185

# ── Ecarts d'entrainement, en secondes par km depuis l'allure marathon ───────
# Ces ecarts sont les relations classiques entre allure objectif marathon et
# allures d'entrainement. Ils sont exposes pour etre ajustables, mais ils n'ont
# rien de personnel : ce sont des rapports physiologiques, pas des chronos.
EASY_OFFSET = (45, 68)
RECOVERY_OFFSET = (62, 90)
STEADY_OFFSET = (25, 42)
MARATHON_BAND = (-2, 3)
SEMI_BAND = (-5, 5)
THRESHOLD_BAND = (-5, 5)
VO2_BAND = (-5, 8)
STRIDES_BAND = (-5, 5)

# Les lignes droites se courent autour de l'allure 1500 m, soit environ 77.5 %
# de l'allure marathon exprimee en secondes par km.
STRIDES_PACE_FACTOR = 0.775

# Fenetre de reconnaissance d'un bloc a allure marathon dans un run reel. Un run
# nettement plus lent est une sortie longue facile, un run nettement plus rapide
# est du seuil : ni l'un ni l'autre ne valide le travail specifique demande.
MARATHON_PACE_TOLERANCE_SLOW = 23
MARATHON_PACE_TOLERANCE_FAST = 17

# Cibles de FC en pourcentage de FC max, par zone. Physiologique, pas personnel.
PACE_TARGETS: dict[str, tuple[float, float] | None] = {
    "recovery": (0.60, 0.70),
    "easy": (0.65, 0.75),
    "steady": (0.73, 0.80),
    "marathon": (0.80, 0.88),
    "semi": (0.85, 0.90),
    "threshold": (0.88, 0.92),
    "vo2": (0.92, 0.97),
    "strides": None,
}


# ── Cadrage du plan ─────────────────────────────────────────────────────────
DEFAULT_PLAN_WEEKS = 15
DEFAULT_TAPER_WEEKS = 3
DEFAULT_LONG_RUN_WEEKDAY = 5   # samedi
DEFAULT_QUALITY_WEEKDAY = 1    # mardi
DEFAULT_REST_WEEKDAY = 0       # lundi

# Bornes de volume de la sortie longue, en km. Ce sont les deux seuls nombres qui
# pilotent la rampe : tout le reste (deload, affutage, dose d'allure marathon)
# s'en deduit.
DEFAULT_LONG_START_KM = 16
DEFAULT_LONG_PEAK_KM = 30
DEFAULT_LONG_AM_START_KM = 6
DEFAULT_LONG_AM_PEAK_KM = 18


def _parse_duration(value: Any) -> int | None:
    """Lit un chrono en secondes depuis 'h:mm:ss', 'mm:ss' ou un nombre."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return int(round(seconds)) if seconds > 0 else None
    text = str(value).strip()
    if not text:
        return None
    # Tolere "45:00 (4:30/km)" : seul le premier groupe temporel compte.
    text = text.split("(")[0].strip()
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return int(round(total)) if total > 0 else None


def _fmt_pace(seconds: float) -> str:
    """Formate une allure en secondes/km vers 'm:ss'."""
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def pace_range(low: float, high: float) -> str:
    """'4:35-4:40/km'. Un seul point si les deux bornes se confondent."""
    lo, hi = _fmt_pace(low), _fmt_pace(high)
    return f"{lo}/km" if lo == hi else f"{lo}-{hi}/km"


def fmt_clock(seconds: int | float | None) -> str:
    """Formate une duree de course : '3h32' ou '1h38', '45:00' sous une heure."""
    if not seconds:
        return ""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}:{secs:02d}"


def riegel(seconds: float, from_km: float, to_km: float) -> float:
    """Projette un chrono d'une distance vers une autre (Riegel, exposant 1.06)."""
    return seconds * (to_km / from_km) ** RIEGEL_EXPONENT


def _pace_at(goal_pace: float, distance_key: str) -> float:
    """Allure de course probable sur `distance_key`, depuis l'allure marathon.

    Riegel applique aux ALLURES : le rapport ne depend que des distances, donc
    (D / marathon) ** (1.06 - 1). Un exposant de 0.06, pas de 1.06.
    """
    ratio = DISTANCES_KM[distance_key] / DISTANCES_KM["marathon"]
    return goal_pace * ratio ** (RIEGEL_EXPONENT - 1)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[runner_profile] {label} illisible ({path}): {exc}", file=sys.stderr)
        return {}
    return payload if isinstance(payload, dict) else {}


def _profile_file() -> Path:
    return Path(os.environ.get("RUNNER_PROFILE_FILE") or REPO_ROOT / "runner_profile.json")


def observed_snapshot_file() -> Path:
    """Snapshot ecrit par le backend depuis la base : records + FC max observee."""
    return Path(
        os.environ.get("RUNNER_OBSERVED_FILE")
        or REPO_ROOT / ".runtime" / "runner-profile.json"
    )


def _env_records() -> dict[str, int]:
    records: dict[str, int] = {}
    for key in ("5k", "10k", "semi", "marathon"):
        raw = os.environ.get(f"RUNNER_PR_{key.upper()}")
        seconds = _parse_duration(raw)
        if seconds:
            records[key] = seconds
    return records


def _coerce_records(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    records: dict[str, int] = {}
    for key, value in payload.items():
        normalized = str(key).strip().lower()
        if normalized not in DISTANCES_KM:
            continue
        seconds = _parse_duration(value)
        if seconds:
            records[normalized] = seconds
    return records


def _coerce_int(value: Any) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _weekday(value: Any, default: int) -> int:
    number = _coerce_int(value) if value is not None and value != "" else None
    if value in (0, "0"):
        number = 0
    if number is None:
        return default
    return number % 7


@dataclass(frozen=True)
class RunnerProfile:
    """Tout ce qui depend de la personne, resolu une fois pour toutes."""

    goal_seconds: int
    goal_source: str
    records: dict[str, int]
    max_hr: int
    max_hr_source: str
    race_name: str
    race_date: date
    plan_start: date
    plan_weeks: int
    taper_weeks: int
    long_run_weekday: int
    quality_weekday: int
    rest_weekday: int
    long_start_km: int
    long_peak_km: int
    long_am_start_km: int
    long_am_peak_km: int
    runner_name: str
    paces: dict[str, tuple[float, float]] = field(default_factory=dict)

    # ── Allures ─────────────────────────────────────────────────────────────
    @property
    def goal_pace(self) -> float:
        """Allure objectif marathon, en secondes par km."""
        return self.goal_seconds / DISTANCES_KM["marathon"]

    def pace(self, key: str) -> str:
        low, high = self.paces[key]
        return pace_range(low, high)

    def pace_target(self, key: str) -> str:
        """Allure ponctuelle a viser (milieu de fourchette), pour une consigne seche."""
        low, high = self.paces[key]
        return f"{_fmt_pace((low + high) / 2)}/km"

    @property
    def goal_label(self) -> str:
        return fmt_clock(self.goal_seconds)

    def projected(self, distance_key: str) -> int:
        """Chrono projete sur une distance, depuis l'objectif marathon."""
        return int(round(_pace_at(self.goal_pace, distance_key) * DISTANCES_KM[distance_key]))

    @property
    def marathon_pace_window(self) -> tuple[float, float]:
        """Fourchette d'allure moyenne acceptee pour reconnaitre un bloc AM couru."""
        target = self.goal_pace
        return (target - MARATHON_PACE_TOLERANCE_FAST, target + MARATHON_PACE_TOLERANCE_SLOW)

    # ── Calendrier ──────────────────────────────────────────────────────────
    @property
    def week_one_monday(self) -> date:
        """Lundi de la S1. Les jours anterieurs sont la reprise, hors numerotation."""
        return self.race_date - timedelta(days=self.race_date.weekday()) - timedelta(
            weeks=self.plan_weeks - 1
        )

    @property
    def taper_start(self) -> date:
        return self.week_one_monday + timedelta(weeks=self.plan_weeks - self.taper_weeks)

    @property
    def description(self) -> str:
        return os.environ.get("PLAN_DESCRIPTION") or (
            f"{self.race_name} — {self.race_date.isoformat()}, "
            f"calibrage {self.goal_label} ({self.pace('marathon')})"
        )

    def as_dict(self) -> dict[str, Any]:
        """Vue serialisable, pour l'API, le MCP et le journal coach."""
        return {
            "runnerName": self.runner_name,
            "raceName": self.race_name,
            "raceDate": self.race_date.isoformat(),
            "planStart": self.plan_start.isoformat(),
            "planWeeks": self.plan_weeks,
            "goalSeconds": self.goal_seconds,
            "goalLabel": self.goal_label,
            "goalPace": self.pace("marathon"),
            "goalSource": self.goal_source,
            "maxHr": self.max_hr,
            "maxHrSource": self.max_hr_source,
            "records": {
                key: {"seconds": value, "label": fmt_clock(value)}
                for key, value in sorted(self.records.items())
            },
            "paces": {key: self.pace(key) for key in self.paces},
        }


def derive_paces(goal_pace: float) -> dict[str, tuple[float, float]]:
    """Toutes les fourchettes d'entrainement, depuis la seule allure objectif.

    Verifie sur plusieurs calibrages : pour un marathon en 3h15 (4:37/km) elle
    rend un seuil a 4:15-4:25, du VO2 a 3:52-4:05, du facile a 5:22-5:45 et de
    la recuperation a 5:39-6:07 — a quelques secondes pres, les fourchettes
    qu'un entraineur pose pour ce niveau.
    """
    pace_3k = _pace_at(goal_pace, "3k")
    pace_10k = _pace_at(goal_pace, "10k")
    pace_semi = _pace_at(goal_pace, "semi")
    # Le seuil se court a l'allure tenable une heure : entre 10 km et semi.
    pace_threshold = (pace_10k + pace_semi) / 2

    return {
        "recovery": (goal_pace + RECOVERY_OFFSET[0], goal_pace + RECOVERY_OFFSET[1]),
        "easy": (goal_pace + EASY_OFFSET[0], goal_pace + EASY_OFFSET[1]),
        "steady": (goal_pace + STEADY_OFFSET[0], goal_pace + STEADY_OFFSET[1]),
        "marathon": (goal_pace + MARATHON_BAND[0], goal_pace + MARATHON_BAND[1]),
        "semi": (pace_semi + SEMI_BAND[0], pace_semi + SEMI_BAND[1]),
        "threshold": (pace_threshold + THRESHOLD_BAND[0], pace_threshold + THRESHOLD_BAND[1]),
        "vo2": (pace_3k + VO2_BAND[0], pace_3k + VO2_BAND[1]),
        "strides": (
            goal_pace * STRIDES_PACE_FACTOR + STRIDES_BAND[0],
            goal_pace * STRIDES_PACE_FACTOR + STRIDES_BAND[1],
        ),
    }


def goal_from_records(records: dict[str, int], margin: float = DEFAULT_GOAL_MARGIN) -> int | None:
    """Projette un objectif marathon depuis le meilleur record disponible.

    Le record le plus LONG gagne : il porte deja de l'endurance specifique, donc
    sa projection est la moins optimiste. Un marathon deja couru sert
    directement de reference, sans marge de conversion supplementaire.
    """
    if not records:
        return None
    if records.get("marathon"):
        return records["marathon"]
    for key in ("semi", "10k", "5k"):
        seconds = records.get(key)
        if not seconds:
            continue
        projected = riegel(seconds, DISTANCES_KM[key], DISTANCES_KM["marathon"])
        return int(round(projected * (1 + margin)))
    return None


def _default_race_date(plan_weeks: int) -> date:
    """Prochain dimanche laissant la place a un bloc complet.

    Sans date de course renseignee, le plan doit quand meme etre affichable :
    on cale une course a `plan_weeks` semaines du prochain lundi.
    """
    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return next_monday + timedelta(weeks=plan_weeks - 1, days=6)


def load_profile() -> RunnerProfile:
    """Resout le profil : environnement > fichier du coureur > observe > repli."""
    configured = _read_json(_profile_file(), "runner_profile.json")
    observed = _read_json(observed_snapshot_file(), "snapshot observe")

    def pick(env_key: str, json_key: str, default: Any = None) -> Any:
        env_value = os.environ.get(env_key)
        if env_value not in (None, ""):
            return env_value
        if configured.get(json_key) not in (None, ""):
            return configured[json_key]
        if observed.get(json_key) not in (None, ""):
            return observed[json_key]
        return default

    # ── Records : le coureur d'abord, Garmin ensuite ──
    records = {
        **_coerce_records(observed.get("records")),
        **_coerce_records(configured.get("records")),
        **_env_records(),
    }

    # ── Objectif ──
    goal_seconds = _parse_duration(pick("RUNNER_GOAL_TIME", "goalTime"))
    if goal_seconds:
        goal_source = "configured"
    else:
        goal_seconds = goal_from_records(records)
        goal_source = "projected_from_records" if goal_seconds else "fallback"
    if not goal_seconds:
        goal_seconds = FALLBACK_GOAL_SECONDS

    # ── FC max ──
    max_hr = _coerce_int(os.environ.get("RUNNER_MAX_HR")) or _coerce_int(configured.get("maxHr"))
    if max_hr:
        max_hr_source = "configured"
    else:
        max_hr = _coerce_int(observed.get("maxHr"))
        max_hr_source = "observed_90d" if max_hr else "fallback"
    if not max_hr:
        max_hr = FALLBACK_MAX_HR

    # ── Cadrage ──
    plan_weeks = max(6, _coerce_int(pick("PLAN_WEEKS", "planWeeks")) or DEFAULT_PLAN_WEEKS)
    taper_weeks = max(1, min(plan_weeks - 2,
                             _coerce_int(pick("PLAN_TAPER_WEEKS", "taperWeeks"))
                             or DEFAULT_TAPER_WEEKS))
    race_date = _parse_date(pick("PLAN_RACE_DATE", "raceDate")) or _default_race_date(plan_weeks)
    week_one = race_date - timedelta(days=race_date.weekday()) - timedelta(weeks=plan_weeks - 1)
    plan_start = _parse_date(pick("PLAN_START_DATE", "planStart"))
    if plan_start is None or plan_start >= week_one:
        # Sans date de debut, la reprise occupe la demi-semaine qui precede la S1.
        plan_start = week_one - timedelta(days=4)

    profile = RunnerProfile(
        goal_seconds=goal_seconds,
        goal_source=goal_source,
        records=records,
        max_hr=max_hr,
        max_hr_source=max_hr_source,
        race_name=str(pick("PLAN_RACE_NAME", "raceName", "Marathon")),
        race_date=race_date,
        plan_start=plan_start,
        plan_weeks=plan_weeks,
        taper_weeks=taper_weeks,
        long_run_weekday=_weekday(pick("PLAN_LONG_RUN_WEEKDAY", "longRunWeekday"),
                                 DEFAULT_LONG_RUN_WEEKDAY),
        quality_weekday=_weekday(pick("PLAN_QUALITY_WEEKDAY", "qualityWeekday"),
                                 DEFAULT_QUALITY_WEEKDAY),
        rest_weekday=_weekday(pick("PLAN_REST_WEEKDAY", "restWeekday"), DEFAULT_REST_WEEKDAY),
        long_start_km=_coerce_int(pick("PLAN_LONG_START_KM", "longStartKm"))
        or DEFAULT_LONG_START_KM,
        long_peak_km=_coerce_int(pick("PLAN_LONG_PEAK_KM", "longPeakKm")) or DEFAULT_LONG_PEAK_KM,
        long_am_start_km=_coerce_int(pick("PLAN_LONG_AM_START_KM", "longAmStartKm"))
        or DEFAULT_LONG_AM_START_KM,
        long_am_peak_km=_coerce_int(pick("PLAN_LONG_AM_PEAK_KM", "longAmPeakKm"))
        or DEFAULT_LONG_AM_PEAK_KM,
        runner_name=str(pick("PLAN_RUNNER_NAME", "runnerName", "")),
    )
    return RunnerProfile(**{**profile.__dict__, "paces": derive_paces(profile.goal_pace)})


def write_observed_snapshot(
    records: dict[str, int] | None,
    max_hr: float | int | None,
    *,
    path: Path | None = None,
) -> bool:
    """Ecrit ce que Garmin nous apprend du coureur, pour le prochain demarrage.

    Appele par le backend apres un freshness-check. Le plan lit ce fichier a
    l'import : le coureur n'a donc rien a saisir pour que ses allures se calent
    sur ses records reels, et un `runner_profile.json` reste prioritaire.
    """
    target = path or observed_snapshot_file()
    payload: dict[str, Any] = {}
    clean_records = {
        key: value
        for key, value in (records or {}).items()
        if key in DISTANCES_KM and _coerce_int(value)
    }
    if clean_records:
        payload["records"] = clean_records
    observed_hr = _coerce_int(max_hr)
    if observed_hr:
        payload["maxHr"] = observed_hr
    if not payload:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"[runner_profile] snapshot non ecrit ({target}): {exc}", file=sys.stderr)
        return False
    return True


PROFILE = load_profile()


def reload_profile() -> RunnerProfile:
    """Relit le profil (tests, ou apres reecriture du snapshot observe)."""
    global PROFILE
    PROFILE = load_profile()
    return PROFILE


__all__ = [
    "DISTANCES_KM",
    "PACE_TARGETS",
    "PROFILE",
    "RunnerProfile",
    "derive_paces",
    "fmt_clock",
    "goal_from_records",
    "load_profile",
    "observed_snapshot_file",
    "pace_range",
    "reload_profile",
    "riegel",
    "write_observed_snapshot",
]
