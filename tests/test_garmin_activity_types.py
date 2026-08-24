"""La base miroite Garmin ; seul `type = 'Run'` alimente le site et les records."""
import unittest

from garmin_freshness import (
    RUNNING_TYPE_KEYS,
    _extract_activity,
    _extract_run,
    activity_category,
)


def _raw(type_key: str, **overrides):
    payload = {
        "activityId": 1234,
        "activityType": {"typeKey": type_key},
        "activityName": "Sortie",
        "startTimeLocal": "2026-08-02 09:00:00",
        "distance": 12000,
        "duration": 7200,
        "elevationGain": 640,
    }
    payload.update(overrides)
    return payload


class ActivityCategoryTests(unittest.TestCase):
    def test_every_running_key_stays_a_run(self):
        for key in RUNNING_TYPE_KEYS:
            self.assertEqual(activity_category(key), "Run", key)

    def test_known_garmin_types_map_to_their_sport(self):
        cases = {
            "hiking": "Hike",
            "walking": "Walk",
            "cycling": "Ride",
            "indoor_cycling": "Ride",
            "e_bike_fitness": "Ride",
            "lap_swimming": "Swim",
            "open_water_swimming": "Swim",
            "resort_skiing": "Ski",
            "resort_skiing_snowboarding_ws": "Ski",
            "cross_country_skiing_ws": "Ski",
            "rowing_v2": "Rowing",
            "stand_up_paddleboarding_v2": "Rowing",
            "bouldering": "RockClimbing",
            "strength_training": "WeightTraining",
            "hiit": "Workout",
            "indoor_cardio": "Workout",
            "breathwork": "Workout",
        }
        for key, expected in cases.items():
            self.assertEqual(activity_category(key), expected, key)

    def test_unknown_type_falls_back_to_other_never_to_run(self):
        for key in ("rugby", "stop_watch", "incident_detected", "", "sport_du_futur"):
            self.assertEqual(activity_category(key), "Other", key)


class ExtractActivityTests(unittest.TestCase):
    def test_hike_is_kept_and_tagged_outside_running(self):
        activity = _extract_activity(_raw("hiking", activityName="Randonnee en montagne"))

        self.assertIsNotNone(activity)
        self.assertEqual(activity["type"], "Hike")
        self.assertEqual(activity["sport_type"], "Hike")
        self.assertEqual(activity["garmin_type_key"], "hiking")
        self.assertEqual(activity["total_elevation_gain"], 640)

    def test_trail_running_stays_a_run_so_the_site_keeps_showing_it(self):
        activity = _extract_activity(_raw("trail_running"))

        self.assertEqual(activity["type"], "Run")
        self.assertEqual(activity["sport_type"], "Run")
        self.assertEqual(activity["garmin_type_key"], "trail_running")

    def test_payload_without_activity_id_is_dropped(self):
        raw = _raw("running")
        del raw["activityId"]

        self.assertIsNone(_extract_activity(raw))

    def test_extract_run_still_filters_out_everything_but_running(self):
        self.assertIsNone(_extract_run(_raw("hiking")))
        self.assertIsNone(_extract_run(_raw("cycling")))
        self.assertIsNotNone(_extract_run(_raw("trail_running")))


if __name__ == "__main__":
    unittest.main()
