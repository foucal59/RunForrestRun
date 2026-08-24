"""Une sortie enregistree deux fois par Garmin ne doit faire qu'une ligne."""
import unittest

from garmin_freshness import (
    _dedupe_cross_training,
    _is_same_outing,
    _parse_existing_outings,
)


def _activity(aid, kind, start, distance, moving_time=3600, name="Sortie"):
    return {
        "id": aid,
        "type": kind,
        "name": name,
        "start_date_local": start,
        "distance": distance,
        "moving_time": moving_time,
    }


class DedupeCrossTrainingTests(unittest.TestCase):
    def test_real_august_pairs_collapse_to_the_richest_track(self):
        # Cas reels du 01-04/08/2026 : la montre enregistre "Randonnee",
        # le telephone "Marche a pied", 6 a 50 secondes plus tot.
        batch = [
            _activity(1, "Hike", "2026-08-04T09:29:44", 10533, 8199, "Randonnée en montagne"),
            _activity(2, "Walk", "2026-08-04T09:29:06", 10043, 10792, "Marche à pied en montagne"),
            _activity(3, "Walk", "2026-08-02T08:39:17", 4582, 5584, "Arrens Marche à pied"),
            _activity(4, "Hike", "2026-08-02T08:39:11", 8834, 5866, "Arrens Randonnée"),
            _activity(5, "Hike", "2026-08-01T12:33:39", 6253, 4995, "Arrens Randonnée"),
            _activity(6, "Walk", "2026-08-01T12:32:49", 6064, 7448, "Arrens Marche à pied"),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 4, 5])
        self.assertEqual(sorted(a["id"] for a in dropped), [2, 3, 6])

    def test_distance_gap_does_not_save_a_duplicate(self):
        # 4 582 m contre 8 834 m : 48 % d'ecart, bien au-dela des 2 % tolerés
        # pour une course. Seul le depart commun compte ici.
        batch = [
            _activity(1, "Hike", "2026-08-02T08:39:11", 8834),
            _activity(2, "Walk", "2026-08-02T08:39:17", 4582),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1])
        self.assertEqual([a["id"] for a in dropped], [2])

    def test_sessions_further_apart_are_two_real_activities(self):
        batch = [
            _activity(1, "Swim", "2025-08-18T10:00:00", 309),
            _activity(2, "Swim", "2025-08-18T16:30:00", 198),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2])
        self.assertEqual(dropped, [])

    def test_runs_are_never_touched_even_when_simultaneous(self):
        # Le dedoublon Strava/Garmin des courses reste _is_duplicate.
        batch = [
            _activity(1, "Run", "2026-08-06T18:00:00", 10000),
            _activity(2, "Run", "2026-08-06T18:00:30", 10010),
            _activity(3, "Hike", "2026-08-06T18:00:10", 4000),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2, 3])
        self.assertEqual(dropped, [])

    def test_different_sports_started_together_are_not_merged(self):
        batch = [
            _activity(1, "WeightTraining", "2026-08-06T18:49:30", 0, moving_time=600),
            _activity(2, "RockClimbing", "2026-08-06T18:49:35", 0, moving_time=1800),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2])
        self.assertEqual(dropped, [])

    def test_two_hikes_started_together_are_not_merged_without_a_walk_twin(self):
        batch = [
            _activity(1, "Hike", "2026-08-06T18:49:30", 5000),
            _activity(2, "Hike", "2026-08-06T18:49:35", 5100),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2])
        self.assertEqual(dropped, [])

    def test_hike_and_walk_more_than_two_minutes_apart_are_not_merged(self):
        batch = [
            _activity(1, "Hike", "2026-08-06T18:49:30", 5000),
            _activity(2, "Walk", "2026-08-06T18:51:31", 5100),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2])
        self.assertEqual(dropped, [])

    def test_unparsable_start_is_kept_rather_than_guessed(self):
        batch = [
            _activity(1, "Hike", "pas une date", 5000),
            _activity(2, "Hike", "aussi cassé", 5000),
        ]

        kept, dropped = _dedupe_cross_training(batch)

        self.assertEqual([a["id"] for a in kept], [1, 2])
        self.assertEqual(dropped, [])


class SameOutingAgainstDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.existing = _parse_existing_outings([
            {"start_date_local": "2026-08-02 08:39:11", "distance": 8834, "type": "Hike"},
        ])

    def test_second_recording_arriving_later_is_recognized(self):
        walk = _activity(9, "Walk", "2026-08-02T08:39:17", 4582)

        self.assertTrue(_is_same_outing(walk, self.existing))

    def test_activity_the_same_day_but_hours_apart_is_kept(self):
        evening = _activity(9, "Walk", "2026-08-02T18:39:17", 4582)

        self.assertFalse(_is_same_outing(evening, self.existing))

    def test_unrelated_sport_at_the_same_time_is_kept(self):
        strength = _activity(9, "WeightTraining", "2026-08-02T08:39:17", 0)

        self.assertFalse(_is_same_outing(strength, self.existing))

    def test_unparsable_start_never_claims_a_match(self):
        self.assertFalse(_is_same_outing(_activity(9, "Walk", "", 0), self.existing))


if __name__ == "__main__":
    unittest.main()
