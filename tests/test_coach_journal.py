import datetime as dt
import unittest

from scripts.coach_journal import analyse_run, detect_stream_repetitions


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


if __name__ == "__main__":
    unittest.main()
