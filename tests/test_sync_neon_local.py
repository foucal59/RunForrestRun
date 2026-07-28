import unittest
from datetime import datetime, timedelta, timezone

from scripts.sync_neon_local import (
    _choose_child_source,
    _newer_component_side,
    _newer_run_metrics_side,
)


class RunMetricSyncTests(unittest.TestCase):
    def test_equal_instants_with_different_offsets_do_not_trigger_copy(self):
        utc_value = datetime(2026, 7, 14, 21, 53, tzinfo=timezone.utc)
        paris_value = datetime(
            2026,
            7,
            14,
            23,
            53,
            tzinfo=timezone(timedelta(hours=2)),
        )

        self.assertIsNone(_newer_run_metrics_side(
            {"run_metrics_updated_at": utc_value},
            {"run_metrics_updated_at": paris_value},
        ))

    def test_newer_marker_selects_source_side(self):
        older = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
        newer = datetime(2026, 7, 14, 20, 1, tzinfo=timezone.utc)

        self.assertEqual(
            _newer_run_metrics_side(
                {"run_metrics_updated_at": older},
                {"run_metrics_updated_at": newer},
            ),
            "local",
        )

    def test_component_markers_do_not_replace_unrelated_tables(self):
        older = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
        newer = older + timedelta(minutes=1)

        self.assertEqual(
            _newer_component_side(
                {"run_laps_updated_at": older},
                {"run_laps_updated_at": newer},
                "run_laps_updated_at",
            ),
            "local",
        )
        self.assertIsNone(
            _newer_component_side(
                {"run_streams_updated_at": older},
                {"run_streams_updated_at": older},
                "run_streams_updated_at",
            )
        )

    def test_newer_component_marker_beats_row_count(self):
        older = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
        newer = older + timedelta(minutes=1)

        self.assertEqual(
            _choose_child_source(
                {"count": 10, "hash": "neon"},
                {"count": 8, "hash": "local"},
                older,
                newer,
            ),
            "local",
        )

    def test_equal_count_divergence_uses_neon(self):
        self.assertEqual(
            _choose_child_source(
                {"count": 10, "hash": "neon-content"},
                {"count": 10, "hash": "local-content"},
            ),
            "neon",
        )

    def test_legacy_fallback_uses_richer_count_and_equal_content_is_stable(self):
        self.assertEqual(
            _choose_child_source(
                {"count": 8, "hash": "neon"},
                {"count": 10, "hash": "local"},
            ),
            "local",
        )
        self.assertIsNone(
            _choose_child_source(
                {"count": 10, "hash": "same"},
                {"count": 10, "hash": "same"},
            )
        )


if __name__ == "__main__":
    unittest.main()
