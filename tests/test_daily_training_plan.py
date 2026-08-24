"""Comportement du plan : generation, reconciliation avec le reel, overrides.

Le calendrier est GENERE depuis `runner_profile.PROFILE` (voir `conftest.py`,
qui fige un profil de test). Ces tests ne figent donc jamais le libelle d'une
seance ni un kilometrage : ils localisent les jours par leur ROLE dans le plan
(jour de qualite de la S5, sortie longue de la semaine de pic, semaine de
decharge) et verifient la propriete attendue. Un test qui asserterait
« la SL du 22 aout fait 25 km » ne testerait qu'un plan, celui de son auteur.
"""
import unittest
from datetime import date, timedelta

from daily_training_plan import (
    PLAN_CALENDAR,
    PLAN_SHAPE,
    PLAN_WEEK_ONE,
    RACE_DAY,
    _estimate_effort,
    _interval_structure,
    _latest_sleep_flags,
    _looks_quality,
    _planned_key_was_completed,
    _reschedule_missed_key,
    _schedule_for,
    build_daily_training_guidance,
    build_plan_overview,
    build_three_day_training_guidance,
    build_workout_export,
    normalize_plan_override,
    normalize_recent_training_runs,
    set_plan_overrides,
)
from runner_profile import PROFILE
from workout_builder import build_garmin_workout

GOAL_PACE = PROFILE.pace("marathon")
GOAL_TARGET = PROFILE.pace_target("marathon")


def monday_of(week_num: int) -> date:
    """Lundi de la semaine numerotee `week_num` (S1 = 1)."""
    return PLAN_WEEK_ONE + timedelta(weeks=week_num - 1)


def day_of(week_num: int, weekday: int) -> date:
    return monday_of(week_num) + timedelta(days=weekday)


def quality_day(week_num: int) -> date:
    return day_of(week_num, PROFILE.quality_weekday)


def long_day(week_num: int) -> date:
    return day_of(week_num, PROFILE.long_run_weekday)


def rest_day(week_num: int) -> date:
    return day_of(week_num, PROFILE.rest_weekday)


def planned(day: date) -> dict:
    return PLAN_CALENDAR[day.isoformat()]


def build_weeks(phase: str | None = None, *, deload: bool | None = None) -> list[int]:
    """Numeros des semaines de construction, filtrables par phase et decharge."""
    from daily_training_plan import _phase_of_week

    out = []
    for week_num in range(1, PLAN_SHAPE["buildWeeks"] + 1):
        is_deload = week_num in PLAN_SHAPE["deloads"]
        if deload is not None and is_deload != deload:
            continue
        week_phase = _phase_of_week(
            week_num, PLAN_SHAPE["buildWeeks"], PLAN_SHAPE["baseWeeks"], PLAN_SHAPE["planWeeks"]
        )
        if phase is not None and week_phase != phase:
            continue
        out.append(week_num)
    return out


def week_with_quality_tag(tag: str, phase: str | None = None) -> int:
    """Numero de la premiere semaine de charge dont la qualite porte ce tag."""
    for week_num in build_weeks(phase=phase, deload=False):
        if planned(quality_day(week_num)).get("tag") == tag:
            return week_num
    raise AssertionError(f"aucune semaine de charge avec une qualite {tag!r}")


def first_day_tagged(tag: str) -> date:
    """Premier jour du plan portant ce tag de seance."""
    for day_iso in sorted(PLAN_CALENDAR):
        if PLAN_CALENDAR[day_iso].get("tag") == tag:
            return date.fromisoformat(day_iso)
    raise AssertionError(f"aucune seance taguee {tag!r} dans le plan genere")


def easy_run(day: str) -> dict:
    return {
        "id": day,
        "date": day,
        "start_date_local": f"{day} 08:00:00",
        "distance_km": 5.0,
        "moving_time": 1800,
        "pace_sec_per_km": 360,
        "average_heartrate": 125,
        "max_heartrate": 140,
    }


def long_run(day: str) -> dict:
    return {
        **easy_run(day),
        "distance_km": 17.0,
        "moving_time": 5100,
        "pace_sec_per_km": 300,
        "average_heartrate": 145,
        "max_heartrate": 165,
    }


def medium_long_run(day: str) -> dict:
    return {
        **easy_run(day),
        "distance_km": 14.0,
        "moving_time": 4480,
        "pace_sec_per_km": 320,
        "average_heartrate": 145,
        "max_heartrate": 160,
    }


def hard_run(day: str) -> dict:
    return {
        **easy_run(day),
        "distance_km": 10.0,
        "moving_time": 2800,
        "pace_sec_per_km": 280,
        "average_heartrate": 149,
        "max_heartrate": 171,
    }


def tempo_run(day: str) -> dict:
    return {
        **easy_run(day),
        "distance_km": 9.0,
        "moving_time": 2430,
        "pace_sec_per_km": 270,
        "average_heartrate": None,
        "max_heartrate": None,
    }


def steady_run(day: str) -> dict:
    return {
        **easy_run(day),
        "distance_km": 12.0,
        "distance_m": 12000,
        "moving_time": 3840,
        "pace_sec_per_km": 320,
        "average_heartrate": 145,
        "max_heartrate": 160,
    }


def marathon_long_run(day: str) -> dict:
    """Vraie sortie longue du plan (calibree sur la SL 20 km du 29 juil 2026)."""
    return {
        **easy_run(day),
        "distance_km": 20.2,
        "distance_m": 20200,
        "moving_time": 6100,
        "pace_sec_per_km": 302,
        "average_heartrate": 150,
        "max_heartrate": 168,
    }


def title_base(title: str) -> str:
    return title.split(" · ")[0]


def session_on(overview: dict, day: str) -> dict:
    return next(
        s
        for week in overview["weeks"]
        for s in week["sessions"]
        if s["date"] == day
    )


# Semaine de reference pour les tests de reconciliation : une semaine de charge
# dont la seance dure est un seuil, precedee d'un jour de repos. C'est le motif
# hebdomadaire le plus courant du plan, donc le plus representatif.
THRESHOLD_WEEK = week_with_quality_tag("threshold")
THRESHOLD_DAY = quality_day(THRESHOLD_WEEK)
THRESHOLD_TITLE = planned(THRESHOLD_DAY)["title"]
PREVIOUS_LONG_DAY = long_day(THRESHOLD_WEEK - 1)


class ThreeDayTrainingGuidanceTests(unittest.TestCase):
    def test_normalize_recent_training_runs_keeps_latest_loaded_runs(self):
        runs = normalize_recent_training_runs(
            [
                {"id": "ten", "date": "2026-06-20", "distance_km": 5, "moving_time": 1500},
                {"id": "nine", "date": "2026-06-21", "distance_km": 5, "moving_time": 1500},
                {"id": "eight", "date": "2026-06-22", "distance_km": 5, "moving_time": 1500},
                {"id": "seven", "date": "2026-06-23", "distance_km": 5, "moving_time": 1500},
                {"id": "six", "date": "2026-06-24", "distance_km": 5, "moving_time": 1500},
                {"id": "five", "date": "2026-06-25", "distance_km": 5, "moving_time": 1500},
                {"id": "four", "date": "2026-06-26", "distance_km": 5, "moving_time": 1500},
                {"id": "old", "date": "2026-06-01", "distance_km": "8", "moving_time": "2400"},
                {"id": "future", "date": "2026-07-10", "distance_km": 12, "moving_time": 3600},
                {"id": "latest", "start_date_local": "2026-07-08T07:00:00", "distance_m": 10000, "moving_time": 2600},
                {"id": "mid", "start_date_local": "2026-07-05T07:00:00", "distance_m": 5000, "moving_time": 1500},
            ],
            "2026-07-09",
        )

        self.assertEqual(len(runs), 10)
        self.assertEqual([run["id"] for run in runs[:3]], ["latest", "mid", "four"])
        self.assertEqual(runs[0]["pace_sec_per_km"], 260)
        self.assertEqual(runs[-1]["id"], "old")
        self.assertEqual(runs[-1]["distance_m"], 8000)

    def test_plan_identity_comes_from_the_runner_profile(self):
        forecast = build_three_day_training_guidance(
            "2026-07-09",
            [long_run("2026-07-08")],
        )

        self.assertEqual(forecast["planSource"], "plan-genere")
        # La description nomme la course et l'objectif du profil, pas une course
        # ecrite dans le code.
        self.assertIn(PROFILE.race_name, forecast["planDescription"])
        self.assertIn(PROFILE.goal_label, forecast["planDescription"])
        self.assertEqual(forecast["planBasis"], "Adapte sur les 10 derniers entrainements charges")
        self.assertEqual(forecast["planPeriod"]["end"], RACE_DAY.isoformat())
        self.assertEqual(forecast["dataThrough"], "2026-07-08 08:00:00")

    def test_guidance_returns_today_through_j_plus_7(self):
        forecast = build_three_day_training_guidance("2026-07-09", [])

        labels = [session["relativeLabel"] for session in forecast["sessions"]]
        self.assertEqual(len(forecast["sessions"]), 8)
        self.assertEqual(labels, ["Aujourd'hui", "J+1", "J+2", "J+3", "J+4", "J+5", "J+6", "J+7"])

    def test_long_run_yesterday_turns_today_into_recovery(self):
        forecast = build_three_day_training_guidance(
            "2026-07-09",
            [long_run("2026-07-08")],
        )

        self.assertEqual(title_base(forecast["sessions"][0]["title"]), "Footing de recuperation")
        self.assertIn(PROFILE.pace("recovery"), forecast["sessions"][0]["session"]["main"])
        self.assertIn("Charge recente", forecast["sessions"][0]["adjustment"])

    def test_fresh_quality_day_gets_quality_from_recent_context(self):
        # Un mardi de qualite aborde sur des jambes fraiches rend la seance dure
        # du calendrier, telle qu'elle est planifiee — pas une version allegee.
        target = quality_day(3)
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [
                easy_run((target - timedelta(days=4)).isoformat()),
                long_run((target - timedelta(days=8)).isoformat()),
            ],
        )

        self.assertEqual(
            title_base(forecast["sessions"][0]["title"]), planned(target)["title"]
        )
        self.assertIn("recup", forecast["sessions"][0]["session"]["main"])

    def test_default_plan_uses_saturday_long_runs_and_tuesday_quality(self):
        overview = build_plan_overview("2026-07-20")
        sessions = {
            session["date"]: session
            for week in overview["weeks"]
            for session in week["sessions"]
        }

        self.assertEqual(sessions["2026-07-20"]["category"], "rest")
        self.assertEqual(sessions["2026-07-21"]["category"], "quality")
        self.assertEqual(sessions["2026-07-25"]["category"], "long")
        self.assertEqual(sessions["2026-07-26"]["category"], "easy")

    def test_the_reprise_holds_exactly_one_long_run(self):
        # La reprise posait deux sorties longues quand elle se terminait la
        # veille de la S1 sur le jour de sortie longue : les deux conditions
        # (bon jour de la semaine, dernier jour) matchaient a la fois.
        reprise = next(
            week for week in build_plan_overview()["weeks"] if not week["index"]
        )
        longs = [s for s in reprise["sessions"] if s["category"] == "long"]

        self.assertEqual(len(longs), 1, [s["date"] for s in longs])
        self.assertTrue(reprise["label"].startswith("Reprise —"))
        # Aucune intensite dans la reprise : c'est une mise en route.
        self.assertEqual([s for s in reprise["sessions"] if s["category"] == "quality"], [])

    def test_week_numbering_matches_the_coach_periodisation(self):
        # La reprise est HORS numerotation : la S1 est le premier lundi du bloc.
        # La compter comme "Semaine 1" decalerait tous les libelles du site de +1
        # par rapport a ce que le coach matinal annonce.
        weeks = {week["start"]: week["label"] for week in build_plan_overview()["weeks"]}
        reprise_monday = PLAN_WEEK_ONE - timedelta(weeks=1)

        self.assertTrue(weeks[reprise_monday.isoformat()].startswith("Reprise —"))
        for week_num in (1, 5, PROFILE.plan_weeks):
            label = weeks[monday_of(week_num).isoformat()]
            self.assertTrue(label.startswith(f"Semaine {week_num} —"), label)

    def test_the_volume_ramp_never_exceeds_six_runs_a_week(self):
        # Le volume monte en rendant le jour de repos aux semaines de forte
        # charge — mais le lendemain de sortie longue devient alors un vrai
        # repos. Sans ce garde-fou, la rampe finit par enchainer 21 jours courus.
        overview = build_plan_overview()
        weeks = {week["index"]: week for week in overview["weeks"] if week["index"]}

        for week_num, week in weeks.items():
            self.assertLessEqual(week["plannedRunDaysMax"], 6, f"S{week_num}")

        # Les semaines de forte charge du bloc specifique courent bien six jours.
        heavy = [
            week_num
            for week_num in build_weeks(phase="specific", deload=False)
            if PLAN_SHAPE["longKm"][week_num] >= PROFILE.long_peak_km - 6
        ]
        self.assertTrue(heavy, "le bloc specifique doit contenir des semaines lourdes")
        for week_num in heavy:
            self.assertEqual(weeks[week_num]["plannedRunDaysMax"], 6, f"S{week_num}")
            self.assertEqual(planned(rest_day(week_num))["category"], "easy", f"S{week_num}")

    def test_the_ramp_grows_across_the_charge_weeks(self):
        weeks = {week["index"]: week for week in build_plan_overview()["weeks"] if week["index"]}
        charge = build_weeks(deload=False)

        # La sortie longue, elle, monte strictement : c'est la rampe elle-meme.
        longs = [PLAN_SHAPE["longKm"][week_num] for week_num in charge]
        self.assertEqual(longs, sorted(longs), f"rampe de sortie longue non croissante : {longs}")
        self.assertEqual(
            longs[0], PROFILE.long_start_km, "la premiere semaine part du plancher"
        )
        self.assertEqual(
            longs[-1], PROFILE.long_peak_km, "la derniere semaine de charge atteint le pic"
        )

        # Le volume hebdomadaire monte en TENDANCE, pas semaine par semaine : le
        # type de seance dure fait varier le total de quelques kilometres (un
        # bloc a allure marathon porte plus de volume qu'un seuil court), et
        # exiger une monotonie stricte reviendrait a interdire cette alternance.
        volumes = [weeks[week_num]["estimatedKm"] for week_num in charge]
        half = len(volumes) // 2
        self.assertGreater(
            sum(volumes[half:]) / len(volumes[half:]),
            sum(volumes[:half]) / half,
            f"la seconde moitie du bloc doit peser plus lourd : {volumes}",
        )
        self.assertEqual(max(volumes), volumes[-1], f"le pic est la derniere semaine : {volumes}")

    def test_the_last_build_week_is_a_real_deload_before_the_test_race(self):
        # La semaine qui precede le semi test est toujours une decharge : ce
        # chrono arrete la cible du jour J, il se court sur des jambes fraiches.
        last_build = PLAN_SHAPE["buildWeeks"]
        self.assertIn(last_build, PLAN_SHAPE["deloads"])

        weeks = {week["index"]: week for week in build_plan_overview()["weeks"] if week["index"]}
        week = weeks[last_build]
        previous = weeks[last_build - 1]

        self.assertEqual((week["phase"], week["phaseLabel"]), ("deload", "Decharge"))
        self.assertLessEqual(week["plannedRunDaysMax"], 5)
        self.assertLess(week["estimatedKmMax"], previous["estimatedKmMax"] * 0.85)
        self.assertIn("leger", planned(quality_day(last_build))["title"].lower())
        self.assertIn("sans bloc rapide", planned(long_day(last_build))["main"].lower())

        # Et la semaine suivante porte bien le semi test.
        self.assertEqual(planned(long_day(last_build + 1)).get("tag"), "race-test")

    def test_every_marathon_pace_block_is_calibrated_on_the_goal_pace(self):
        # Une seule allure marathon dans tout le plan, celle de l'objectif. Un
        # bloc AM calibre sur l'allure du semi ou du 10 km serait plus rapide que
        # ce que la course demande — l'erreur qui fait exploser un marathon.
        sessions = {
            session["date"]: session
            for week in build_plan_overview()["weeks"]
            for session in week["sessions"]
        }

        am_days = [
            day
            for day, session in sessions.items()
            if session.get("tag") == "marathon-pace"
        ]
        self.assertTrue(am_days, "le plan doit contenir des rappels d'allure marathon")
        for day in am_days:
            self.assertIn(GOAL_TARGET, sessions[day]["structure"]["main"], day)

        # Meme calibrage dans les blocs AM des sorties longues.
        long_am_days = [
            long_day(week_num).isoformat()
            for week_num in build_weeks(deload=False)
            if PLAN_SHAPE["longAmKm"][week_num]
        ]
        self.assertTrue(long_am_days, "le bloc specifique doit poser de l'AM en sortie longue")
        for day in long_am_days:
            self.assertIn(GOAL_PACE, sessions[day]["structure"]["main"], day)

    def test_the_marathon_pace_dose_grows_across_the_specific_block(self):
        # Ce qui compte est de courir l'allure jambes videes : la dose d'AB en
        # sortie longue doit monter tout au long du bloc specifique. Une SL de
        # 30 km qui demanderait moins d'AM qu'une SL de 25 km serait une
        # regression silencieuse.
        specific = build_weeks(phase="specific", deload=False)
        doses = [PLAN_SHAPE["longAmKm"][week_num] for week_num in specific]

        self.assertEqual(doses, sorted(doses), f"dose AM non croissante : {doses}")
        self.assertEqual(max(doses), PROFILE.long_am_peak_km)

        # Aucune semaine de charge du bloc specifique ne laisse sa sortie longue
        # sans allure marathon.
        for week_num in specific:
            self.assertGreater(PLAN_SHAPE["longAmKm"][week_num], 0, f"S{week_num}")
            self.assertIn(GOAL_PACE, planned(long_day(week_num))["main"], f"S{week_num}")

        # Un bloc AM garde toujours de quoi se mettre en route.
        for week_num in specific:
            self.assertLessEqual(
                PLAN_SHAPE["longAmKm"][week_num],
                PLAN_SHAPE["longKm"][week_num] - 8,
                f"S{week_num}",
            )

        # Les decharges et la fin de l'affutage restent volontairement legeres :
        # a huit jours de la course, plus d'AM ne rapporte plus de forme.
        for week_num in build_weeks(deload=True):
            self.assertEqual(PLAN_SHAPE["longAmKm"][week_num], 0, f"S{week_num}")
        last_taper_long = planned(long_day(PROFILE.plan_weeks - 1))
        self.assertLessEqual(PLAN_SHAPE["longKm"].get(PROFILE.plan_weeks - 1, 0), PROFILE.long_peak_km)
        self.assertIn("km", last_taper_long["main"])

    def test_a_prep_race_is_a_key_session_but_never_a_watch_workout(self):
        # Un dossard de preparation s'ajoute par override du coach, jamais par le
        # generateur : personne ne peut deviner les courses auxquelles un coureur
        # s'inscrit. Ce que le plan doit garantir, c'est son traitement.
        self.addCleanup(set_plan_overrides, {})
        target = quality_day(PROFILE.plan_weeks - 3)
        set_plan_overrides({
            target.isoformat(): {
                "kind": "custom",
                "category": "race",
                "title": "10 km course",
                "warmup": "20' footing progressif + 4 lignes",
                "main": "10 km a bloc",
                "cooldown": "12' tres facile",
            }
        })

        course = session_on(build_plan_overview(target.isoformat()), target.isoformat())

        self.assertEqual((course["category"], course["categoryLabel"]), ("race", "Course"))
        self.assertTrue(course["keySession"])
        # Le kind "race" est cable en dur sur le marathon (42,2 km) : un dossard
        # plus court ne doit surtout pas l'emprunter.
        self.assertEqual(course["kind"], "custom")
        # Une course ne s'exporte pas en seance structuree Garmin.
        self.assertFalse(course["workoutEligible"])
        # Echauffement + dossard + retour au calme, pas seulement les 10 km.
        self.assertGreater(course["estimatedKm"], 13)

    def test_weekly_volume_counts_quality_and_exposes_optional_range(self):
        weeks = {week["index"]: week for week in build_plan_overview()["weeks"] if week["index"]}

        # Un footing "ou repos" ne doit pas se lire comme un kilometrage
        # obligatoire : l'UI montre une plage.
        optional = [
            week
            for week in weeks.values()
            if week["estimatedKmMax"] > week["estimatedKmMin"]
        ]
        self.assertTrue(optional, "le plan pose des seances optionnelles")
        for week in optional:
            self.assertLess(week["plannedRunDaysMin"], week["plannedRunDaysMax"], week["start"])

        # La qualite compte dans le volume : echauffement et retour au calme
        # inclus, une seance dure n'est jamais a zero kilometre.
        target = quality_day(build_weeks(phase="specific", deload=False)[0])
        quality = session_on(build_plan_overview(target.isoformat()), target.isoformat())
        self.assertEqual(quality["category"], "quality")
        self.assertGreater(quality["estimatedKm"], 0)

    def test_guidance_exposes_the_reconciled_current_week(self):
        week_num = build_weeks(phase="specific", deload=False)[0]
        target = quality_day(week_num)
        guidance = build_three_day_training_guidance(
            target.isoformat(),
            [hard_run(target.isoformat())],
        )

        week = guidance["currentWeek"]
        self.assertEqual(week["index"], week_num)
        # La semaine reconciliee reste celle du plan : meme volume, memes jours.
        planned_week = next(
            candidate
            for candidate in build_plan_overview(target.isoformat())["weeks"]
            if candidate["index"] == week_num
        )
        self.assertEqual(week["plannedRunDaysMax"], planned_week["plannedRunDaysMax"])
        self.assertGreater(week["estimatedKmMax"], 0)

    def test_quality_done_one_day_early_moves_the_previous_rest(self):
        # Une semaine ou le jour de repos precede immediatement la qualite : la
        # qualite courue en avance doit deplacer le repos, pas l'effacer.
        week_num = next(
            week for week in build_weeks(phase="base", deload=False)
            if planned(rest_day(week)).get("category") == "rest"
        )
        early = rest_day(week_num)
        quality = quality_day(week_num)
        run = hard_run(early.isoformat())

        on_quality_day = build_daily_training_guidance(
            quality.isoformat(), [run], as_of_day=early.isoformat(), apply_adjustments=False
        )
        day_after = build_daily_training_guidance(
            (quality + timedelta(days=1)).isoformat(),
            [run],
            as_of_day=early.isoformat(),
            apply_adjustments=False,
        )

        self.assertEqual(on_quality_day["category"], "rest")
        self.assertIn("repos prevu la veille est deplace", on_quality_day["adjustment"])
        self.assertEqual(day_after["adjustment"], "Rien a changer.")

    def test_in_a_high_volume_week_an_advanced_quality_shifts_only_one_day(self):
        # Dans une semaine lourde, le jour de repos est devenu un footing de
        # volume. Une qualite courue ce jour-la decale ce footing d'un jour, sans
        # faire glisser toute la semaine.
        week_num = next(
            week for week in build_weeks(phase="specific", deload=False)
            if planned(rest_day(week)).get("category") == "easy"
        )
        early = rest_day(week_num)
        run = hard_run(early.isoformat())

        forecast = build_three_day_training_guidance(early.isoformat(), [run])
        next_day = forecast["sessions"][1]
        day_after = forecast["sessions"][2]

        self.assertEqual(next_day["category"], "easy")
        self.assertEqual(title_base(next_day["title"]), planned(early)["title"])
        self.assertIn("seance legere prevue la veille", next_day["adjustment"])
        self.assertEqual(day_after["adjustment"], "Rien a changer.")

        week = forecast["currentWeek"]
        self.assertEqual(week["plannedRunDaysMax"], 6)

    def test_missed_quality_is_truly_rescheduled_lightened(self):
        # Une qualite manquee est decalee en version allegee le lendemain, au
        # lieu d'etre remplacee par de la recuperation : la seance dure de la
        # semaine ne disparait pas parce qu'elle a saute d'un jour.
        week_num = build_weeks(phase="specific", deload=False)[0]
        quality = quality_day(week_num)
        target = quality + timedelta(days=1)
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [
                easy_run((quality - timedelta(days=5)).isoformat()),
                easy_run((quality - timedelta(days=7)).isoformat()),
            ],
        )

        today = forecast["sessions"][0]
        self.assertEqual(
            title_base(today["title"]), f"{planned(quality)['title']} (allegee, decalee)"
        )
        self.assertIn("allegee", today["session"]["main"])
        self.assertIn("decale", today["adjustment"])

    def test_coach_override_beats_missed_key_reschedule(self):
        # Bug reel du 16 aout 2026 : SL du samedi courue le vendredi (en avance).
        # Le dimanche, la fenetre "SL en avance" avait expire, le site croyait la
        # seance cle "manquee hier" et la recalait en version allegee -- ecrasant
        # l'override coach (footing de recup) pose sur le jour. Sans override, le
        # meme scenario recale bien la qualite (cf. test precedent) ; avec un
        # override explicite, celui-ci doit primer sur toute auto-adaptation.
        self.addCleanup(set_plan_overrides, {})
        set_plan_overrides({
            "2026-08-19": {
                "kind": "custom",
                "category": "easy",
                "title": "Footing de recuperation",
                "main": "30-40' tres facile a 5:40-6:05/km, ou repos",
                "note": "Seance cle deja couverte : recup, rien a rattraper.",
            }
        })

        forecast = build_three_day_training_guidance(
            "2026-08-19",
            [easy_run("2026-08-13"), easy_run("2026-08-11")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Footing de recuperation")
        self.assertNotIn("decale", today["adjustment"])
        self.assertNotIn("manquee", today["adjustment"])

    def test_stale_sleep_does_not_force_recovery(self):
        stale_sleep = {
            "date": "2026-07-06",
            "sleep_score": 40,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 14000,
        }
        forecast = build_three_day_training_guidance(
            "2026-07-12",
            [long_run("2026-07-11")],
            stale_sleep,
        )

        self.assertNotIn("Sommeil", forecast["observations"])

    def test_yesterday_wake_sleep_does_not_steer_today(self):
        yesterday_sleep = {
            "date": "2026-07-29",
            "sleep_score": 49,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 17820,
        }
        forecast = build_three_day_training_guidance(
            "2026-07-30",
            [easy_run("2026-07-24"), long_run("2026-07-20")],
            yesterday_sleep,
        )

        self.assertTrue(forecast["sessions"][0]["title"].startswith("Endurance moyenne"))
        self.assertNotIn("Sommeil", forecast["observations"])
        self.assertIsNone(forecast["sleep"])

    def test_future_session_uses_calendar_structure(self):
        # Les jours a venir sortent du calendrier tel quel : aucune adaptation
        # ne s'invente sur un jour qui n'a pas encore ete couru.
        week_num = build_weeks(phase="base", deload=False)[1]
        start = quality_day(week_num)
        forecast = build_three_day_training_guidance(
            start.isoformat(), [long_run((start - timedelta(days=2)).isoformat())]
        )

        for offset in range(1, 5):
            day = start + timedelta(days=offset)
            session = forecast["sessions"][offset]
            self.assertEqual(session["date"], day.isoformat())
            self.assertEqual(title_base(session["title"]), planned(day)["title"], day.isoformat())
            self.assertEqual(session["adjustment"], "Rien a changer.", day.isoformat())

    def test_today_run_marks_only_today_as_done(self):
        # Un run du jour ne valide que le jour : les suivants restent a venir.
        target = rest_day(2) - timedelta(days=1)  # dimanche de la S1
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [easy_run(target.isoformat())],
        )

        self.assertEqual(forecast["sessions"][0]["status"], "done")
        self.assertEqual(forecast["sessions"][1]["status"], "rest")
        self.assertNotIn(
            forecast["sessions"][2]["status"], {"done"},
        )

    def test_poor_sleep_replaces_quality_but_not_future_structure(self):
        poor_sleep = {
            "date": "2026-07-28",
            "sleep_score": 50,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 18000,
        }
        forecast = build_three_day_training_guidance(
            "2026-07-28",
            [easy_run("2026-07-24"), long_run("2026-07-20")],
            poor_sleep,
        )

        self.assertTrue(forecast["sessions"][0]["title"].startswith("Footing facile"))
        self.assertIn("Recuperation", forecast["sessions"][0]["adjustment"])
        self.assertTrue(forecast["sessions"][1]["title"].startswith("Footing facile + lignes"))
        self.assertTrue(forecast["sessions"][2]["title"].startswith("Endurance moyenne"))

    def test_cautious_sleep_keeps_quality_unchanged(self):
        # Un sommeil moyen n'annule pas une seance dure : seul un sommeil
        # franchement mauvais le fait.
        target = next(
            quality_day(week)
            for week in build_weeks(phase="base", deload=False)
            if planned(quality_day(week)).get("tag") == "threshold"
        )
        cautious_sleep = {
            "date": target.isoformat(),
            "sleep_score": 65,
            "sleep_quality": "fair",
            "sleep_duration_seconds": 25200,
        }
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [
                easy_run((target - timedelta(days=4)).isoformat()),
                long_run((target - timedelta(days=8)).isoformat()),
            ],
            cautious_sleep,
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["category"], "quality")
        self.assertEqual(today["tag"], "threshold")
        self.assertEqual(title_base(today["title"]), planned(target)["title"])
        self.assertEqual(today["session"]["main"], planned(target)["main"])
        self.assertEqual(today["adjustment"], "Rien a changer.")

    def test_cautious_sleep_keeps_long_run_with_marathon_pace_unchanged(self):
        week_num = next(
            week for week in build_weeks(deload=False) if PLAN_SHAPE["longAmKm"][week]
        )
        target = long_day(week_num)
        cautious_sleep = {
            "date": target.isoformat(),
            "sleep_score": 65,
            "sleep_quality": "fair",
            "sleep_duration_seconds": 25200,
        }
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [easy_run((target - timedelta(days=2)).isoformat())],
            cautious_sleep,
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["category"], "long")
        self.assertEqual(title_base(today["title"]), planned(target)["title"])
        self.assertIn(f"{PLAN_SHAPE['longKm'][week_num]} km", today["session"]["main"])
        self.assertIn(GOAL_PACE, today["session"]["main"])
        self.assertEqual(today["adjustment"], "Rien a changer.")

    def test_a_marathon_pace_session_is_prescribed_at_the_goal_pace(self):
        target = first_day_tagged("marathon-pace")
        quality = build_daily_training_guidance(
            target.isoformat(),
            [],
            as_of_day=PLAN_WEEK_ONE.isoformat(),
            apply_adjustments=False,
        )

        self.assertEqual(quality["date"], target.isoformat())
        self.assertEqual(title_base(quality["title"]), planned(target)["title"])
        self.assertIn(GOAL_TARGET, quality["session"]["main"])

    def test_unplanned_run_on_rest_day_is_requalified_from_reality(self):
        # Repos au plan, mais tempo reellement couru : la seance affichee doit
        # decrire le reel, pas la trame.
        # Un repos qui n'est PAS la veille d'une seance dure : sinon le run est
        # lu (a juste titre) comme la qualite du lendemain courue en avance.
        week_num = next(
            week for week in build_weeks(phase="specific", deload=False)
            if planned(long_day(week) + timedelta(days=1)).get("category") == "rest"
        )
        target = long_day(week_num) + timedelta(days=1)
        self.assertEqual(planned(target)["category"], "rest")
        self.assertNotEqual(planned(target + timedelta(days=1)).get("category"), "quality")
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [tempo_run(target.isoformat())],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Seance qualite deja faite")
        self.assertNotIn("Repos", today["title"])
        self.assertIn("prevoyait du repos", today["session"]["main"])
        self.assertIn("non prevue", today["adjustment"])

    def test_preview_easy_day_downgraded_after_unplanned_hard_run(self):
        # Mercredi 22 juil: footing au plan, tempo reellement couru -> jeudi
        # (endurance moyenne au plan) doit passer en recuperation.
        forecast = build_three_day_training_guidance(
            "2026-07-22",
            [tempo_run("2026-07-22")],
        )

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing de recuperation")
        self.assertIn("non planifie", tomorrow["adjustment"])

    def test_planned_quality_preview_yields_when_quality_already_done(self):
        preview = build_daily_training_guidance(
            "2026-07-28",
            [hard_run("2026-07-25")],
            as_of_day="2026-07-27",
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), "Footing facile")
        self.assertIn("Qualite deja faite", preview["adjustment"])

    def test_long_run_with_hr_spike_does_not_cancel_planned_quality(self):
        # Prepa marathon : une sortie longue (avec un simple pic de FC en fin de
        # course) ne doit PAS etre comptee comme une "qualite" recente qui annule
        # le seuil planifie 2 jours plus tard. Regression du 26 juil 2026 : SL de
        # 18 km (max_hr 177) rétrogradait a tort le Seuil 4 x 6' du lundi.
        week_num = next(
            week for week in build_weeks(phase="base", deload=False)
            if planned(quality_day(week)).get("tag") == "threshold"
        )
        target = quality_day(week_num)
        long_with_spike = {
            **long_run((target - timedelta(days=3)).isoformat()),
            "distance_km": 18.0,
            "moving_time": 5340,
            "pace_sec_per_km": 296,
            "average_heartrate": 150,
            "max_heartrate": 177,
        }
        preview = build_daily_training_guidance(
            target.isoformat(),
            [long_with_spike],
            as_of_day=(target - timedelta(days=1)).isoformat(),
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), planned(target)["title"])
        self.assertEqual(preview["tag"], "threshold")
        self.assertNotIn("Qualite deja faite", preview["adjustment"])

    def test_recent_long_run_does_not_cancel_todays_planned_quality(self):
        # Jour courant (apply_adjustments=True) : lundi = Seuil planifie, SL
        # courue samedi (2 j avant). En prepa marathon une SL recente ne doit pas
        # faire sauter le seuil du jour (regression du 27 juil 2026).
        guidance = build_daily_training_guidance(
            THRESHOLD_DAY.isoformat(),
            [long_run(PREVIOUS_LONG_DAY.isoformat())],
            as_of_day=THRESHOLD_DAY.isoformat(),
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), THRESHOLD_TITLE)
        self.assertEqual(guidance["tag"], "threshold")

    def test_long_substitute_on_quality_day_is_absorbed_not_counted_as_threshold(self):
        # Si une SL remplace le seuil le jour meme, elle compte comme charge
        # consommee mais pas comme un seuil techniquement couvert.
        long_with_spike = {
            **long_run("2026-08-25"),
            "distance_km": 18.0,
            "moving_time": 5340,
            "pace_sec_per_km": 296,
            "average_heartrate": 150,
            "max_heartrate": 177,
        }
        forecast = build_three_day_training_guidance("2026-08-25", [long_with_spike])

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Sortie longue deja faite")
        self.assertIn("plus exigeante", today["session"]["main"])
        self.assertIn("plus grosse", today["adjustment"])

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing de recuperation")

    def test_recent_true_quality_downgrades_todays_planned_quality(self):
        # Une vraie qualite recente est un signal de charge distinct d'une SL :
        # dans ce cas le seuil du jour doit bien sauter.
        guidance = build_daily_training_guidance(
            "2026-07-28",
            [hard_run("2026-07-26")],
            as_of_day="2026-07-28",
            apply_adjustments=True,
        )

        self.assertTrue(guidance["title"].startswith("Footing facile"))
        self.assertIn("Recuperation", guidance["adjustment"])

    def test_quality_two_days_ago_does_not_lighten_after_a_recovery_day(self):
        # Cas reel du 12 aout 2026 : seuil 5x6' le lundi 10, footing recup de 40'
        # le mardi 11, et le site rabotait quand meme le footing 55' + lignes du
        # mercredi a 35-45' en allure de recuperation. Le jour intermediaire a
        # deja servi de recuperation : alleger a J+2 revient a compter deux fois
        # la meme seance, et fait dire au site autre chose qu'au coach matinal.
        week_num = build_weeks(phase="specific", deload=False)[0]
        quality = quality_day(week_num)
        target = quality + timedelta(days=2)
        guidance = build_daily_training_guidance(
            target.isoformat(),
            [
                easy_run((quality + timedelta(days=1)).isoformat()),
                hard_run(quality.isoformat()),
            ],
            as_of_day=target.isoformat(),
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), planned(target)["title"])
        self.assertEqual(guidance["adjustment"], "Rien a changer.")

    def test_quality_still_lightens_the_next_day_and_a_loaded_second_day(self):
        # La regle utile est conservee : le lendemain d'une qualite reste allege,
        # et J+2 aussi tant que la veille a ete chargeante (pas de recup posee).
        for day, runs in (
            ("2026-08-19", [hard_run("2026-08-18")]),
            ("2026-08-20", [hard_run("2026-08-19"), hard_run("2026-08-18")]),
        ):
            with self.subTest(day=day):
                guidance = build_daily_training_guidance(
                    day, runs, as_of_day=day, apply_adjustments=True
                )
                self.assertEqual(title_base(guidance["title"]), "Footing de recuperation")
                self.assertIn("Charge recente", guidance["adjustment"])

    def test_long_run_yesterday_does_not_masquerade_as_advanced_marathon_pace(self):
        # Une SL la veille d'une seance AM ne doit pas etre annoncee comme si
        # la seance AM avait ete faite avec 1 jour d'avance.
        target = first_day_tagged("marathon-pace")
        guidance = build_daily_training_guidance(
            target.isoformat(),
            [long_run((target - timedelta(days=1)).isoformat())],
            as_of_day=target.isoformat(),
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), planned(target)["title"])
        self.assertNotIn("courue avec 1 jour d'avance", guidance["adjustment"])

    def test_long_run_done_early_turns_current_long_day_into_recovery(self):
        # Jour courant : si la SL a vraiment ete faite vendredi, samedi ne doit
        # pas proposer une deuxieme SL.
        guidance = build_daily_training_guidance(
            "2026-08-01",
            [long_run("2026-07-31")],
            as_of_day="2026-08-01",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "Footing de recuperation")
        self.assertIn("Sortie longue deja courue", guidance["adjustment"])

    def test_long_run_done_early_does_not_stack_two_recovery_days(self):
        # Le gabarit pose la recup le lendemain de la SL. SL courue le vendredi
        # 31 juil : le samedi 1er aou (jour de SL prevu) devient la recup, et le
        # dimanche 2 aou est deja a J+2 -- il affichait la MEME seance que la
        # veille ("Footing de recuperation · ~6 km · 38 min" deux jours de
        # suite). Il redevient un footing facile.
        preview = build_daily_training_guidance(
            "2026-08-02",
            [long_run("2026-07-31")],
            as_of_day="2026-07-31",
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), "Footing facile")
        self.assertIn("deja a J+2", preview["adjustment"])

    def test_long_run_done_early_is_not_a_missed_key_session_the_next_day(self):
        # Cas reel du dim 23 aout 2026 : SL de 25 km courue le ven 21, rien le
        # sam 22 (jour de SL prevu, passe en recuperation). Le dimanche voyait
        # "seance cle manquee hier" et decalait une "Sortie longue marathon
        # allegee · ~16 km" 48 h apres la SL. Une cle absorbee en avance n'est
        # pas une cle manquee.
        guidance = build_daily_training_guidance(
            "2026-08-02",
            [long_run("2026-07-31")],
            as_of_day="2026-08-02",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "Footing facile")
        self.assertFalse(guidance["rescheduledMissedKey"])
        self.assertNotIn("manquee", guidance["adjustment"])
        self.assertIn("deja a J+2", guidance["adjustment"])

    def test_current_day_and_preview_agree_on_the_day_after_an_advanced_long(self):
        # Le meme dimanche ne doit pas changer de seance selon qu'il est lu comme
        # apercu (depuis le vendredi) ou comme jour courant.
        runs = [long_run("2026-07-31")]
        preview = build_daily_training_guidance(
            "2026-08-02", runs, as_of_day="2026-07-31", apply_adjustments=False
        )
        current = build_daily_training_guidance(
            "2026-08-02", runs, as_of_day="2026-08-02", apply_adjustments=True
        )

        self.assertEqual(title_base(current["title"]), title_base(preview["title"]))
        self.assertEqual(current["adjustment"], preview["adjustment"])

    def test_genuinely_missed_long_run_is_still_rescheduled_lighter(self):
        # Contre-epreuve : SL du samedi remplacee par un footing (elle n'a pas
        # ete couverte en avance) -> le dimanche la decale bien en version allegee.
        guidance = build_daily_training_guidance(
            "2026-08-02",
            [easy_run("2026-08-01")],
            as_of_day="2026-08-02",
            apply_adjustments=True,
        )

        self.assertTrue(guidance["rescheduledMissedKey"])
        self.assertIn("allegee", guidance["title"])

    def test_long_run_done_on_its_day_keeps_the_recovery_the_day_after(self):
        # Contre-epreuve : SL courue a sa date (samedi), le dimanche garde bien
        # sa recuperation d'apres-SL.
        preview = build_daily_training_guidance(
            "2026-08-02",
            [long_run("2026-08-01")],
            as_of_day="2026-08-01",
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), "Footing de recuperation")

    def test_partial_long_run_does_not_cover_planned_long_run(self):
        # 14 km sur une SL 20 km = charge consommee, mais pas SL couverte.
        forecast = build_three_day_training_guidance(
            "2026-08-01",
            [medium_long_run("2026-08-01")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Sortie longue partielle deja faite")
        self.assertIn("n'est pas faite", today["session"]["main"])
        self.assertNotIn("deja couverte", today["session"]["main"])
        self.assertIn("non couverte", today["adjustment"])

    def test_eighty_five_percent_long_run_covers_planned_long_run(self):
        # 17 km = 85% d'une SL 20 km, donc la SL est consideree couverte.
        forecast = build_three_day_training_guidance(
            "2026-08-01",
            [long_run("2026-08-01")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Sortie longue deja faite")
        self.assertIn("deja couverte", today["session"]["main"])

    def test_partial_long_run_early_does_not_cancel_planned_long_run(self):
        # 14 km vendredi ne suffit pas a couvrir la SL 20 km du samedi.
        guidance = build_daily_training_guidance(
            "2026-08-01",
            [medium_long_run("2026-07-31")],
            as_of_day="2026-08-01",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "SL 20 km avec AM")
        self.assertIn("sortie longue prevue reste", guidance["adjustment"])

    def test_recent_long_run_does_not_block_missed_quality_reschedule(self):
        # Mercredi courant : le seuil du mardi est manque, et la SL normale du
        # samedi ne doit pas etre confondue avec une qualite recente bloquante.
        forecast = build_three_day_training_guidance(
            (THRESHOLD_DAY + timedelta(days=1)).isoformat(),
            [long_run(PREVIOUS_LONG_DAY.isoformat())],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), f"{THRESHOLD_TITLE} (allegee, decalee)")
        self.assertIn("decale", today["adjustment"])

    def test_recent_long_run_does_not_block_lighter_quality_day_reschedule(self):
        # Apercu J+1 : mardi a ete couru facile au lieu du seuil. La SL du
        # samedi ne doit pas empecher le seuil allege de passer au mercredi.
        forecast = build_three_day_training_guidance(
            THRESHOLD_DAY.isoformat(),
            [long_run(PREVIOUS_LONG_DAY.isoformat()), easy_run(THRESHOLD_DAY.isoformat())],
        )

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), f"{THRESHOLD_TITLE} (allegee, decalee)")
        self.assertIn("decale", tomorrow["adjustment"])

    def test_alternate_hard_key_day_turns_next_day_into_recovery(self):
        # Si la seance cle n'est pas couverte mais qu'un effort dur/long a ete
        # fait a la place, on recupere au lieu d'empiler un rattrapage le lendemain.
        forecast = build_three_day_training_guidance(
            "2026-07-29",
            [long_run("2026-07-28")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Footing de recuperation")
        self.assertNotIn("Seuil 4 x 6'", today["title"])
        self.assertIn("effort reel etait deja chargeant", today["adjustment"])

    def test_alternate_quality_on_long_day_does_not_reschedule_long_next_day(self):
        # Samedi SL au plan, mais qualite courte courue. Dimanche doit rester en
        # regeneration, pas devenir une SL allegee juste apres une seance dure.
        forecast = build_three_day_training_guidance(
            "2026-07-26",
            [hard_run("2026-07-25")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Footing de recuperation")
        self.assertNotIn("Sortie longue marathon allegee", today["title"])
        self.assertIn("effort reel etait deja chargeant", today["adjustment"])

    def test_lighter_run_on_key_day_flags_key_as_not_covered(self):
        # Mardi 21 juil: Seuil 3 x 8' au plan, mais footing leger reellement couru.
        forecast = build_three_day_training_guidance(
            "2026-07-21",
            [easy_run("2026-07-21")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Footing deja fait")
        self.assertIn("plus legere", today["session"]["main"])
        self.assertIn("n'est pas faite", today["session"]["main"])
        self.assertIn("decale", today["adjustment"])

        # L'apercu de demain (footing au plan) porte la seance cle decalee.
        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Seuil 3 x 8' (allegee, decalee)")
        self.assertIn("decale", tomorrow["adjustment"])

    def test_preview_keeps_plan_when_key_day_has_no_run_yet(self):
        # Pas encore de run le jour de la seance cle : on ne la declare pas
        # manquee prematurement, l'apercu de demain reste celui du plan.
        forecast = build_three_day_training_guidance("2026-07-21", [])

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing facile + lignes")
        self.assertEqual(tomorrow["adjustment"], "Rien a changer.")

    def test_run_matching_plan_keeps_plain_done_message(self):
        forecast = build_three_day_training_guidance(
            "2026-07-09",
            [easy_run("2026-07-09")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["title"], "Footing deja fait")
        self.assertIn("deja couverte", today["session"]["main"])

    def test_race_day_carries_the_profile_race_and_its_goal_pace(self):
        forecast = build_three_day_training_guidance(RACE_DAY.isoformat(), [])

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), PROFILE.race_name)
        self.assertIn(f"calibrage {PROFILE.goal_label}", forecast["planDescription"])
        # La consigne du jour J nomme l'allure objectif, jamais une autre.
        self.assertIn(GOAL_TARGET, today["session"]["main"])
        self.assertIn(PROFILE.goal_label, today["session"]["main"])

    def test_a_session_run_one_day_early_never_moves_the_long_run(self):
        # Une seance courue la veille decale ce qui la suit jusqu'a la prochaine
        # sortie longue exclue : la SL, elle, ne bouge jamais de sa date.
        week_num = build_weeks(phase="base", deload=False)[1]
        long_run_day = long_day(week_num)
        # La veille de l'endurance moyenne : cette seance est courue en avance.
        steady_day = next(
            day_of(week_num, weekday)
            for weekday in range(7)
            if planned(day_of(week_num, weekday)).get("tag") == "steady"
        )
        target = steady_day - timedelta(days=1)
        self.assertLess(target, long_run_day)
        forecast = build_three_day_training_guidance(
            target.isoformat(),
            [steady_run(target.isoformat())],
        )

        by_date = {session["date"]: session for session in forecast["sessions"]}
        self.assertEqual(by_date[target.isoformat()]["status"], "done")
        self.assertIn("prevue le lendemain", by_date[target.isoformat()]["adjustment"])

        long_session = by_date[long_run_day.isoformat()]
        self.assertEqual(long_session["category"], "long")
        self.assertEqual(title_base(long_session["title"]), planned(long_run_day)["title"])

    def test_advanced_quality_swaps_only_previous_day_and_never_shifts_later_days(self):
        # Bug reel du 11 aout 2026 : la qualite du lun 27 juil (avancee du mardi)
        # decalait encore les seances des 12-14 aout, parce que les SL des 1er et
        # 8 aout avaient ete annulees par des overrides coach (week-end rando,
        # montagne) : la borne "prochaine SL" sautait au 15 aout, 18 jours plus
        # loin. Le site affichait donc l'endurance moyenne le mercredi quand la
        # trame du coach matinal la placait le jeudi.
        week_num = next(
            week for week in build_weeks(phase="base", deload=False)
            if planned(rest_day(week)).get("category") == "rest"
        )
        early = rest_day(week_num)
        quality = quality_day(week_num)

        # Les deux sorties longues suivantes sont annulees par le coach : la
        # borne "prochaine SL" saute alors de plusieurs semaines.
        self.addCleanup(set_plan_overrides, {})
        set_plan_overrides({
            long_day(week_num).isoformat(): {"kind": "rest", "note": "Randonnee."},
            long_day(week_num + 1).isoformat(): {"kind": "rest", "note": "Montagne."},
        })

        runs = [hard_run(early.isoformat())]

        # Le jour de repos passe au lendemain. Le jour suivant reste a sa place,
        # sans decalage en chaine.
        same_week = build_plan_overview(early.isoformat(), runs)
        moved = session_on(same_week, quality.isoformat())
        self.assertEqual(moved["category"], "rest")
        self.assertTrue(moved["adjusted"])
        self.assertIn("repos prevu la veille est deplace", moved["adjustment"])

        following_day = session_on(same_week, (quality + timedelta(days=1)).isoformat())
        self.assertEqual(title_base(following_day["title"]), planned(quality + timedelta(days=1))["title"])
        self.assertFalse(following_day["adjusted"])

        # Deux semaines plus tard, la trame reste elle aussi intacte.
        later = monday_of(week_num + 2)
        overview = build_plan_overview(later.isoformat(), runs)
        for offset in (2, 3, 4):
            day = later + timedelta(days=offset)
            session = session_on(overview, day.isoformat())
            self.assertEqual(title_base(session["title"]), planned(day)["title"], day.isoformat())
            self.assertFalse(session["adjusted"], day.isoformat())

    def test_garmin_workout_payload_only_for_long_and_structured_quality(self):
        # Seules les SL et les qualites structurees partent sur la montre : on ne
        # programme pas un footing, et encore moins un jour de repos.
        week_num = next(
            week for week in build_weeks(deload=False) if PLAN_SHAPE["longAmKm"][week]
        )
        long_run_day = long_day(week_num)
        easy_day = long_run_day - timedelta(days=1)

        self.assertIsNone(build_workout_export(rest_day(week_num).isoformat()))
        self.assertIsNone(build_workout_export(easy_day.isoformat()))

        forecast = build_three_day_training_guidance(easy_day.isoformat(), [])
        by_date = {session["date"]: session for session in forecast["sessions"]}
        self.assertFalse(by_date[easy_day.isoformat()]["workoutEligible"])
        self.assertTrue(by_date[long_run_day.isoformat()]["workoutEligible"])

        quality = build_workout_export(quality_day(week_num).isoformat())
        long_run_export = build_workout_export(long_run_day.isoformat())
        self.assertEqual(quality["category"], "quality")
        self.assertEqual(long_run_export["category"], "long")

        workout = build_garmin_workout(
            long_run_export["structure"],
            title=long_run_export["title"],
            category=long_run_export["category"],
            est_minutes=long_run_export["estimatedMinutes"],
        )
        self.assertEqual(workout["sportType"]["sportTypeKey"], "running")
        workout_steps = workout["workoutSegments"][0]["workoutSteps"]
        # Une SL avec bloc AM sort en deux paliers : facile puis allure marathon.
        self.assertEqual([step["stepName"] for step in workout_steps], ["Facile", "Allure marathon"])
        total_m = PLAN_SHAPE["longKm"][week_num] * 1000.0
        self.assertEqual(sum(step["endConditionValue"] for step in workout_steps), total_m)
        self.assertEqual(
            workout_steps[1]["endConditionValue"], PLAN_SHAPE["longAmKm"][week_num] * 1000.0
        )
        self.assertEqual(workout_steps[1]["targetType"]["workoutTargetTypeKey"], "pace.zone")

    def test_garmin_workout_payload_keeps_multiple_repeat_blocks(self):
        # Une seance de rappel de vitesse a deux series distinctes : le payload
        # Garmin doit garder les deux groupes, pas les fondre en un seul.
        export = build_workout_export(first_day_tagged("vo2").isoformat())
        workout = build_garmin_workout(
            export["structure"],
            title=export["title"],
            category=export["category"],
            est_minutes=export["estimatedMinutes"],
        )
        repeats = [
            step
            for step in workout["workoutSegments"][0]["workoutSteps"]
            if step["type"] == "RepeatGroupDTO"
        ]
        self.assertGreaterEqual(len(repeats), 1)
        for group in repeats:
            self.assertGreater(group["numberOfIterations"], 1)
            self.assertGreater(group["workoutSteps"][0]["endConditionValue"], 0)

    def test_garmin_workout_payload_keeps_the_deload_long_run_easy(self):
        deload = min(PLAN_SHAPE["deloads"])
        export = build_workout_export(long_day(deload).isoformat())
        workout = build_garmin_workout(
            export["structure"],
            title=export["title"],
            category=export["category"],
            est_minutes=export["estimatedMinutes"],
        )
        workout_steps = workout["workoutSegments"][0]["workoutSteps"]
        # Une SL de decharge n'a pas de palier d'allure marathon : un seul bloc.
        self.assertEqual([step["stepName"] for step in workout_steps], ["Sortie longue"])
        self.assertEqual(
            sum(step["endConditionValue"] for step in workout_steps),
            PLAN_SHAPE["longKm"][deload] * 1000.0,
        )


class KeySessionCompletionTests(unittest.TestCase):
    """Couverture d'une seance cle : relative au volume prevu, pas absolue."""

    @staticmethod
    def _run(km: float, pace: int, avg: int = 150, mx: int = 172) -> dict:
        return {
            "id": "r", "date": "2026-07-27",
            "distance_km": km, "distance_m": int(km * 1000),
            "moving_time": int(km * pace), "pace_sec_per_km": pace,
            "average_heartrate": avg, "max_heartrate": mx,
        }

    def test_rescheduled_key_session_actually_reduces_volume(self):
        # La seance decalee annonce "-25%" : le contenu ET l'estimation doivent
        # suivre, sinon le titre affiche le volume plein (incoherence).
        session = _schedule_for(THRESHOLD_DAY, [])
        rescheduled = _reschedule_missed_key(session)

        self.assertIn("allegee", rescheduled["title"])
        self.assertNotEqual(rescheduled["main"], session["main"])
        self.assertLess(
            _estimate_effort(rescheduled)[1],
            _estimate_effort(session)[1],
        )

    def test_slow_long_run_does_not_cover_marathon_pace_block(self):
        # Une SL de 20 km a 5:05/km n'est pas un travail a allure marathon.
        session = _schedule_for(date(2026, 8, 18), [])
        self.assertEqual(session["tag"], "marathon-pace")

        self.assertFalse(_planned_key_was_completed(session, self._run(20, 305)))
        self.assertTrue(_planned_key_was_completed(session, self._run(15, 295)))

    def test_taper_marathon_pace_recall_is_covered_when_actually_run(self):
        # "Rappel AM 3 x 1 km" : faire pile la seance doit compter (l'ancien
        # seuil absolu de 8 km ne la reconnaissait jamais).
        target = next(
            day
            for day in (RACE_DAY - timedelta(days=offset) for offset in range(4, 20))
            if PLAN_CALENDAR.get(day.isoformat(), {}).get("tag") == "marathon-pace"
        )
        session = _schedule_for(target, [])
        self.assertEqual(session["tag"], "marathon-pace")

        goal_sec = int(round(PROFILE.goal_pace))
        self.assertTrue(_planned_key_was_completed(session, self._run(3, goal_sec)))
        self.assertFalse(_planned_key_was_completed(session, self._run(8, goal_sec + 33)))

    def test_much_faster_quality_does_not_masquerade_as_marathon_pace(self):
        session = _schedule_for(date(2026, 8, 18), [])

        self.assertFalse(_planned_key_was_completed(session, self._run(15, 240)))

    def test_short_fast_run_does_not_cover_full_threshold_session(self):
        # Seuil 4 x 6' (~47 min) : il faut au moins 70% du volume prevu.
        session = _schedule_for(date(2026, 7, 28), [])

        self.assertFalse(_planned_key_was_completed(session, self._run(3, 270)))
        self.assertFalse(_planned_key_was_completed(session, self._run(5, 280)))
        self.assertTrue(_planned_key_was_completed(session, self._run(12, 265)))

    def test_unmeasurable_run_does_not_cover_quality_by_default(self):
        session = _schedule_for(date(2026, 7, 28), [])
        run = self._run(0, 270)
        run["moving_time"] = 0

        self.assertFalse(_planned_key_was_completed(session, run))

    def test_short_recent_quality_does_not_remove_todays_threshold(self):
        short_quality = self._run(3, 270)
        short_quality["date"] = PREVIOUS_LONG_DAY.isoformat()

        guidance = build_daily_training_guidance(
            THRESHOLD_DAY.isoformat(),
            [short_quality],
            as_of_day=THRESHOLD_DAY.isoformat(),
            apply_adjustments=True,
        )

        self.assertEqual(guidance["tag"], "threshold")
        self.assertEqual(title_base(guidance["title"]), THRESHOLD_TITLE)

    def test_short_recent_quality_does_not_remove_threshold_preview(self):
        short_quality = self._run(3, 270)
        short_quality["date"] = PREVIOUS_LONG_DAY.isoformat()

        guidance = build_daily_training_guidance(
            THRESHOLD_DAY.isoformat(),
            [short_quality],
            as_of_day=(THRESHOLD_DAY - timedelta(days=1)).isoformat(),
            apply_adjustments=False,
        )

        self.assertEqual(guidance["tag"], "threshold")
        self.assertEqual(title_base(guidance["title"]), THRESHOLD_TITLE)

    def test_short_recent_quality_does_not_block_missed_threshold_reschedule(self):
        short_quality = self._run(3, 270)
        short_quality["date"] = (THRESHOLD_DAY - timedelta(days=1)).isoformat()

        target = THRESHOLD_DAY + timedelta(days=1)
        guidance = build_daily_training_guidance(
            target.isoformat(),
            [short_quality],
            as_of_day=target.isoformat(),
            apply_adjustments=True,
        )

        self.assertTrue(guidance["rescheduledMissedKey"])
        self.assertEqual(
            title_base(guidance["title"]),
            f"{THRESHOLD_TITLE} (allegee, decalee)",
        )

    def test_average_sleep_no_longer_flags_anything(self):
        # Seul un sommeil vraiment mauvais doit peser sur la seance.
        average = {"date": "2026-07-27", "sleep_score": 65, "sleep_duration_seconds": 24000}
        bad = {"date": "2026-07-27", "sleep_score": 50, "sleep_duration_seconds": 18000}

        self.assertFalse(_latest_sleep_flags(average, date(2026, 7, 27))["poor"])
        self.assertNotIn("cautious", _latest_sleep_flags(average, date(2026, 7, 27)))
        self.assertTrue(_latest_sleep_flags(bad, date(2026, 7, 27))["poor"])


class IntervalStructureTests(unittest.TestCase):
    """Un fractionne courru en cote se lit dans les laps, pas dans les moyennes.

    Cas reel : un fractionne VO2 couru en montagne ->
    6,7 km a 5:47/km, 136 bpm de moyenne. Sur les seules moyennes, le site
    concluait "footing" et redecalait la VO2 au lendemain.
    """

    @staticmethod
    def _lap(seconds: int, distance_m: int, avg_hr: int) -> dict:
        return {
            "moving_time": seconds,
            "distance_m": distance_m,
            "average_heartrate": avg_hr,
            "max_heartrate": avg_hr + 12,
        }

    def _mountain_vo2(self) -> dict:
        return {
            "id": 1, "date": "2026-08-04",
            "distance_km": 6.7, "distance_m": 6696,
            "moving_time": 2322, "pace_sec_per_km": 346.8,
            "average_heartrate": 136, "max_heartrate": 172,
            "laps": [
                self._lap(353, 1000, 116),   # echauffement
                self._lap(294, 607, 138),
                self._lap(158, 710, 144),    # rep
                self._lap(115, 295, 130),    # recup
                self._lap(185, 611, 160),    # rep
                self._lap(295, 906, 131),    # recup longue
                self._lap(120, 265, 118),
                self._lap(181, 732, 144),    # rep
                self._lap(117, 250, 131),    # recup
                self._lap(178, 749, 148),    # rep
                self._lap(90, 121, 154),
                self._lap(234, 448, 141),    # retour au calme
            ],
        }

    @staticmethod
    def _drifting_easy_run() -> dict:
        # Footing auto-lape au km : la derive cardiaque rend les derniers
        # kilometres "chauds", mais ils sont contigus -> un seul bloc.
        laps = [
            {"moving_time": 330, "distance_m": 1000,
             "average_heartrate": 128 + 3 * i, "max_heartrate": 142 + 3 * i}
            for i in range(10)
        ]
        return {
            "id": 2, "date": "2026-08-04",
            "distance_km": 10.0, "distance_m": 10000,
            "moving_time": 3300, "pace_sec_per_km": 330,
            "average_heartrate": 136, "max_heartrate": 168,
            "laps": laps,
        }

    def test_mountain_intervals_are_read_as_quality(self):
        run = self._mountain_vo2()
        blocks, work_seconds = _interval_structure(run)

        self.assertGreaterEqual(blocks, 3)
        self.assertGreaterEqual(work_seconds, 6 * 60)
        self.assertTrue(_looks_quality(run))

    def test_mountain_intervals_cover_the_planned_vo2(self):
        session = _schedule_for(first_day_tagged("vo2"), [])
        self.assertEqual(session["tag"], "vo2")

        self.assertTrue(_planned_key_was_completed(session, self._mountain_vo2()))

    def test_mountain_intervals_do_not_push_the_session_to_tomorrow(self):
        guidance = build_daily_training_guidance(
            "2026-08-05",
            [self._mountain_vo2()],
            as_of_day="2026-08-04",
            apply_adjustments=False,
        )

        self.assertFalse(guidance["rescheduledMissedKey"])
        self.assertEqual(guidance["category"], "easy")

    def test_drifting_easy_run_is_not_mistaken_for_intervals(self):
        run = self._drifting_easy_run()

        self.assertEqual(_interval_structure(run), (0, 0.0))
        self.assertFalse(_looks_quality(run))

    def test_run_without_laps_still_uses_averages(self):
        run = self._mountain_vo2()
        run.pop("laps")
        run["id"] = 3

        self.assertEqual(_interval_structure(run), (0, 0.0))
        self.assertFalse(_looks_quality(run))


class LongRunDoneInAdvanceTests(unittest.TestCase):
    """SL courue plusieurs jours avant la date prevue.

    Le calendrier place les sorties longues le samedi. Faire la SL le mercredi
    ne doit pas conduire le site a la redemander le samedi.
    """

    # sam 1er aout 2026 : "SL 20 km avec AM" ; SL precedente le sam 25 juil.
    LONG_DAY = "2026-08-01"

    def forecast(self, day: str, runs: list) -> dict:
        return build_three_day_training_guidance(day, runs)

    def session_for(self, forecast: dict, day: str) -> dict:
        return next(s for s in forecast["sessions"] if s["date"] == day)

    def test_long_run_three_days_early_turns_planned_saturday_into_recovery(self):
        forecast = self.forecast("2026-07-29", [marathon_long_run("2026-07-29")])
        saturday = self.session_for(forecast, self.LONG_DAY)

        self.assertEqual(title_base(saturday["title"]), "Footing de recuperation")
        self.assertIn("3 jours d'avance", saturday["adjustment"])
        self.assertIn("mer 29 juil", saturday["adjustment"])

    def test_long_run_three_days_early_is_announced_the_day_it_is_run(self):
        forecast = self.forecast("2026-07-29", [marathon_long_run("2026-07-29")])

        self.assertEqual(forecast["status"], "done")
        self.assertIn("SL 20 km avec AM", forecast["adjustment"])
        self.assertIn("3 jours d'avance", forecast["adjustment"])
        self.assertIn("sam 1 aout", forecast["adjustment"])

    def test_medium_long_three_days_early_keeps_the_planned_long_run(self):
        # 14 km n'atteint pas 85% des 20 km prevus : ce n'est pas la SL du plan.
        forecast = self.forecast("2026-07-29", [medium_long_run("2026-07-29")])
        saturday = self.session_for(forecast, self.LONG_DAY)

        self.assertEqual(title_base(saturday["title"]), "SL 20 km avec AM")

    def test_long_run_five_days_early_keeps_the_planned_long_run(self):
        # Au-dela de la fenetre, on ne considere plus que la SL a ete anticipee :
        # cinq jours avant, c'est la charge du debut de semaine, pas la SL.
        forecast = self.forecast("2026-07-27", [marathon_long_run("2026-07-27")])
        saturday = self.session_for(forecast, self.LONG_DAY)

        self.assertEqual(title_base(saturday["title"]), "SL 20 km avec AM")

    def test_previous_week_long_run_never_cancels_the_next_one(self):
        # La SL du samedi precedent ne doit jamais annuler celle qui vient.
        forecast = self.forecast("2026-07-27", [marathon_long_run("2026-07-25")])
        saturday = self.session_for(forecast, self.LONG_DAY)

        self.assertEqual(title_base(saturday["title"]), "SL 20 km avec AM")

    def test_plan_page_shows_the_same_adjustment_as_the_cockpit(self):
        overview = build_plan_overview("2026-07-29", [marathon_long_run("2026-07-29")])
        saturday = session_on(overview, self.LONG_DAY)
        wednesday = session_on(overview, "2026-07-29")

        self.assertTrue(saturday["adjusted"])
        self.assertEqual(saturday["plannedTitle"], "SL 20 km avec AM")
        self.assertIn("3 jours d'avance", saturday["adjustment"])
        # Le mercredi etait un footing facile au calendrier : la realite prime.
        self.assertTrue(wednesday["adjusted"])
        self.assertEqual(wednesday["plannedTitle"], "Footing facile + lignes")

    def test_plan_page_without_runs_stays_on_the_planned_calendar(self):
        overview = build_plan_overview("2026-07-29")
        saturday = session_on(overview, self.LONG_DAY)

        self.assertFalse(saturday["adjusted"])
        self.assertIsNone(saturday["adjustment"])
        self.assertEqual(title_base(saturday["title"]), "SL 20 km avec AM")


class CoachPlanOverrideTests(unittest.TestCase):
    """Ajustements ecrits par le coach (table plan_overrides).

    Le calendrier marathon est fige dans le code : ces overrides sont le seul
    canal par lequel une decision du coach devient visible sur le site.
    """

    def setUp(self):
        self.addCleanup(set_plan_overrides, {})

    def test_override_replaces_the_planned_session_everywhere(self):
        set_plan_overrides({
            "2026-08-01": {
                "title": "SL 24 km recalee",
                "main": "24 km dont 10 derniers a 4:35/km",
                "category": "long",
                "note": "Deplacee apres discussion.",
            }
        })

        self.assertEqual(_schedule_for(date(2026, 8, 1))["title"], "SL 24 km recalee")
        saturday = session_on(build_plan_overview("2026-07-29"), "2026-08-01")
        self.assertEqual(title_base(saturday["title"]), "SL 24 km recalee")
        self.assertTrue(saturday["coachOverride"])
        self.assertEqual(saturday["coachNote"], "Deplacee apres discussion.")

    def test_override_can_turn_a_session_into_rest(self):
        set_plan_overrides({"2026-08-01": {"kind": "rest", "note": "Voyage."}})

        self.assertEqual(_schedule_for(date(2026, 8, 1))["category"], "rest")

    def test_incomplete_override_never_erases_the_planned_session(self):
        # Un ajustement mal forme doit etre ignore, pas effacer la seance prevue.
        self.assertIsNone(normalize_plan_override({"title": "Sans contenu"}))
        self.assertIsNone(normalize_plan_override({"main": "Sans titre"}))
        self.assertIsNone(normalize_plan_override("pas un dict"))

        set_plan_overrides({"2026-08-01": {"title": "Sans contenu"}})
        self.assertEqual(_schedule_for(date(2026, 8, 1))["title"], "SL 20 km avec AM")

    def test_unknown_category_falls_back_to_easy(self):
        session = normalize_plan_override({
            "title": "Footing libre",
            "main": "45' relache",
            "category": "n-importe-quoi",
        })

        self.assertEqual(session["category"], "easy")

    def test_override_wins_over_the_advance_rule(self):
        # Le coach a explicitement replace une SL le dimanche : la regle
        # d'anticipation ne doit pas la transformer en recuperation.
        set_plan_overrides({
            "2026-08-02": {
                "title": "SL 22 km rattrapage",
                "main": "22 km faciles",
                "category": "long",
            }
        })
        overview = build_plan_overview("2026-07-29", [marathon_long_run("2026-07-29")])
        sunday = session_on(overview, "2026-08-02")

        self.assertEqual(title_base(sunday["title"]), "SL 22 km rattrapage")


if __name__ == "__main__":
    unittest.main()
