"""Le profil du coureur : d'ou viennent l'objectif, les allures et le cadrage.

C'est le module qui rend ce depot utilisable par quelqu'un d'autre que son
auteur. Ces tests figent les deux proprietes qui comptent :

  1. une SEULE valeur (l'objectif marathon) suffit a produire les huit
     fourchettes d'allure, avec les rapports d'un plan ecrit a la main ;
  2. l'ordre de priorite est respecte — ce que le coureur declare gagne toujours
     sur ce que Garmin observe, qui gagne sur le repli.
"""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import runner_profile
from runner_profile import (
    DISTANCES_KM,
    derive_paces,
    fmt_clock,
    goal_from_records,
    load_profile,
    riegel,
    write_observed_snapshot,
)


class PaceDerivationTests(unittest.TestCase):
    """Les allures se deduisent de l'objectif, elles ne se saisissent pas."""

    def setUp(self):
        # 3h15 sur marathon = 4:37/km : un calibrage rond, facile a relire.
        self.paces = derive_paces((3 * 3600 + 15 * 60) / DISTANCES_KM["marathon"])

    def test_every_training_zone_is_produced(self):
        self.assertEqual(
            set(self.paces),
            {"recovery", "easy", "steady", "marathon", "semi", "threshold", "vo2", "strides"},
        )

    def test_zones_are_ordered_from_slowest_to_fastest(self):
        order = ["recovery", "easy", "steady", "marathon", "semi", "threshold", "vo2", "strides"]
        lows = [self.paces[key][0] for key in order]
        self.assertEqual(lows, sorted(lows, reverse=True), f"zones desordonnees : {lows}")

    def test_every_zone_is_a_real_range(self):
        for key, (low, high) in self.paces.items():
            self.assertLess(low, high, key)

    def test_the_derived_paces_match_a_hand_written_plan(self):
        # Reference : les fourchettes qu'un entraineur pose pour un calibrage
        # 3h15. Tolerance de 5 s/km — au-dela, la formule ne decrit plus la
        # pratique et le plan genere deviendrait un plan theorique.
        expected = {
            "marathon": (277, 280),
            "threshold": (258, 268),
            "vo2": (235, 248),
            "easy": (322, 345),
            "recovery": (340, 365),
            "steady": (302, 320),
            "semi": (261, 271),
        }
        for key, (low, high) in expected.items():
            got_low, got_high = self.paces[key]
            self.assertAlmostEqual(got_low, low, delta=5, msg=f"{key} borne basse")
            self.assertAlmostEqual(got_high, high, delta=5, msg=f"{key} borne haute")

    def test_a_faster_goal_makes_every_zone_faster(self):
        faster = derive_paces((3 * 3600) / DISTANCES_KM["marathon"])
        for key in self.paces:
            self.assertLess(faster[key][0], self.paces[key][0], key)


class GoalProjectionTests(unittest.TestCase):
    def test_a_run_marathon_is_used_as_is(self):
        # Un marathon deja couru porte deja l'endurance specifique : aucune
        # marge de conversion a appliquer.
        self.assertEqual(goal_from_records({"marathon": 14164}), 14164)

    def test_a_ten_k_projects_a_marathon_with_a_conversion_margin(self):
        # 10 km en 45:00. Riegel brut donne environ 3h27 ; la marge de
        # conversion ramene l'objectif a un chrono tenable sur 42 km.
        raw = riegel(2700, 10.0, DISTANCES_KM["marathon"])
        projected = goal_from_records({"10k": 2700})

        self.assertGreater(projected, raw, "la projection brute est optimiste")
        self.assertEqual(fmt_clock(projected), "3h32")

    def test_the_longest_record_wins(self):
        # Le record le plus long est la projection la moins optimiste : un semi
        # doit primer sur un 5 km, meme si le 5 km projette plus vite.
        both = goal_from_records({"5k": 1231, "semi": 6000})
        self.assertEqual(both, goal_from_records({"semi": 6000}))

    def test_no_record_means_no_projection(self):
        self.assertIsNone(goal_from_records({}))


class ProfilePrecedenceTests(unittest.TestCase):
    """Ce que le coureur declare gagne sur ce que Garmin observe."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.configured = self.tmp / "runner_profile.json"
        self.observed = self.tmp / "observed.json"
        # Un environnement propre : sinon le profil du poste de dev fuiterait
        # dans les assertions.
        cleaned = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PLAN_", "RUNNER_"))
        }
        cleaned["RUNNER_PROFILE_FILE"] = str(self.configured)
        cleaned["RUNNER_OBSERVED_FILE"] = str(self.observed)
        patcher = mock.patch.dict(os.environ, cleaned, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_without_anything_the_profile_still_builds(self):
        profile = load_profile()

        self.assertEqual(profile.goal_source, "fallback")
        self.assertEqual(profile.max_hr_source, "fallback")
        # Un plan affichable meme sans configuration : la course se cale a
        # `plan_weeks` semaines, sinon la page Plan serait vide au premier lancement.
        self.assertGreater(profile.race_date, date.today())
        self.assertEqual(profile.pace("marathon"), profile.pace("marathon"))

    def test_the_observed_snapshot_calibrates_the_plan_without_any_input(self):
        # Le scenario reel : le coureur connecte Garmin, la synchronisation ecrit
        # ses records, et le plan est calibre au demarrage suivant.
        write_observed_snapshot({"10k": 2700}, 179, path=self.observed)

        profile = load_profile()

        self.assertEqual(profile.goal_source, "projected_from_records")
        self.assertEqual(profile.goal_label, "3h32")
        self.assertEqual((profile.max_hr, profile.max_hr_source), (179, "observed_90d"))

    def test_the_runner_choice_beats_the_observed_snapshot(self):
        write_observed_snapshot({"10k": 2700}, 179, path=self.observed)
        self.configured.write_text(
            json.dumps({"goalTime": "3:30:00", "maxHr": 190}), encoding="utf-8"
        )

        profile = load_profile()

        self.assertEqual((profile.goal_source, profile.goal_label), ("configured", "3h30"))
        self.assertEqual((profile.max_hr, profile.max_hr_source), (190, "configured"))
        # Le record observe reste lisible : il n'est pas efface par l'objectif.
        self.assertEqual(profile.records["10k"], 2700)

    def test_the_environment_beats_the_runner_file(self):
        self.configured.write_text(json.dumps({"goalTime": "3:30:00"}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"RUNNER_GOAL_TIME": "2:59:00"}):
            profile = load_profile()

        self.assertEqual(profile.goal_label, "2h59")

    def test_an_unreadable_profile_file_never_breaks_the_plan(self):
        self.configured.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

        profile = load_profile()

        self.assertEqual(profile.goal_source, "fallback")

    def test_the_plan_frame_comes_from_the_configuration(self):
        self.configured.write_text(
            json.dumps({
                "raceName": "Marathon d'ailleurs",
                "raceDate": "2027-04-11",
                "planWeeks": 12,
                "longRunWeekday": 6,
                "goalTime": "3:45:00",
            }),
            encoding="utf-8",
        )

        profile = load_profile()

        self.assertEqual(profile.race_name, "Marathon d'ailleurs")
        self.assertEqual(profile.race_date, date(2027, 4, 11))
        self.assertEqual(profile.plan_weeks, 12)
        self.assertEqual(profile.long_run_weekday, 6)
        # La course est en S12 : la S1 tombe 11 semaines avant sa semaine.
        self.assertEqual(profile.week_one_monday, date(2027, 1, 18))
        self.assertLess(profile.plan_start, profile.week_one_monday)

    def test_a_weekday_of_zero_is_honoured(self):
        # Piege classique : 0 (lundi) est falsy. Un `or` naif remplacerait le
        # choix du coureur par le defaut sans rien dire.
        self.configured.write_text(json.dumps({"longRunWeekday": 0}), encoding="utf-8")

        self.assertEqual(load_profile().long_run_weekday, 0)


class ObservedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "nested" / "observed.json"

    def test_nothing_observed_writes_nothing(self):
        self.assertFalse(write_observed_snapshot({}, None, path=self.path))
        self.assertFalse(self.path.exists())

    def test_unknown_distances_are_dropped(self):
        write_observed_snapshot({"10k": 2700, "42k": 9999, "semi": 0}, None, path=self.path)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload, {"records": {"10k": 2700}})


class ObservedProfileSyncTests(unittest.TestCase):
    """La lecture en base passe par les memes requetes que la page Records."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "observed.json"

    def _sync(self, bests, history):
        import runner_profile_sync

        fake_db = mock.Mock()
        fake_db.get_computed_bests_bulk.return_value = bests
        fake_db.get_recent_runs_for_plan.return_value = history
        with mock.patch.object(runner_profile_sync, "db", fake_db), mock.patch.dict(
            os.environ, {"RUNNER_OBSERVED_FILE": str(self.path)}
        ):
            written = runner_profile_sync.refresh_observed_profile(date(2026, 8, 24))
        return written, fake_db

    def test_the_fastest_effort_of_each_distance_is_kept(self):
        written, fake_db = self._sync(
            {
                "10k": [{"timeSeconds": 2700}, {"timeSeconds": 2900}],
                "semi": [{"timeSeconds": 5467}],
                "5k": [],
                "marathon": [],
            },
            [{"date": "2026-08-20", "max_heartrate": 179}],
        )

        self.assertTrue(written)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["records"], {"10k": 2700, "semi": 5467})
        self.assertEqual(payload["maxHr"], 179)
        # Une seule requete pour les quatre distances, comme la page Records.
        fake_db.get_computed_bests_bulk.assert_called_once_with(["5k", "10k", "semi", "marathon"])

    def test_a_heart_rate_outside_the_window_is_not_written(self):
        # Sans observation recente, ecrire le repli figerait une valeur
        # arbitraire dans le snapshot et masquerait l'absence de donnee.
        written, _ = self._sync(
            {"10k": [{"timeSeconds": 2700}], "5k": [], "semi": [], "marathon": []},
            [{"date": "2020-01-01", "max_heartrate": 179}],
        )

        self.assertTrue(written)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("maxHr", payload)

    def test_a_database_error_is_never_fatal(self):
        import runner_profile_sync

        fake_db = mock.Mock()
        fake_db.get_computed_bests_bulk.side_effect = RuntimeError("base injoignable")
        fake_db.get_recent_runs_for_plan.side_effect = RuntimeError("base injoignable")
        with mock.patch.object(runner_profile_sync, "db", fake_db), mock.patch.dict(
            os.environ, {"RUNNER_OBSERVED_FILE": str(self.path)}
        ):
            self.assertFalse(runner_profile_sync.refresh_observed_profile(date(2026, 8, 24)))
        self.assertFalse(self.path.exists())


class DurationParsingTests(unittest.TestCase):
    def test_common_shapes_are_understood(self):
        cases = {
            "3:15:00": 11700,
            "45:00": 2700,
            "45:00 (4:30/km)": 2700,   # copie-colle depuis la page Records
            11700: 11700,
            "": None,
            None: None,
            "pas un chrono": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(runner_profile._parse_duration(raw), expected)


if __name__ == "__main__":
    unittest.main()
