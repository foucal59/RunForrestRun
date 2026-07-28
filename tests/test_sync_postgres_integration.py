import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pg8000.dbapi


ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync_neon_local.py"
POSTGRES_TOOLS = (shutil.which("initdb"), shutil.which("pg_ctl"))

# PostgreSQL 16 refuse de demarrer quand le postmaster devient multithreade au
# boot ("postmaster became multithreaded during startup") — cas courant sur
# macOS recent quand la locale n'est pas explicite. Forcer LC_ALL/LANG=C evite
# le probleme pour tout le cluster jetable de ce test.
PG_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


@unittest.skipUnless(all(POSTGRES_TOOLS), "PostgreSQL server tools are unavailable")
class PostgresReplicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data_dir = self.base / "data"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]

        init = subprocess.run(
            [
                POSTGRES_TOOLS[0], "-D", str(self.data_dir), "-A", "trust",
                "-U", "postgres", "--no-locale", "--encoding=UTF8",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            env=PG_ENV,
        )
        if init.returncode:
            self.tmp.cleanup()
            self.fail(f"initdb failed:\n{init.stdout}\n{init.stderr}")

        start = subprocess.run(
            [
                POSTGRES_TOOLS[1], "-D", str(self.data_dir),
                "-l", str(self.base / "postgres.log"), "-w", "start",
                "-o", f"-F -h 127.0.0.1 -p {self.port}",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            env=PG_ENV,
        )
        if start.returncode:
            self.tmp.cleanup()
            self.fail(f"postgres start failed:\n{start.stdout}\n{start.stderr}")

        self.neon_url = self._url("neon_test")
        self.local_url = self._url("local_test")
        createdb = str(Path(POSTGRES_TOOLS[0]).with_name("createdb"))
        for database in ("neon_test", "local_test"):
            result = subprocess.run(
                [
                    createdb, "-h", "127.0.0.1", "-p", str(self.port),
                    "-U", "postgres", database,
                ],
                text=True,
                capture_output=True,
                timeout=15,
                env=PG_ENV,
            )
            if result.returncode:
                self.fail(f"createdb {database} failed: {result.stderr}")
        for url in (self.neon_url, self.local_url):
            self._create_core_schema(url)

    def tearDown(self):
        if getattr(self, "data_dir", None) and self.data_dir.exists():
            subprocess.run(
                [POSTGRES_TOOLS[1], "-D", str(self.data_dir), "-m", "fast", "stop"],
                text=True,
                capture_output=True,
                timeout=15,
                env=PG_ENV,
            )
        if getattr(self, "tmp", None):
            self.tmp.cleanup()

    def _url(self, database: str) -> str:
        return f"postgresql://postgres@127.0.0.1:{self.port}/{database}"

    def _connect(self, url: str):
        database = url.rsplit("/", 1)[-1]
        return pg8000.dbapi.connect(
            host="127.0.0.1",
            port=self.port,
            user="postgres",
            database=database,
            timeout=10,
        )

    def _create_core_schema(self, url: str) -> None:
        statements = (
            """CREATE TABLE activities (
                id BIGINT PRIMARY KEY,
                athlete_id BIGINT,
                name TEXT,
                type TEXT DEFAULT 'Run',
                start_date_local TIMESTAMPTZ,
                details_fetched_at TIMESTAMPTZ,
                sync_complete_at TIMESTAMPTZ,
                sync_status TEXT NOT NULL DEFAULT 'partial',
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE activity_laps (
                id BIGINT UNIQUE,
                activity_id BIGINT NOT NULL,
                lap_index INTEGER NOT NULL,
                name TEXT,
                PRIMARY KEY (activity_id, lap_index)
            )""",
            """CREATE TABLE activity_splits (
                id BIGSERIAL UNIQUE,
                activity_id BIGINT NOT NULL,
                split_index INTEGER NOT NULL,
                split_type TEXT NOT NULL DEFAULT 'metric',
                distance DOUBLE PRECISION,
                UNIQUE (activity_id, split_index, split_type)
            )""",
            """CREATE TABLE activity_best_efforts (
                id BIGINT PRIMARY KEY,
                activity_id BIGINT NOT NULL,
                name TEXT,
                distance DOUBLE PRECISION
            )""",
            """CREATE TABLE activity_streams (
                activity_id BIGINT NOT NULL,
                stream_index INTEGER NOT NULL,
                time_sec INTEGER,
                PRIMARY KEY (activity_id, stream_index)
            )""",
        )
        conn = self._connect(url)
        try:
            cur = conn.cursor()
            for statement in statements:
                cur.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def _run_sync(self, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update({
            "DATABASE_URL_NEON": self.neon_url,
            "LOCAL_DATABASE_URL": self.local_url,
            "SYNC_DB_TIMEOUT": "15",
            "LC_ALL": "C",
            "LANG": "C",
        })
        result = subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
        )
        if result.returncode:
            self.fail(
                f"sync failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    @staticmethod
    def _execute(conn, sql: str, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def test_full_bidirectional_convergence_is_idempotent(self):
        self._run_sync("--prepare-only")
        neon = self._connect(self.neon_url)
        local = self._connect(self.local_url)
        try:
            self._execute(neon, """
                INSERT INTO activities
                    (id, name, type, start_date_local, details_fetched_at,
                     sync_status, run_laps_updated_at, run_streams_updated_at)
                VALUES
                    (1, 'Neon only', 'Run', NOW(), NOW(), 'partial',
                     '2026-07-15T10:00:00Z', '2026-07-15T10:00:00Z'),
                    (3, 'Components', 'Run', NOW(), NOW(), 'partial', NULL, NULL),
                    (4, 'Hash conflict', 'Run', NOW(), NOW(), 'partial', NULL, NULL)
            """)
            self._execute(neon, """
                UPDATE activities
                SET vo2max = 50,
                    run_summary_updated_at = '2026-07-15T10:00:00Z',
                    hr_time_in_zones = '[{"zone": 1}]'::jsonb,
                    run_zones_updated_at = '2026-07-15T12:00:00Z',
                    health_sleep_score = 82,
                    health_hrv_last_night_avg_ms = 71,
                    health_resting_hr_bpm = 43,
                    run_health_updated_at = '2026-07-15T13:00:00Z'
                WHERE id = 3
            """)
            self._execute(neon, """
                INSERT INTO activity_laps (id, activity_id, lap_index, name)
                VALUES (101, 1, 1, 'Neon lap')
            """)
            self._execute(neon, """
                INSERT INTO activity_streams
                    (activity_id, stream_index, time_sec, garmin_metrics)
                VALUES (1, 0, 0, '{"side": "neon"}'::jsonb),
                       (4, 0, 10, '{"winner": "neon"}'::jsonb)
            """)
            self._execute(neon, """
                INSERT INTO vo2max_history (date, vo2max, updated_at)
                VALUES ('2026-07-15', 55.5, '2026-07-15T10:00:00Z')
            """)
            self._execute(neon, """
                INSERT INTO sync_meta (key, value, updated_at)
                VALUES ('garmin_tokens', '{"side":"neon-old"}',
                        '2026-07-15T10:00:00Z')
            """)
            self._execute(neon, """
                INSERT INTO sync_tombstones (entity_type, entity_id, deleted_at)
                VALUES ('activity', '5', '2026-07-15T12:00:00Z')
            """)
            neon.commit()

            self._execute(local, """
                INSERT INTO activities
                    (id, name, type, start_date_local, details_fetched_at,
                     sync_status, run_laps_updated_at, run_streams_updated_at)
                VALUES
                    (2, 'Local only', 'Run', NOW(), NOW(), 'partial',
                     '2026-07-15T11:00:00Z', '2026-07-15T11:00:00Z'),
                    (3, 'Components', 'Run', NOW(), NOW(), 'partial', NULL, NULL),
                    (4, 'Hash conflict', 'Run', NOW(), NOW(), 'partial', NULL, NULL),
                    (5, 'Deleted', 'Run', NOW(), NOW(), 'partial', NULL, NULL)
            """)
            self._execute(local, """
                UPDATE activities
                SET vo2max = 55,
                    run_summary_updated_at = '2026-07-15T11:00:00Z',
                    hr_time_in_zones = '[{"zone": 2}]'::jsonb,
                    run_zones_updated_at = '2026-07-15T10:00:00Z'
                WHERE id = 3
            """)
            self._execute(local, """
                INSERT INTO activity_laps (id, activity_id, lap_index, name)
                VALUES (202, 2, 1, 'Local lap'), (505, 5, 1, 'Deleted lap')
            """)
            self._execute(local, """
                INSERT INTO activity_streams
                    (activity_id, stream_index, time_sec, garmin_metrics)
                VALUES (2, 0, 0, '{"side": "local"}'::jsonb),
                       (4, 0, 99, '{"winner": "local"}'::jsonb),
                       (5, 0, 0, '{"deleted": true}'::jsonb)
            """)
            self._execute(local, """
                INSERT INTO sleep_history
                    (date, sleep_score, sleep_quality, updated_at)
                VALUES ('2026-07-15', 88, 'GOOD', '2026-07-15T11:00:00Z')
            """)
            self._execute(local, """
                INSERT INTO sync_meta (key, value, updated_at)
                VALUES ('garmin_tokens', '{"side":"local-new"}',
                        '2026-07-15T11:00:00Z')
            """)
            local.commit()
        finally:
            neon.close()
            local.close()

        first = self._run_sync()
        self.assertIn("small_rows=3", first.stderr)
        self.assertIn("tombstones=1", first.stderr)

        neon = self._connect(self.neon_url)
        local = self._connect(self.local_url)
        try:
            for conn in (neon, local):
                ids = {
                    int(row[0]) for row in self._execute(
                        conn, "SELECT id FROM activities ORDER BY id"
                    ).fetchall()
                }
                self.assertEqual(ids, {1, 2, 3, 4})
                statuses = self._execute(
                    conn, "SELECT DISTINCT sync_status FROM activities"
                ).fetchall()
                self.assertEqual({row[0] for row in statuses}, {"ok"})
                component = self._execute(conn, """
                    SELECT vo2max, hr_time_in_zones
                    FROM activities WHERE id = 3
                """).fetchone()
                self.assertEqual(component[0], 55)
                self.assertEqual(component[1], [{"zone": 1}])
                health = self._execute(conn, """
                    SELECT health_sleep_score, health_hrv_last_night_avg_ms,
                           health_resting_hr_bpm
                    FROM activities WHERE id = 3
                """).fetchone()
                self.assertEqual(list(health), [82, 71, 43])
                hash_winner = self._execute(conn, """
                    SELECT time_sec, garmin_metrics
                    FROM activity_streams WHERE activity_id = 4
                """).fetchone()
                self.assertEqual(hash_winner[0], 10)
                self.assertEqual(hash_winner[1], {"winner": "neon"})
                self.assertEqual(
                    self._execute(
                        conn, "SELECT COUNT(*) FROM activity_laps WHERE activity_id IN (1, 2)"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    self._execute(
                        conn, "SELECT vo2max FROM vo2max_history WHERE date = '2026-07-15'"
                    ).fetchone()[0],
                    55.5,
                )
                self.assertEqual(
                    self._execute(
                        conn, "SELECT sleep_score FROM sleep_history WHERE date = '2026-07-15'"
                    ).fetchone()[0],
                    88,
                )
                self.assertEqual(
                    self._execute(
                        conn, "SELECT value FROM sync_meta WHERE key = 'garmin_tokens'"
                    ).fetchone()[0],
                    '{"side":"local-new"}',
                )
                self.assertEqual(
                    self._execute(
                        conn, "SELECT COUNT(*) FROM sync_tombstones WHERE entity_id = '5'"
                    ).fetchone()[0],
                    1,
                )
        finally:
            neon.close()
            local.close()

        second = self._run_sync()
        self.assertIn("pending=0", second.stderr)
        self.assertIn("actions=0", second.stderr)
        self.assertIn("small_rows=0", second.stderr)
        self.assertIn("tombstones=0", second.stderr)


if __name__ == "__main__":
    unittest.main()
