import unittest
from datetime import datetime, timedelta, timezone

from garmin_freshness import _filter_tombstoned_runs
from scripts.sync_neon_local import _plan_small_table_actions


class SmallTablePlanTests(unittest.TestCase):
    def test_missing_rows_are_copied_both_directions(self):
        actions = _plan_small_table_actions(
            {"2026-07-14": datetime(2026, 7, 14, 8, tzinfo=timezone.utc)},
            {"2026-07-13": datetime(2026, 7, 13, 8, tzinfo=timezone.utc)},
        )

        self.assertEqual(
            actions,
            [
                ("local", "neon", "2026-07-13"),
                ("neon", "local", "2026-07-14"),
            ],
        )

    def test_newest_updated_at_wins(self):
        older = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
        newer = older + timedelta(minutes=1)

        self.assertEqual(
            _plan_small_table_actions({"tokens": older}, {"tokens": newer}),
            [("local", "neon", "tokens")],
        )
        self.assertEqual(
            _plan_small_table_actions({"tokens": newer}, {"tokens": older}),
            [("neon", "local", "tokens")],
        )

    def test_equal_instants_and_missing_timestamps_do_not_overwrite(self):
        utc_value = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
        paris_value = datetime(
            2026,
            7,
            14,
            10,
            tzinfo=timezone(timedelta(hours=2)),
        )

        self.assertEqual(
            _plan_small_table_actions({"tokens": utc_value}, {"tokens": paris_value}),
            [],
        )
        self.assertEqual(
            _plan_small_table_actions({"tokens": None}, {"tokens": None}),
            [],
        )

    def test_absent_table_is_treated_as_an_empty_manifest(self):
        self.assertEqual(
            _plan_small_table_actions({"shoe-1": None}, {}),
            [("neon", "local", "shoe-1")],
        )


class TombstoneFilterTests(unittest.TestCase):
    def test_filters_canonical_and_garmin_ids(self):
        runs = [
            {"id": 1, "garmin_activity_id": 101},
            {"id": 2, "garmin_activity_id": 202},
            {"id": 3, "garmin_activity_id": 303},
        ]

        self.assertEqual(
            _filter_tombstoned_runs(runs, {"2", "303"}),
            [{"id": 1, "garmin_activity_id": 101}],
        )


if __name__ == "__main__":
    unittest.main()
