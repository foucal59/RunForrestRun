import unittest

from database_pg import _iso_notz
from garmin_freshness import (
    _build_streams,
    _extract_run,
    _laps_from_garmin,
    _normalize_zone_payload,
)


class GarminRunMetricTests(unittest.TestCase):
    def test_sqlite_text_timestamp_is_normalized(self):
        self.assertEqual(_iso_notz("2026-07-14 08:00:00"), "2026-07-14T08:00:00")

    def test_summary_keeps_run_metrics_and_excludes_profile_and_sleep(self):
        run = _extract_run({
            "activityId": 42,
            "activityType": {"typeKey": "running"},
            "activityName": "Intervals",
            "startTimeLocal": "2026-07-14 08:00:00",
            "startTimeGMT": "2026-07-14 06:00:00",
            "distance": 10000,
            "duration": 3000,
            "averageRunningCadenceInStepsPerMinute": 168,
            "maxRunningCadenceInStepsPerMinute": 190,
            "startLatitude": 50.63,
            "startLongitude": 3.06,
            "endLatitude": 50.64,
            "endLongitude": 3.07,
            "activityTrainingLoad": 121.5,
            "aerobicTrainingEffect": 3.8,
            "anaerobicTrainingEffect": 2.4,
            "vO2MaxValue": 57.0,
            "avgPower": 401.0,
            "normPower": 422.0,
            "maxPower": 615.0,
            "avgGroundContactTime": 232.0,
            "avgStrideLength": 1.25,
            "avgVerticalOscillation": 8.1,
            "avgVerticalRatio": 6.5,
            "differenceBodyBattery": -8,
            "hrTimeInZone_4": 900.0,
            "powerTimeInZone_3": 600.0,
            "fastestSplit_5000": 1180.0,
            "ownerFullName": "Do not persist",
            "ownerProfileImageUrlLarge": "https://example.invalid/profile.jpg",
            "sleepScore": 90,
        })

        self.assertEqual(run["average_cadence"], 84)
        self.assertEqual(run["max_cadence"], 95)
        self.assertEqual(run["activity_training_load"], 121.5)
        self.assertEqual(run["avg_ground_contact_time"], 232.0)
        self.assertEqual(run["hr_time_in_zones"][0]["zone"], 4)
        self.assertEqual(run["power_time_in_zones"][0]["seconds"], 600.0)
        self.assertEqual(run["garmin_fastest_splits"], {"5000": 1180.0})
        self.assertNotIn("ownerFullName", run["garmin_summary"])
        self.assertNotIn("ownerProfileImageUrlLarge", run["garmin_summary"])
        self.assertNotIn("sleepScore", run["garmin_summary"])

    def test_zone_endpoint_keeps_duration_and_boundary(self):
        rows = _normalize_zone_payload([
            {"zoneNumber": 4, "secsInZone": 743.921, "zoneLowBoundary": 142},
        ])

        self.assertEqual(rows, [{
            "zone": 4,
            "seconds": 743.921,
            "low_boundary": 142,
            "source": "garmin_zone_endpoint",
        }])

    def test_laps_keep_power_dynamics_coordinates_and_raw_payload(self):
        raw = {
            "lapIndex": 1,
            "distance": 1000,
            "duration": 250,
            "averageRunCadence": 170,
            "maxRunCadence": 190,
            "averagePower": 405,
            "normalizedPower": 420,
            "groundContactTime": 225,
            "strideLength": 1.31,
            "verticalOscillation": 7.9,
            "verticalRatio": 6.1,
            "startLatitude": 50.6,
            "startLongitude": 3.0,
        }

        lap = _laps_from_garmin(42, [raw])[0]

        self.assertEqual(lap["average_cadence"], 85)
        self.assertEqual(lap["max_cadence"], 95)
        self.assertEqual(lap["average_watts"], 405)
        self.assertEqual(lap["ground_contact_time"], 225)
        self.assertEqual(lap["garmin_data"], raw)

    def test_streams_keep_running_dynamics_and_unknown_future_metrics(self):
        class FakeApi:
            def get_activity_details(self, _activity_id, _max_chart, _max_poly):
                return {
                    "metricDescriptors": [
                        {"key": "sumDuration", "metricsIndex": 0},
                        {"key": "sumDistance", "metricsIndex": 1},
                        {"key": "directHeartRate", "metricsIndex": 2},
                        {"key": "directPower", "metricsIndex": 3},
                        {"key": "directGroundContactTime", "metricsIndex": 4},
                        {"key": "directVerticalOscillation", "metricsIndex": 5},
                        {"key": "futureRunMetric", "metricsIndex": 6},
                    ],
                    "activityDetailMetrics": [
                        {"metrics": [0, 0, 140, 390, 235, 8.2, 12.5]},
                        {"metrics": [1, 4, 142, 410, 229, 8.0, 13.0]},
                    ],
                }

        streams = _build_streams(FakeApi(), 42)

        self.assertEqual(streams["watts"]["data"], [390, 410])
        self.assertEqual(streams["ground_contact_time"]["data"], [235, 229])
        self.assertEqual(streams["vertical_oscillation"]["data"], [8.2, 8.0])
        self.assertEqual(
            streams["garmin_metrics"]["data"],
            [{"futureRunMetric": 12.5}, {"futureRunMetric": 13.0}],
        )


if __name__ == "__main__":
    unittest.main()
