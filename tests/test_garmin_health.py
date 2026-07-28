import unittest

from garmin_health import build_run_health_snapshot


class GarminHealthTests(unittest.TestCase):
    def test_builds_health_snapshot_at_run_end(self):
        run = {
            "id": 42,
            "start_date_local": "2026-07-20T18:00:00",
            "elapsed_time": 3600,
            "moving_time": 3500,
        }
        sleep = {
            "dailySleepDTO": {
                "calendarDate": "2026-07-20",
                "sleepTimeSeconds": 22980,
                "sleepStartTimestampLocal": "2026-07-20T01:28:28.0",
                "sleepEndTimestampLocal": "2026-07-20T07:58:28.0",
                "sleepScores": {
                    "overall": {"value": 82, "qualifierKey": "GOOD"}
                },
            }
        }
        hrv = {
            "hrvSummary": {
                "calendarDate": "2026-07-20",
                "lastNightAvg": 79,
                "weeklyAvg": 74,
                "status": "BALANCED",
                "baseline": {"balancedLow": 62, "balancedUpper": 89},
            },
            "endTimestampLocal": "2026-07-20T07:58:08.0",
        }
        heart_rates = {
            "calendarDate": "2026-07-20",
            "restingHeartRate": 43,
            "lastSevenDaysAvgRestingHeartRate": 48,
        }

        snapshot = build_run_health_snapshot(
            run, [sleep], [hrv], [heart_rates], []
        )

        self.assertEqual(snapshot["health_snapshot_at"], "2026-07-20T19:00:00")
        self.assertEqual(snapshot["health_sleep_score"], 82)
        self.assertEqual(snapshot["health_sleep_quality"], "GOOD")
        self.assertEqual(snapshot["health_hrv_last_night_avg_ms"], 79.0)
        self.assertEqual(snapshot["health_hrv_weekly_avg_ms"], 74.0)
        self.assertEqual(snapshot["health_hrv_status"], "BALANCED")
        self.assertEqual(snapshot["health_hrv_baseline_low_ms"], 62.0)
        self.assertEqual(snapshot["health_resting_hr_bpm"], 43)
        self.assertEqual(snapshot["health_resting_hr_7d_avg_bpm"], 48.0)

    def test_ignores_sleep_and_hrv_that_end_after_the_run(self):
        run = {
            "id": 43,
            "start_date_local": "2026-07-20T05:30:00",
            "elapsed_time": 1800,
        }
        future_sleep = {
            "dailySleepDTO": {
                "calendarDate": "2026-07-20",
                "sleepTimeSeconds": 24000,
                "sleepEndTimestampLocal": "2026-07-20T07:30:00",
                "sleepScores": {
                    "overall": {"value": 90, "qualifierKey": "EXCELLENT"}
                },
            }
        }
        previous_sleep = {
            "dailySleepDTO": {
                "calendarDate": "2026-07-19",
                "sleepTimeSeconds": 21000,
                "sleepEndTimestampLocal": "2026-07-19T07:20:00",
                "sleepScores": {"overall": {"value": 70}},
            }
        }
        future_hrv = {
            "hrvSummary": {"calendarDate": "2026-07-20", "lastNightAvg": 88},
            "endTimestampLocal": "2026-07-20T07:30:00",
        }
        previous_hrv = {
            "hrvSummary": {"calendarDate": "2026-07-19", "lastNightAvg": 66},
            "endTimestampLocal": "2026-07-19T07:20:00",
        }

        snapshot = build_run_health_snapshot(
            run,
            [future_sleep, previous_sleep],
            [future_hrv, previous_hrv],
            [],
            [],
        )

        self.assertEqual(snapshot["health_sleep_date"], "2026-07-19")
        self.assertEqual(snapshot["health_sleep_score"], 70)
        self.assertEqual(snapshot["health_hrv_date"], "2026-07-19")
        self.assertEqual(snapshot["health_hrv_last_night_avg_ms"], 66.0)

    def test_extracts_resting_hr_from_rhr_metric_payload(self):
        run = {
            "id": 44,
            "start_date_local": "2026-07-20T09:00:00",
            "elapsed_time": 1200,
        }
        rhr = {
            "allMetrics": {
                "metricsMap": {
                    "WELLNESS_RESTING_HEART_RATE": [
                        {"calendarDate": "2026-07-20", "value": 41.0}
                    ]
                }
            }
        }

        snapshot = build_run_health_snapshot(run, [], [], [{}], [rhr])

        self.assertEqual(snapshot["health_resting_hr_bpm"], 41)
        self.assertEqual(snapshot["health_resting_hr_date"], "2026-07-20")


if __name__ == "__main__":
    unittest.main()
