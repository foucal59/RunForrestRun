import os
import unittest
from unittest.mock import patch

from database_convergence import (
    _configured_databases,
    synchronize_available_databases,
)


LOCAL_URL = "postgresql://localhost:5432/strava"
NEON_URL = "postgresql://runner@example.neon.tech/strava?sslmode=require"


class DatabaseConvergenceTests(unittest.TestCase):
    def test_local_runtime_discovers_local_and_neon_once(self):
        env = {
            "DATABASE_URL": LOCAL_URL,
            "LOCAL_DATABASE_URL": LOCAL_URL,
            "DATABASE_URL_NEON": NEON_URL,
        }
        with patch.dict(os.environ, env, clear=True):
            labels, neon_url, local_url = _configured_databases()

        self.assertEqual(labels, ["local", "neon"])
        self.assertEqual(neon_url, NEON_URL)
        self.assertEqual(local_url, LOCAL_URL)

    def test_vercel_runtime_uses_primary_as_neon_when_alias_is_absent(self):
        with patch.dict(os.environ, {"DATABASE_URL": NEON_URL}, clear=True):
            labels, neon_url, local_url = _configured_databases()

        self.assertEqual(labels, ["neon"])
        self.assertEqual(neon_url, NEON_URL)
        self.assertEqual(local_url, "")

    def test_single_database_is_already_synchronized(self):
        with patch.dict(os.environ, {"DATABASE_URL": NEON_URL}, clear=True):
            result = synchronize_available_databases()

        self.assertEqual(result["mode"], "single_database")
        self.assertTrue(result["ok"])
        self.assertEqual(result["synchronized"], ["neon"])

    def test_two_databases_run_bidirectional_convergence(self):
        env = {
            "DATABASE_URL": LOCAL_URL,
            "LOCAL_DATABASE_URL": LOCAL_URL,
            "DATABASE_URL_NEON": NEON_URL,
            "MANUAL_SYNC_DB_TIMEOUT": "7",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("scripts.sync_neon_local.main", return_value=0) as converge,
        ):
            result = synchronize_available_databases()

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "converged")
        self.assertEqual(result["synchronized"], ["local", "neon"])
        converge.assert_called_once_with(
            neon_url=NEON_URL,
            local_url=LOCAL_URL,
            dry_run=False,
            prepare_only=False,
            connection_timeout=7,
        )

    def test_unreachable_replica_reports_partial_without_credentials(self):
        env = {
            "DATABASE_URL": NEON_URL,
            "LOCAL_DATABASE_URL": LOCAL_URL,
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("scripts.sync_neon_local.main", side_effect=TimeoutError("secret-url")),
        ):
            result = synchronize_available_databases()

        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "partial")
        self.assertEqual(result["synchronized"], ["neon"])
        self.assertEqual(result["error"], "TimeoutError")
        self.assertNotIn("secret-url", str(result))


if __name__ == "__main__":
    unittest.main()
