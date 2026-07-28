import os
import unittest
from datetime import date

os.environ["PLAN_START_DATE"] = "2030-01-03"
os.environ["PLAN_RACE_DATE"] = "2030-04-21"

from daily_training_plan import (
    _estimate_effort,
    _latest_sleep_flags,
    _planned_key_was_completed,
    _reschedule_missed_key,
    _schedule_for,
    build_daily_training_guidance,
    build_plan_overview,
    build_three_day_training_guidance,
    build_workout_export,
    normalize_recent_training_runs,
)
from workout_builder import build_garmin_workout


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


def title_base(title: str) -> str:
    return title.split(" · ")[0]


class ThreeDayTrainingGuidanceTests(unittest.TestCase):
    def test_normalize_recent_training_runs_keeps_latest_loaded_runs(self):
        runs = normalize_recent_training_runs(
            [
                {"id": "ten", "date": "2029-12-15", "distance_km": 5, "moving_time": 1500},
                {"id": "nine", "date": "2029-12-16", "distance_km": 5, "moving_time": 1500},
                {"id": "eight", "date": "2029-12-17", "distance_km": 5, "moving_time": 1500},
                {"id": "seven", "date": "2029-12-18", "distance_km": 5, "moving_time": 1500},
                {"id": "six", "date": "2029-12-19", "distance_km": 5, "moving_time": 1500},
                {"id": "five", "date": "2029-12-20", "distance_km": 5, "moving_time": 1500},
                {"id": "four", "date": "2029-12-21", "distance_km": 5, "moving_time": 1500},
                {"id": "old", "date": "2029-11-26", "distance_km": "8", "moving_time": "2400"},
                {"id": "future", "date": "2030-01-04", "distance_km": 12, "moving_time": 3600},
                {"id": "latest", "start_date_local": "2030-01-02T07:00:00", "distance_m": 10000, "moving_time": 2600},
                {"id": "mid", "start_date_local": "2029-12-30T07:00:00", "distance_m": 5000, "moving_time": 1500},
            ],
            "2030-01-03",
        )

        self.assertEqual(len(runs), 10)
        self.assertEqual([run["id"] for run in runs[:3]], ["latest", "mid", "four"])
        self.assertEqual(runs[0]["pace_sec_per_km"], 260)
        self.assertEqual(runs[-1]["id"], "old")
        self.assertEqual(runs[-1]["distance_m"], 8000)

    def test_marathon_template_source_and_basis_are_returned(self):
        forecast = build_three_day_training_guidance(
            "2030-01-03",
            [long_run("2030-01-02")],
        )

        self.assertEqual(forecast["planSource"], "marathon-template")
        self.assertEqual(
            forecast["planDescription"],
            "Coach Marathon (modele de 16 semaines a personnaliser)",
        )
        self.assertEqual(forecast["planBasis"], "Adapte sur les 10 derniers entrainements charges")
        self.assertEqual(forecast["planPeriod"]["end"], "2030-04-21")
        self.assertEqual(forecast["dataThrough"], "2030-01-02 08:00:00")

    def test_guidance_returns_today_through_j_plus_7(self):
        forecast = build_three_day_training_guidance("2030-01-03", [])

        labels = [session["relativeLabel"] for session in forecast["sessions"]]
        self.assertEqual(len(forecast["sessions"]), 8)
        self.assertEqual(labels, ["Aujourd'hui", "J+1", "J+2", "J+3", "J+4", "J+5", "J+6", "J+7"])

    def test_long_run_yesterday_turns_today_into_recovery(self):
        forecast = build_three_day_training_guidance(
            "2030-01-03",
            [long_run("2030-01-02")],
        )

        self.assertEqual(title_base(forecast["sessions"][0]["title"]), "Footing de recuperation")
        self.assertIn("6:35-7:05/km", forecast["sessions"][0]["session"]["main"])
        self.assertIn("Charge recente", forecast["sessions"][0]["adjustment"])

    def test_fresh_monday_gets_quality_from_recent_context(self):
        forecast = build_three_day_training_guidance(
            "2030-01-21",
            [easy_run("2030-01-18"), long_run("2030-01-14")],
        )

        self.assertEqual(title_base(forecast["sessions"][0]["title"]), "Seuil 4 x 6'")
        self.assertIn("recup", forecast["sessions"][0]["session"]["main"])

    def test_default_plan_uses_saturday_long_runs_and_monday_quality(self):
        overview = build_plan_overview("2030-01-14")
        sessions = {
            session["date"]: session
            for week in overview["weeks"]
            for session in week["sessions"]
        }

        self.assertEqual(sessions["2030-01-14"]["category"], "quality")
        self.assertEqual(sessions["2030-01-19"]["category"], "long")
        self.assertEqual(sessions["2030-01-20"]["category"], "rest")

    def test_missed_quality_is_truly_rescheduled_lightened(self):
        # Qualite AM 5 x 2 km manquee le 17 aout : le 18, elle est decalee en
        # version allegee au lieu d'etre remplacee par de la recuperation.
        forecast = build_three_day_training_guidance(
            "2030-02-12",
            [easy_run("2030-02-06"), easy_run("2030-02-04")],
        )

        self.assertEqual(title_base(forecast["sessions"][0]["title"]), "AM 5 x 2 km (allegee, decalee)")
        self.assertIn("allegee", forecast["sessions"][0]["session"]["main"])
        self.assertIn("decale", forecast["sessions"][0]["adjustment"])

    def test_stale_sleep_does_not_force_recovery(self):
        stale_sleep = {
            "date": "2029-12-31",
            "sleep_score": 40,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 14000,
        }
        forecast = build_three_day_training_guidance(
            "2030-01-06",
            [long_run("2030-01-05")],
            stale_sleep,
        )

        self.assertNotIn("Sommeil", forecast["observations"])

    def test_yesterday_wake_sleep_does_not_steer_today(self):
        yesterday_sleep = {
            "date": "2030-01-23",
            "sleep_score": 49,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 17820,
        }
        forecast = build_three_day_training_guidance(
            "2030-01-24",
            [easy_run("2030-01-18"), long_run("2030-01-14")],
            yesterday_sleep,
        )

        self.assertTrue(forecast["sessions"][0]["title"].startswith("Endurance moyenne"))
        self.assertNotIn("Sommeil", forecast["observations"])
        self.assertIsNone(forecast["sleep"])

    def test_future_session_uses_calendar_structure(self):
        forecast = build_three_day_training_guidance("2030-01-14", [long_run("2030-01-13")])

        tomorrow = forecast["sessions"][1]
        self.assertEqual(tomorrow["date"], "2030-01-15")
        self.assertEqual(title_base(tomorrow["title"]), "Footing facile + lignes")
        self.assertEqual(tomorrow["adjustment"], "Rien a changer.")
        self.assertEqual(forecast["sessions"][5]["date"], "2030-01-19")
        self.assertEqual(title_base(forecast["sessions"][5]["title"]), "SL 18 km avec AM")

    def test_today_run_marks_only_today_as_done(self):
        forecast = build_three_day_training_guidance(
            "2030-01-03",
            [easy_run("2030-01-03")],
        )

        self.assertEqual(forecast["sessions"][0]["status"], "done")
        self.assertEqual(forecast["sessions"][1]["status"], "rest")
        self.assertEqual(forecast["sessions"][2]["status"], "scheduled")

    def test_poor_sleep_replaces_quality_but_not_future_structure(self):
        poor_sleep = {
            "date": "2030-01-21",
            "sleep_score": 50,
            "sleep_quality": "poor",
            "sleep_duration_seconds": 18000,
        }
        forecast = build_three_day_training_guidance(
            "2030-01-21",
            [easy_run("2030-01-18"), long_run("2030-01-14")],
            poor_sleep,
        )

        self.assertTrue(forecast["sessions"][0]["title"].startswith("Footing facile"))
        self.assertIn("Recuperation", forecast["sessions"][0]["adjustment"])
        self.assertTrue(forecast["sessions"][1]["title"].startswith("Footing facile + lignes"))
        self.assertTrue(forecast["sessions"][2]["title"].startswith("Repos"))

    def test_cautious_sleep_keeps_quality_unchanged(self):
        cautious_sleep = {
            "date": "2030-01-21",
            "sleep_score": 65,
            "sleep_quality": "fair",
            "sleep_duration_seconds": 25200,
        }
        forecast = build_three_day_training_guidance(
            "2030-01-21",
            [easy_run("2030-01-18"), long_run("2030-01-14")],
            cautious_sleep,
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["category"], "quality")
        self.assertEqual(today["tag"], "threshold")
        self.assertEqual(title_base(today["title"]), "Seuil 4 x 6'")
        self.assertIn("4 x 6'", today["session"]["main"])
        self.assertEqual(today["adjustment"], "Rien a changer.")

    def test_cautious_sleep_keeps_long_run_with_marathon_pace_unchanged(self):
        cautious_sleep = {
            "date": "2030-01-26",
            "sleep_score": 65,
            "sleep_quality": "fair",
            "sleep_duration_seconds": 25200,
        }
        forecast = build_three_day_training_guidance(
            "2030-01-26",
            [easy_run("2030-01-24")],
            cautious_sleep,
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["category"], "long")
        self.assertEqual(title_base(today["title"]), "SL 20 km avec AM")
        self.assertIn("20 km", today["session"]["main"])
        self.assertIn("8 derniers", today["session"]["main"])
        self.assertIn("5:25-5:35/km", today["session"]["main"])
        self.assertEqual(today["adjustment"], "Rien a changer.")

    def test_specific_marathon_sessions_match_pdf_calibration(self):
        quality = build_daily_training_guidance(
            "2030-02-11",
            [],
            as_of_day="2030-01-07",
            apply_adjustments=False,
        )

        self.assertEqual(quality["date"], "2030-02-11")
        self.assertEqual(title_base(quality["title"]), "AM 5 x 2 km")
        self.assertIn("5:30/km", quality["session"]["main"])

    def test_unplanned_run_on_rest_day_is_requalified_from_reality(self):
        # Mercredi 22 juil: repos au plan, mais tempo reellement couru.
        forecast = build_three_day_training_guidance(
            "2030-01-16",
            [tempo_run("2030-01-16")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Seance qualite deja faite")
        self.assertNotIn("Repos", today["title"])
        self.assertIn("prevoyait du repos", today["session"]["main"])
        self.assertIn("non prevue", today["adjustment"])

        # Le lendemain s'allege: la seance non planifiee vient d'etre consommee.
        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing de recuperation")
        self.assertIn("non planifie", tomorrow["adjustment"])

    def test_preview_easy_day_downgraded_after_unplanned_hard_run(self):
        # Jeudi 23 juil: repos au plan, tempo reellement couru -> vendredi
        # (endurance moyenne au plan) doit passer en recuperation.
        forecast = build_three_day_training_guidance(
            "2030-01-16",
            [tempo_run("2030-01-16")],
        )

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing de recuperation")
        self.assertIn("non planifie", tomorrow["adjustment"])

    def test_planned_quality_preview_yields_when_quality_already_done(self):
        preview = build_daily_training_guidance(
            "2030-01-21",
            [hard_run("2030-01-19")],
            as_of_day="2030-01-20",
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), "Footing facile")
        self.assertIn("Qualite deja faite", preview["adjustment"])

    def test_long_run_with_hr_spike_does_not_cancel_planned_quality(self):
        # Prepa marathon : une sortie longue (avec un simple pic de FC en fin de
        # course) ne doit PAS etre comptee comme une "qualite" recente qui annule
        # le seuil planifie 2 jours plus tard. Regression du 26 juil 2026 : SL de
        # 18 km (max_hr 177) rétrogradait a tort le Seuil 4 x 6' du lundi.
        long_with_spike = {
            **long_run("2030-01-19"),
            "distance_km": 18.0,
            "moving_time": 5340,
            "pace_sec_per_km": 296,
            "average_heartrate": 150,
            "max_heartrate": 177,
        }
        preview = build_daily_training_guidance(
            "2030-01-21",
            [long_with_spike],
            as_of_day="2030-01-20",
            apply_adjustments=False,
        )

        self.assertEqual(title_base(preview["title"]), "Seuil 4 x 6'")
        self.assertEqual(preview["tag"], "threshold")
        self.assertNotIn("Qualite deja faite", preview["adjustment"])

    def test_recent_long_run_does_not_cancel_todays_planned_quality(self):
        # Jour courant (apply_adjustments=True) : lundi = Seuil planifie, SL
        # courue samedi (2 j avant). En prepa marathon une SL recente ne doit pas
        # faire sauter le seuil du jour (regression du 27 juil 2026).
        guidance = build_daily_training_guidance(
            "2030-01-21",
            [long_run("2030-01-19")],
            as_of_day="2030-01-21",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "Seuil 4 x 6'")
        self.assertEqual(guidance["tag"], "threshold")

    def test_long_substitute_on_quality_day_is_absorbed_not_counted_as_threshold(self):
        # Si une SL remplace le seuil le jour meme, elle compte comme charge
        # consommee mais pas comme un seuil techniquement couvert.
        long_with_spike = {
            **long_run("2030-01-21"),
            "distance_km": 18.0,
            "moving_time": 5340,
            "pace_sec_per_km": 296,
            "average_heartrate": 150,
            "max_heartrate": 177,
        }
        forecast = build_three_day_training_guidance("2030-01-21", [long_with_spike])

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
            "2030-01-21",
            [hard_run("2030-01-19")],
            as_of_day="2030-01-21",
            apply_adjustments=True,
        )

        self.assertTrue(guidance["title"].startswith("Footing facile"))
        self.assertIn("Recuperation", guidance["adjustment"])

    def test_long_run_yesterday_does_not_masquerade_as_advanced_marathon_pace(self):
        # Une SL la veille d'une seance AM ne doit pas etre annoncee comme si
        # la seance AM avait ete faite avec 1 jour d'avance.
        guidance = build_daily_training_guidance(
            "2030-02-11",
            [long_run("2030-02-10")],
            as_of_day="2030-02-11",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "AM 5 x 2 km")
        self.assertNotIn("courue avec 1 jour d'avance", guidance["adjustment"])

    def test_long_run_done_early_turns_current_long_day_into_recovery(self):
        # Jour courant : si la SL a vraiment ete faite vendredi, samedi ne doit
        # pas proposer une deuxieme SL.
        guidance = build_daily_training_guidance(
            "2030-01-26",
            [long_run("2030-01-25")],
            as_of_day="2030-01-26",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "Footing de recuperation")
        self.assertIn("Sortie longue deja courue", guidance["adjustment"])

    def test_partial_long_run_does_not_cover_planned_long_run(self):
        # 14 km sur une SL 20 km = charge consommee, mais pas SL couverte.
        forecast = build_three_day_training_guidance(
            "2030-01-26",
            [medium_long_run("2030-01-26")],
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
            "2030-01-26",
            [long_run("2030-01-26")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["status"], "done")
        self.assertEqual(today["title"], "Sortie longue deja faite")
        self.assertIn("deja couverte", today["session"]["main"])

    def test_partial_long_run_early_does_not_cancel_planned_long_run(self):
        # 14 km vendredi ne suffit pas a couvrir la SL 20 km du samedi.
        guidance = build_daily_training_guidance(
            "2030-01-26",
            [medium_long_run("2030-01-25")],
            as_of_day="2030-01-26",
            apply_adjustments=True,
        )

        self.assertEqual(title_base(guidance["title"]), "SL 20 km avec AM")
        self.assertIn("sortie longue prevue reste", guidance["adjustment"])

    def test_recent_long_run_does_not_block_missed_quality_reschedule(self):
        # Mardi courant : le seuil du lundi est manque, et la SL normale du
        # samedi ne doit pas etre confondue avec une qualite recente bloquante.
        forecast = build_three_day_training_guidance(
            "2030-01-22",
            [long_run("2030-01-19")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Seuil 4 x 6' (allegee, decalee)")
        self.assertIn("decale", today["adjustment"])

    def test_recent_long_run_does_not_block_lighter_quality_day_reschedule(self):
        # Apercu J+1 : lundi a ete couru facile au lieu du seuil. La SL du
        # samedi ne doit pas empecher le seuil allege de passer au mardi.
        forecast = build_three_day_training_guidance(
            "2030-01-21",
            [long_run("2030-01-19"), easy_run("2030-01-21")],
        )

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Seuil 4 x 6' (allegee, decalee)")
        self.assertIn("decale", tomorrow["adjustment"])

    def test_alternate_hard_key_day_turns_next_day_into_recovery(self):
        # Si la seance cle n'est pas couverte mais qu'un effort dur/long a ete
        # fait a la place, on recupere au lieu d'empiler un rattrapage le lendemain.
        forecast = build_three_day_training_guidance(
            "2030-01-22",
            [long_run("2030-01-21")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Footing de recuperation")
        self.assertNotIn("Seuil 4 x 6'", today["title"])
        self.assertIn("effort reel etait deja chargeant", today["adjustment"])

    def test_alternate_quality_on_long_day_does_not_reschedule_long_next_day(self):
        # Samedi SL au plan, mais qualite courte courue. Dimanche doit rester en
        # regeneration, pas devenir une SL allegee juste apres une seance dure.
        forecast = build_three_day_training_guidance(
            "2030-01-20",
            [hard_run("2030-01-19")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(title_base(today["title"]), "Footing de recuperation")
        self.assertNotIn("Sortie longue marathon allegee", today["title"])
        self.assertIn("effort reel etait deja chargeant", today["adjustment"])

    def test_lighter_run_on_key_day_flags_key_as_not_covered(self):
        # Lundi 20 juil: Seuil 3 x 8' au plan, mais footing leger reellement couru.
        forecast = build_three_day_training_guidance(
            "2030-01-14",
            [easy_run("2030-01-14")],
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
        forecast = build_three_day_training_guidance("2030-01-14", [])

        tomorrow = forecast["sessions"][1]
        self.assertEqual(title_base(tomorrow["title"]), "Footing facile + lignes")
        self.assertEqual(tomorrow["adjustment"], "Rien a changer.")

    def test_run_matching_plan_keeps_plain_done_message(self):
        forecast = build_three_day_training_guidance(
            "2030-01-03",
            [easy_run("2030-01-03")],
        )

        today = forecast["sessions"][0]
        self.assertEqual(today["title"], "Footing deja fait")
        self.assertIn("deja couverte", today["session"]["main"])

    def test_race_day_is_the_target_race(self):
        forecast = build_three_day_training_guidance("2030-04-21", [])

        self.assertEqual(title_base(forecast["sessions"][0]["title"]), "Marathon")
        self.assertIn("a personnaliser", forecast["planDescription"])
        self.assertIn("depart prudent", forecast["sessions"][0]["session"]["main"])
        self.assertIn("allure cible configuree", forecast["sessions"][0]["session"]["main"])

    def test_steady_run_one_day_early_shifts_until_next_saturday_long_run(self):
        forecast = build_three_day_training_guidance(
            "2030-01-16",
            [steady_run("2030-01-16")],
        )

        self.assertEqual(forecast["sessions"][0]["status"], "done")
        self.assertIn("prevue demain", forecast["sessions"][0]["adjustment"])
        self.assertEqual(title_base(forecast["sessions"][1]["title"]), "Footing court")
        self.assertEqual(title_base(forecast["sessions"][2]["title"]), "Footing de recuperation")
        self.assertEqual(title_base(forecast["sessions"][3]["title"]), "SL 18 km avec AM")
        self.assertIn("reste a sa date", forecast["sessions"][2]["adjustment"])

    def test_garmin_workout_payload_only_for_long_and_structured_quality(self):
        self.assertIsNone(build_workout_export("2030-01-15"))
        self.assertIsNone(build_workout_export("2030-01-17"))

        forecast = build_three_day_training_guidance("2030-01-17", [])
        by_date = {session["date"]: session for session in forecast["sessions"]}
        self.assertFalse(by_date["2030-01-17"]["workoutEligible"])
        self.assertTrue(by_date["2030-01-19"]["workoutEligible"])
        self.assertTrue(by_date["2030-01-21"]["workoutEligible"])

        quality = build_workout_export("2030-01-14")
        long_run_export = build_workout_export("2030-01-19")
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
        self.assertEqual([step["stepName"] for step in workout_steps], ["Facile", "Allure marathon"])
        self.assertEqual([step["endConditionValue"] for step in workout_steps], [12000.0, 6000.0])
        self.assertEqual(workout_steps[1]["targetType"]["workoutTargetTypeKey"], "pace.zone")

    def test_garmin_workout_payload_keeps_multiple_repeat_blocks(self):
        export = build_workout_export("2030-02-25")
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
        self.assertEqual([step["numberOfIterations"] for step in repeats], [6, 4])
        self.assertEqual(repeats[0]["workoutSteps"][0]["endConditionValue"], 400.0)
        self.assertEqual(repeats[1]["workoutSteps"][0]["endConditionValue"], 200.0)
        self.assertGreater(
            repeats[1]["workoutSteps"][0]["targetValueOne"],
            repeats[0]["workoutSteps"][0]["targetValueOne"],
        )

    def test_garmin_workout_payload_distributes_repeated_am_long_run_blocks(self):
        export = build_workout_export("2030-03-23")
        workout = build_garmin_workout(
            export["structure"],
            title=export["title"],
            category=export["category"],
            est_minutes=export["estimatedMinutes"],
        )
        workout_steps = workout["workoutSegments"][0]["workoutSteps"]
        self.assertEqual([step["stepName"] for step in workout_steps].count("Allure marathon"), 3)
        self.assertEqual(sum(step["endConditionValue"] for step in workout_steps), 32000.0)


class KeySessionCompletionTests(unittest.TestCase):
    """Couverture d'une seance cle : relative au volume prevu, pas absolue."""

    @staticmethod
    def _run(km: float, pace: int, avg: int = 150, mx: int = 172) -> dict:
        return {
            "id": "r", "date": "2030-01-21",
            "distance_km": km, "distance_m": int(km * 1000),
            "moving_time": int(km * pace), "pace_sec_per_km": pace,
            "average_heartrate": avg, "max_heartrate": mx,
        }

    def test_rescheduled_key_session_actually_reduces_volume(self):
        # La seance decalee annonce "-25%" : le contenu ET l'estimation doivent
        # suivre, sinon le titre affiche le volume plein (incoherence).
        session = _schedule_for(date(2030, 1, 21), [])
        rescheduled = _reschedule_missed_key(session)

        self.assertIn("3 x 6'", rescheduled["main"])
        self.assertLess(
            _estimate_effort(rescheduled)[1],
            _estimate_effort(session)[1],
        )

    def test_slow_long_run_does_not_cover_marathon_pace_block(self):
        # Une SL de 20 km a 6:05/km n'est pas un travail a allure marathon.
        session = _schedule_for(date(2030, 2, 11), [])
        self.assertEqual(session["tag"], "marathon-pace")

        self.assertFalse(_planned_key_was_completed(session, self._run(20, 365)))
        self.assertTrue(_planned_key_was_completed(session, self._run(15, 330)))

    def test_taper_marathon_pace_recall_is_covered_when_actually_run(self):
        # "Rappel AM 3 x 1 km" : faire pile la seance doit compter (l'ancien
        # seuil absolu de 8 km ne la reconnaissait jamais).
        session = _schedule_for(date(2030, 4, 16), [])
        self.assertEqual(session["tag"], "marathon-pace")

        self.assertTrue(_planned_key_was_completed(session, self._run(3, 330)))
        self.assertFalse(_planned_key_was_completed(session, self._run(8, 370)))

    def test_short_fast_run_does_not_cover_full_threshold_session(self):
        # Seuil 4 x 6' (~47 min) : il faut au moins 70% du volume prevu.
        session = _schedule_for(date(2030, 1, 21), [])

        self.assertFalse(_planned_key_was_completed(session, self._run(3, 270)))
        self.assertFalse(_planned_key_was_completed(session, self._run(5, 280)))
        self.assertTrue(_planned_key_was_completed(session, self._run(12, 265)))

    def test_average_sleep_no_longer_flags_anything(self):
        # Seul un sommeil vraiment mauvais doit peser sur la seance.
        average = {"date": "2030-01-21", "sleep_score": 65, "sleep_duration_seconds": 24000}
        bad = {"date": "2030-01-21", "sleep_score": 50, "sleep_duration_seconds": 18000}

        self.assertFalse(_latest_sleep_flags(average, date(2030, 1, 21))["poor"])
        self.assertNotIn("cautious", _latest_sleep_flags(average, date(2030, 1, 21)))
        self.assertTrue(_latest_sleep_flags(bad, date(2030, 1, 21))["poor"])


if __name__ == "__main__":
    unittest.main()
