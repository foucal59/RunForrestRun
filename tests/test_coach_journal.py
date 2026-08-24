import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts.coach_journal import (
    PROFILE,
    analyse_run,
    detect_stream_repetitions,
    dump_source_info,
    easy_hr_reference,
    parse_plan_overrides,
    profile_for,
    rolling_run_volume,
    run_metrics,
    unescape_copy_value,
)


def build_stream(phases, sample_seconds=2):
    rows = []
    elapsed = 0
    distance = 0.0
    index = 0
    for duration, speed, heartrate in phases:
        phase_end = elapsed + duration
        while elapsed < phase_end:
            rows.append({
                "stream_index": str(index),
                "time_sec": str(elapsed),
                "distance": str(distance),
                "heartrate": str(heartrate),
            })
            elapsed += sample_seconds
            distance += speed * sample_seconds
            index += 1
    rows.append({
        "stream_index": str(index),
        "time_sec": str(elapsed),
        "distance": str(distance),
        "heartrate": str(phases[-1][2]),
    })
    return rows


class StreamRepetitionDetectionTests(unittest.TestCase):
    def test_detects_six_repetitions_without_manual_laps(self):
        phases = [(600, 3.2, 140)]
        for repetition in range(6):
            phases.append((100, 4.0 + repetition * 0.01, 165 + repetition))
            if repetition < 5:
                phases.append((90, 2.5, 145))
        phases.append((600, 3.2, 142))

        efforts = detect_stream_repetitions(build_stream(phases))

        self.assertEqual(len(efforts), 6)
        self.assertTrue(all(distance == 400 for distance, _, _ in efforts))
        self.assertEqual([heartrate for _, _, heartrate in efforts], [165, 166, 167, 168, 169, 170])

    def test_ignores_a_steady_run_with_gps_noise(self):
        phases = [(60, 3.15 + (index % 3) * 0.05, 140) for index in range(30)]

        self.assertEqual(detect_stream_repetitions(build_stream(phases)), [])

    def test_ignores_two_unrelated_accelerations(self):
        phases = [
            (600, 3.2, 140),
            (250, 4.0, 160),
            (120, 2.6, 145),
            (70, 4.8, 165),
            (600, 3.2, 142),
        ]

        self.assertEqual(detect_stream_repetitions(build_stream(phases)), [])

    def test_gps_repetitions_replace_incomplete_automatic_laps(self):
        phases = [(600, 3.2, 140)]
        for repetition in range(6):
            phases.append((100, 4.0, 165 + repetition))
            if repetition < 5:
                phases.append((90, 2.5, 145))
        phases.append((600, 3.2, 142))
        activity = {
            "id": "activity-1",
            "_dt": dt.datetime(2026, 7, 14, 8, 0),
            "distance": "10000",
            "moving_time": "3000",
            "average_heartrate": "148",
            "max_heartrate": "172",
            "name": "Course matinale",
        }
        automatic_laps = {
            "activity-1": [{
                "lap_index": "1",
                "average_speed": "3.4",
                "distance": "1000",
                "max_heartrate": "150",
            }],
        }

        run = analyse_run(activity, automatic_laps, {"activity-1": build_stream(phases)})

        self.assertEqual(run["kind"], "Qualite")
        self.assertEqual(run["fast_source"], "gps")
        self.assertEqual(len(run["fast"]), 6)

    def test_gps_repetitions_replace_same_count_of_kilometre_laps(self):
        phases = [(600, 3.2, 140)]
        for repetition in range(6):
            phases.append((100, 4.0, 165 + repetition))
            if repetition < 5:
                phases.append((90, 2.5, 145))
        phases.append((600, 3.2, 142))
        activity = {
            "id": "activity-2",
            "_dt": dt.datetime(2026, 7, 14, 8, 0),
            "distance": "10000",
            "moving_time": "3000",
            "average_heartrate": "148",
            "max_heartrate": "172",
            "name": "Course matinale",
        }
        automatic_laps = {
            "activity-2": [
                {
                    "lap_index": str(index),
                    "average_speed": "3.8",
                    "distance": "1000",
                    "max_heartrate": "165",
                }
                for index in range(1, 7)
            ],
        }

        run = analyse_run(activity, automatic_laps, {"activity-2": build_stream(phases)})

        self.assertEqual(run["fast_source"], "gps")
        self.assertEqual([distance for distance, _, _ in run["fast"]], [400] * 6)


class PlanOverrideDumpTests(unittest.TestCase):
    """Ajustements du coach relus depuis le dump SQL local."""

    def write_dump(self, rows: list[str]) -> str:
        body = "".join(f"{row}\n" for row in rows)
        dump = (
            "COPY public.plan_overrides (day, payload, note, source, updated_at) FROM stdin;\n"
            f"{body}"
            "\\.\n"
        )
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text(dump, encoding="utf-8")
        return str(path)

    def copy_escape(self, value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("\t", "\\t")
            .replace("\n", "\\n")
        )

    def test_payload_with_backslash_escapes_survives_the_dump(self):
        # json.dumps echappe les guillemets avec un backslash, que COPY redouble :
        # sans desechappement, json.loads echoue et l'ajustement est perdu.
        session = {"title": 'SL "test"', "main": "22 km a 5:20/km", "category": "long"}
        payload = self.copy_escape(json.dumps(session))
        dump = self.write_dump([f"2026-08-02\t{payload}\tNote coach\tcoach-mcp\t2026-07-29"])

        overrides = parse_plan_overrides(dump)

        self.assertEqual(list(overrides), ["2026-08-02"])
        self.assertEqual(overrides["2026-08-02"]["session"], session)
        self.assertEqual(overrides["2026-08-02"]["note"], "Note coach")

    def test_null_note_becomes_none(self):
        payload = self.copy_escape(json.dumps({"kind": "rest"}))
        dump = self.write_dump([f"2026-08-02\t{payload}\t\\N\tcoach-mcp\t2026-07-29"])

        self.assertIsNone(parse_plan_overrides(dump)["2026-08-02"]["note"])

    def test_unreadable_payload_is_skipped_not_fatal(self):
        dump = self.write_dump([
            "2026-08-02\tpas-du-json\t\\N\tcoach-mcp\t2026-07-29",
            f"2026-08-03\t{self.copy_escape(json.dumps({'kind': 'rest'}))}\t\\N\tcoach-mcp\t2026-07-29",
        ])

        self.assertEqual(list(parse_plan_overrides(dump)), ["2026-08-03"])

    def test_missing_table_yields_no_override(self):
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text("-- dump sans plan_overrides\n", encoding="utf-8")

        self.assertEqual(parse_plan_overrides(str(path)), {})

    def test_unescape_handles_tabs_and_newlines(self):
        self.assertEqual(unescape_copy_value("a\\tb\\nc\\\\d"), "a\tb\nc\\d")


class ProfileRecordsFromDumpTests(unittest.TestCase):
    """Records 5K/10K du profil coach, relus dans le dump plutot que codes en dur."""

    def write_dump(self, activities: list[str], efforts: list[str]) -> str:
        dump = (
            "COPY public.activities (id, type, start_date_local) FROM stdin;\n"
            + "".join(f"{row}\n" for row in activities)
            + "\\.\n"
            "COPY public.activity_best_efforts "
            "(activity_id, name, moving_time, elapsed_time, distance, elevation_delta) FROM stdin;\n"
            + "".join(f"{row}\n" for row in efforts)
            + "\\.\n"
        )
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text(dump, encoding="utf-8")
        return str(path)

    def test_a_record_read_in_the_dump_replaces_the_profile_value(self):
        # Le profil du coureur ne fournit qu'un repli (souvent vide au premier
        # demarrage) : c'est le dump qui fait autorite sur les records, sinon le
        # coach calibrerait indefiniment sur un chrono perime.
        dump = self.write_dump(
            ["23405318914\tRun\t2026-06-28 11:07:00"],
            ["23405318914\t10K\t2450\t2450\t10000\t-1.2"],
        )

        profile = profile_for(dump)
        self.assertEqual(profile["pr_10k"], "40:50 (4:05/km)")
        self.assertEqual(profile["pr_5k"], PROFILE["pr_5k"])  # rien de plus rapide au dump
        self.assertEqual(profile["pr_marathon"], PROFILE["pr_marathon"])  # jamais recalcule

    def test_a_downhill_effort_is_refused_exactly_like_the_records_page(self):
        # MAX_NET_DROP_PER_KM = 5 m/km : un 10 km a -200 m est ecarte par
        # get_computed_bests_bulk, il doit l'etre ici aussi — sinon le coach
        # annoncerait un record que la page Records refuse d'afficher.
        dump = self.write_dump(
            ["1\tRun\t2026-08-06 10:00:00"],
            ["1\t10K\t2200\t2200\t10000\t-200.0"],
        )

        self.assertEqual(profile_for(dump)["pr_10k"], PROFILE["pr_10k"])

    def test_a_hike_never_becomes_a_running_record(self):
        # Les deux bases miroitent Garmin (randos comprises) : sans le filtre
        # type='Run', une descente de rando entrerait dans les records du coach.
        dump = self.write_dump(
            ["2\tHike\t2026-08-07 10:00:00"],
            ["2\t10K\t2000\t2000\t10000\t\\N"],
        )

        self.assertEqual(profile_for(dump)["pr_10k"], PROFILE["pr_10k"])

    def test_an_unknown_elevation_still_counts(self):
        # elevation_delta NULL = inconnu (ligne historique sans stream d'altitude) :
        # la ligne passe, comme en SQL.
        dump = self.write_dump(
            ["3\tRun\t2025-05-01 10:00:00"],
            ["3\t5K\t1180\t1180\t5000\t\\N"],
        )

        self.assertEqual(profile_for(dump)["pr_5k"], "19:40 (3:56/km)")

    def test_a_dump_without_records_keeps_the_fallback_profile(self):
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text("-- dump sans best efforts\n", encoding="utf-8")

        profile = profile_for(str(path), dt.date(2026, 8, 17))

        self.assertEqual({key: profile[key] for key in PROFILE}, PROFILE)
        self.assertEqual(profile["fc_max_reference"]["source"], "fallback")
        self.assertEqual(profile["fc_facile_reference"]["fallbackReason"], "insufficient_samples")


class DynamicHeartRateProfileTests(unittest.TestCase):
    def write_activity_dump(self, rows: list[str], lap_rows: list[str] | None = None) -> str:
        dump = (
            "COPY public.activities "
            "(id, type, start_date_local, distance, moving_time, average_heartrate, max_heartrate, name) "
            "FROM stdin;\n"
            + "".join(f"{row}\n" for row in rows)
            + "\\.\n"
            "COPY public.activity_best_efforts "
            "(activity_id, name, moving_time, elapsed_time, distance, elevation_delta) FROM stdin;\n"
            "\\.\n"
            "COPY public.activity_laps "
            "(activity_id, lap_index, moving_time, elapsed_time, distance, average_heartrate, max_heartrate) "
            "FROM stdin;\n"
            + "".join(f"{row}\n" for row in (lap_rows or []))
            + "\\.\n"
        )
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text(dump, encoding="utf-8")
        return str(path)

    def test_profile_exposes_current_max_hr_and_a_robust_easy_median(self):
        dump = self.write_activity_dump([
            # Pointe FC encore dans les 90 jours, mais hors fenetre easy de 42 jours.
            "1\tRun\t2026-06-28 11:07:00\t10000\t3000\t160\t179",
            # Trois footings qualifiants : mediane 135 bpm.
            "2\tRun\t2026-07-10 08:00:00\t10000\t3300\t134\t155",
            "3\tRun\t2026-07-25 08:00:00\t9000\t2970\t136\t158",
            "4\tRun\t2026-08-10 08:00:00\t8000\t2640\t135\t154",
            # Qualite dont la moyenne tombe dans la bande easy et dont le nom
            # Garmin est generique : seule la structure des laps la demasque.
            "8\tRun\t2026-08-09 08:00:00\t8000\t2640\t149\t170\tDinard Course a pied",
            # Trop court, trop rapide, et mauvais sport : aucun ne compte.
            "5\tRun\t2026-08-11 08:00:00\t4000\t1320\t120\t145",
            "6\tRun\t2026-08-12 08:00:00\t10000\t3000\t170\t176",
            "7\tHike\t2026-08-13 08:00:00\t10000\t3300\t150\t190",
        ], [
            "8\t1\t300\t300\t1200\t156\t165",
            "8\t2\t180\t180\t400\t135\t145",
            "8\t3\t300\t300\t1200\t157\t166",
            "8\t4\t180\t180\t400\t136\t146",
            "8\t5\t300\t300\t1200\t158\t167",
            "8\t6\t180\t180\t400\t137\t147",
        ])

        profile = profile_for(dump, dt.date(2026, 8, 17))

        self.assertEqual(profile["fc_max"], 179)
        self.assertEqual(profile["fc_max_reference"]["observedOn"], "2026-06-28")
        self.assertEqual(profile["fc_max_reference"]["source"], "observed_90d")
        self.assertEqual(profile["fc_facile"], 135)
        self.assertEqual(profile["fc_facile_reference"]["sampleCount"], 3)
        self.assertEqual(profile["fc_facile_reference"]["excludedQualityCount"], 1)
        self.assertEqual(
            profile["fc_facile_reference"]["qualityDetection"],
            "laps_then_name_and_average_hr",
        )
        self.assertEqual(profile["fc_facile_reference"]["observedMedian"], 135)
        self.assertEqual(profile["fc_facile_reference"]["source"], "observed_median_42d")

    def test_easy_baseline_falls_back_explicitly_when_the_sample_is_too_small(self):
        rows = [
            {"type": "Run", "date": "2026-08-01", "distance_m": 10000,
             "moving_time": 3300, "average_heartrate": 134},
            {"type": "Run", "date": "2026-08-10", "distance_m": 8000,
             "moving_time": 2640, "average_heartrate": 136},
        ]

        reference = easy_hr_reference(rows, dt.date(2026, 8, 17))

        self.assertEqual(reference["value"], PROFILE["fc_facile"])
        self.assertEqual(reference["source"], "fallback")
        self.assertEqual(reference["fallbackReason"], "insufficient_samples")
        self.assertEqual(reference["sampleCount"], 2)
        self.assertEqual(reference["observedMedian"], 135)

class DumpSourceInfoTests(unittest.TestCase):
    def test_snapshot_provenance_distinguishes_generation_date_from_dump_mtime(self):
        path = Path(tempfile.mkdtemp()) / "dump.sql"
        path.write_text("-- fixture\n", encoding="utf-8")

        info = dump_source_info(str(path))

        self.assertTrue(info["exists"])
        self.assertEqual(info["path"], str(path))
        self.assertIsNotNone(info["modified_at"])
        self.assertGreaterEqual(info["age_seconds"], 0)

    def test_missing_dump_has_explicit_provenance(self):
        path = Path(tempfile.mkdtemp()) / "absent.sql"

        self.assertEqual(dump_source_info(str(path)), {
            "path": str(path),
            "exists": False,
            "modified_at": None,
            "age_seconds": None,
        })


class RollingVolumeTests(unittest.TestCase):
    def test_seven_day_volume_counts_today_and_six_previous_dates(self):
        today = dt.date(2026, 8, 17)
        runs = [
            {"dt": dt.datetime(2026, 8, 17, 8), "dist": 12.49},
            {"dt": dt.datetime(2026, 8, 11, 8), "dist": 10.07},
            # J-7 etait inclus par l'ancienne condition <= 7 : huit dates.
            {"dt": dt.datetime(2026, 8, 10, 8), "dist": 10.20},
            # Une donnee future ne doit jamais gonfler le volume courant.
            {"dt": dt.datetime(2026, 8, 18, 8), "dist": 8.00},
        ]

        self.assertAlmostEqual(rolling_run_volume(runs, today), 22.56)


class RunMetricsTests(unittest.TestCase):
    def test_cadence_is_doubled_from_per_leg_value(self):
        # Garmin/Strava stocke la cadence par jambe : 84.56 -> 169 spm.
        m = run_metrics({"average_cadence": "84.5625", "max_cadence": "91.5"})
        self.assertEqual(m["cadence_spm"], 169)
        self.assertEqual(m["cadence_max_spm"], 183)

    def test_stride_converted_to_metres_and_effect_rounded(self):
        m = run_metrics({
            "avg_stride_length": "116.84",
            "aerobic_training_effect": "3.299999952316284",
        })
        self.assertEqual(m["longueur_foulee_m"], 1.17)
        self.assertEqual(m["effet_aerobie"], 3.3)

    def test_hr_zones_parsed_into_seconds_per_zone(self):
        raw = ('[{"zone": 1, "seconds": 16.0}, {"zone": 3, "seconds": 3476.1}]')
        m = run_metrics({"hr_time_in_zones": raw})
        self.assertEqual(m["fc_temps_par_zone_s"], {1: 16, 3: 3476})

    def test_missing_fields_are_none_not_a_crash(self):
        m = run_metrics({})
        self.assertIsNone(m["cadence_spm"])
        self.assertIsNone(m["longueur_foulee_m"])
        self.assertIsNone(m["fc_temps_par_zone_s"])
        self.assertIsNone(m["sante"]["sommeil_score"])
        self.assertIsNone(m["meteo"]["temperature_c"])

    def test_postgres_null_marker_is_treated_as_absent(self):
        m = run_metrics({"training_effect_label": "\\N", "health_hrv_status": "\\N"})
        self.assertIsNone(m["label_effet"])
        self.assertIsNone(m["sante"]["vfc_statut"])


if __name__ == "__main__":
    unittest.main()
