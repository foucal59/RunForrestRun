import unittest

import db
from database_pg import compute_gear_assignments
from garmin_freshness import _standalone_ungeared_runs


class GearReconciliationTests(unittest.TestCase):
    def test_public_canonical_map_helper_is_exported_by_db_shim(self):
        self.assertTrue(hasattr(db, "get_canonical_gear_map"))

    def test_assigns_active_shoe_but_skips_geared_twin(self):
        rows = [
            {
                "id": 1,
                "gear_id": "road-shoe",
                "sport_type": "Run",
                "sdl": "2026-06-10 08:00:00",
            },
            {
                "id": 2,
                "gear_id": None,
                "sport_type": "Run",
                "sdl": "2026-06-11 08:00:00",
            },
            {
                "id": 3,
                "gear_id": None,
                "sport_type": "Run",
                "sdl": "2026-06-10 08:00:00",
            },
            {
                "id": 4,
                "gear_id": None,
                "sport_type": "TrailRun",
                "sdl": "2026-06-11 09:00:00",
            },
        ]

        self.assertEqual(
            compute_gear_assignments(rows, since="2026-06-01"),
            {2: "road-shoe"},
        )
        self.assertEqual(
            [row["id"] for row in _standalone_ungeared_runs(rows, "2026-06-01")],
            [2, 4],
        )


if __name__ == "__main__":
    unittest.main()
