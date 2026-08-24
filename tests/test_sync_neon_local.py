import unittest
from datetime import datetime, timedelta, timezone

from scripts.sync_neon_local import (
    _COLUMN_INFO_CACHE,
    _choose_child_source,
    _column_info,
    _insert_rows_in_batches,
    _newer_component_side,
    _newer_run_metrics_side,
)


class RunMetricSyncTests(unittest.TestCase):
    def test_column_info_is_cached_per_connection_and_table(self):
        class Cursor:
            def __init__(self, owner):
                self.owner = owner

            def execute(self, _sql, _params):
                self.owner.queries += 1

            def fetchall(self):
                return [("id", "bigint"), ("payload", "jsonb")]

        class Connection:
            def __init__(self):
                self.queries = 0

            def cursor(self):
                return Cursor(self)

        conn = Connection()
        _COLUMN_INFO_CACHE.clear()
        self.assertEqual(_column_info(conn, "runs"), [("id", "bigint"), ("payload", "jsonb")])
        self.assertEqual(_column_info(conn, "runs"), [("id", "bigint"), ("payload", "jsonb")])
        self.assertEqual(conn.queries, 1)

    def test_bulk_insert_uses_one_round_trip_per_chunk(self):
        class Cursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))

        cur = Cursor()
        rows = [(index, f"run-{index}") for index in range(5)]
        _insert_rows_in_batches(
            cur,
            "INSERT INTO runs (id, name)",
            "%s, %s",
            rows,
            chunk_size=2,
        )

        self.assertEqual(len(cur.calls), 3)
        self.assertEqual(cur.calls[0][1], [0, "run-0", 1, "run-1"])
        self.assertEqual(cur.calls[-1][1], [4, "run-4"])
        self.assertIn("VALUES (%s, %s), (%s, %s)", cur.calls[0][0])

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
