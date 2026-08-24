import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SQLiteIntegrationTests(unittest.TestCase):
    def _run(self, db_path: Path, source: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update({
            "SQLITE_PATH": str(db_path),
            "DATABASE_URL": "",
            "DATABASE_URL_NEON": "",
            "LOCAL_DATABASE_URL": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            self.fail(
                f"SQLite subprocess failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_rich_run_round_trip_markers_meta_and_tombstone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "round-trip.db"
            self._run(db_path, '''
                import db_sqlite as db

                db.init_db()
                raw = db._safe_conn()._raw
                raw.execute(
                    "INSERT INTO athletes (id, firstname, lastname) VALUES (?, ?, ?)",
                    (42, "Ada", "Runner"),
                )
                raw.commit()
                db._pg._get_sole_athlete_id._cached = None

                activity = {
                    "id": 9001,
                    "athlete_id": 42,
                    "name": "Run riche Garmin",
                    "start_date_local": "2026-07-15T07:00:00",
                    "start_date_gmt": "2026-07-15T05:00:00Z",
                    "distance": 10100.5,
                    "moving_time": 3000,
                    "elapsed_time": 3050,
                    "total_elevation_gain": 85,
                    "average_speed": 3.36,
                    "max_speed": 5.1,
                    "average_heartrate": 151,
                    "max_heartrate": 178,
                    "average_cadence": 172,
                    "source": "garmin",
                    "garmin_activity_id": 88009001,
                    "garmin_activity_uuid": "run-uuid-9001",
                    "device_name": "Forerunner Test",
                    "vo2max": 55.5,
                    "aerobic_training_effect": 3.7,
                    "avg_stride_length": 1.12,
                    "hr_time_in_zones": [{"zone": 4, "seconds": 600}],
                    "power_time_in_zones": [{"zone": 3, "seconds": 900}],
                    "garmin_fastest_splits": [{"distance": 1000, "time": 270}],
                    "garmin_summary": {"activityId": 88009001, "steps": 10400},
                }
                db.upsert_activities([activity])
                db.upsert_activity_details(
                    9001,
                    [{
                        "split": 1,
                        "distance": 1000,
                        "elapsed_time": 280,
                        "moving_time": 275,
                        "average_speed": 3.64,
                    }],
                    [{
                        "id": 99001,
                        "name": "5K",
                        "distance": 5000,
                        "moving_time": 1450,
                        "elapsed_time": 1460,
                    }],
                )
                db.upsert_activity_laps(9001, [{
                    "id": 19001,
                    "lap_index": 1,
                    "name": "Lap 1",
                    "distance": 1000,
                    "elapsed_time": 280,
                    "moving_time": 275,
                    "ground_contact_time": 244.2,
                    "vertical_ratio": 7.8,
                    "garmin_data": {"lapDTO": True},
                }])
                db.upsert_streams(9001, {
                    "time": {"data": [0, 1]},
                    "distance": {"data": [0.0, 3.4]},
                    "heartrate": {"data": [120, 122]},
                    "ground_contact_time": {"data": [245.0, 243.5]},
                    "stride_length": {"data": [1.1, 1.12]},
                    "garmin_metrics": {"data": [
                        {"performanceCondition": 2},
                        {"performanceCondition": 3},
                    ]},
                })
                db.upsert_activity_health(9001, {
                    "health_snapshot_at": "2026-07-15T07:50:50",
                    "health_sleep_date": "2026-07-15",
                    "health_sleep_score": 81,
                    "health_sleep_quality": "GOOD",
                    "health_sleep_duration_seconds": 22500,
                    "health_hrv_date": "2026-07-15",
                    "health_hrv_last_night_avg_ms": 74,
                    "health_hrv_status": "BALANCED",
                    "health_resting_hr_date": "2026-07-15",
                    "health_resting_hr_bpm": 44,
                })
                db.set_sync_meta("garmin_tokens", {"access": "test-token"})
                assert db.upsert_sleep_score("2026-07-15", 88, "GOOD", 25200)
                assert db.upsert_sleep_score("2026-07-16", None, None, 20000)

                activities = db.get_all_activities()
                assert len(activities) == 1, activities
                run = activities[0]
                assert run["vo2max"] == 55.5, run
                assert run["hr_time_in_zones"] == [{"seconds": 600, "zone": 4}], run
                assert run["garmin_fastest_splits"][0]["time"] == 270, run
                assert run["health_sleep_score"] == 81, run
                assert run["health_hrv_last_night_avg_ms"] == 74.0, run
                assert run["health_resting_hr_bpm"] == 44, run

                streams = db.get_streams(9001)["streams"]
                assert streams["ground_contact_time"]["data"] == [245.0, 243.5], streams
                assert streams["garmin_metrics"]["data"][1]["performanceCondition"] == 3
                assert db.get_activity_laps(9001)[0]["ground_contact_time"] == 244.2

                markers = raw.execute("""
                    SELECT run_summary_updated_at, run_zones_updated_at,
                           run_details_updated_at, run_laps_updated_at,
                           run_streams_updated_at, run_health_updated_at
                    FROM activities WHERE id = 9001
                """).fetchone()
                assert all(markers), markers
                meta = raw.execute(
                    "SELECT value, updated_at FROM sync_meta WHERE key = ?",
                    ("garmin_tokens",),
                ).fetchone()
                assert meta[1], meta
                assert db.get_sync_meta()["garmin_tokens"]["access"] == "test-token"
                assert db.get_latest_sleep_score("2026-07-15") == {
                    "date": "2026-07-15",
                    "sleep_score": 88,
                    "sleep_quality": "GOOD",
                    "sleep_duration_seconds": 25200,
                }
                assert db.get_latest_sleep_score("2026-07-16") is None
                duration_only = raw.execute(
                    "SELECT sleep_score, sleep_duration_seconds FROM sleep_history WHERE date = ?",
                    ("2026-07-16",),
                ).fetchone()
                assert duration_only == (None, 20000), duration_only

                db.delete_activity(9001)
                assert db.get_all_activities() == []
                assert "9001" in db.get_activity_tombstone_ids()
                for table in (
                    "activity_splits", "activity_best_efforts",
                    "activity_laps", "activity_streams",
                ):
                    count = raw.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE activity_id = 9001"
                    ).fetchone()[0]
                    assert count == 0, (table, count)
            ''')

    def test_existing_sqlite_schema_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE activities (
                    id INTEGER PRIMARY KEY,
                    athlete_id INTEGER,
                    type TEXT,
                    start_date_local TEXT
                );
                CREATE TABLE activity_streams (
                    activity_id INTEGER,
                    stream_index INTEGER,
                    PRIMARY KEY (activity_id, stream_index)
                );
                CREATE TABLE activity_laps (
                    id INTEGER UNIQUE,
                    activity_id INTEGER,
                    lap_index INTEGER,
                    PRIMARY KEY (activity_id, lap_index)
                );
                CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO sync_meta (key, value) VALUES ('legacy', 'true');
            """)
            conn.close()

            self._run(db_path, """
                import sqlite3
                import db_sqlite as db

                db.init_db()
                raw = sqlite3.connect(db.SQLITE_PATH)
                expected = {
                    "activities": {
                        "run_summary_updated_at", "run_zones_updated_at",
                        "run_details_updated_at", "run_laps_updated_at",
                        "run_streams_updated_at", "run_health_updated_at",
                        "vo2max", "health_sleep_score",
                    },
                    "activity_streams": {"garmin_metrics", "ground_contact_time"},
                    "activity_laps": {"garmin_data", "vertical_ratio"},
                    "sync_meta": {"updated_at"},
                }
                for table, wanted in expected.items():
                    columns = {
                        row[1] for row in raw.execute(f'PRAGMA table_info("{table}")')
                    }
                    assert wanted <= columns, (table, wanted - columns)
                updated_at = raw.execute(
                    "SELECT updated_at FROM sync_meta WHERE key = 'legacy'"
                ).fetchone()[0]
                assert updated_at
            """)


class GearQueryPortabilityTests(unittest.TestCase):
    """Les requetes doivent tourner sur les DEUX moteurs, pas seulement Postgres.

    `get_all_gears` faisait `primary_shoe AS primary` : « primary » est un mot
    reserve, donc SQLite refusait la requete et /api/data/shoes renvoyait 500 en
    mode dev — le mode que le README recommande pour demarrer sans base distante.
    """

    def test_get_all_gears_runs_on_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "gears.db"
            result = SQLiteIntegrationTests._run(self, db_path, '''
                import json
                import db_sqlite as db

                db.init_db()
                db.upsert_gears([{
                    "id": "g1",
                    "name": "Chaussure de test",
                    "nickname": "test",
                    "brand_name": "Marque",
                    "model_name": "Modele",
                    "distance": 120000,
                    "primary": True,
                    "retired": False,
                }])
                print(json.dumps(db.get_all_gears()))
            ''')

        gears = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(gears), 1)
        self.assertEqual(gears[0]["id"], "g1")
        # La colonne est bien exposee sous son alias, guillemets compris.
        self.assertIn("primary", gears[0])


if __name__ == "__main__":
    unittest.main()
