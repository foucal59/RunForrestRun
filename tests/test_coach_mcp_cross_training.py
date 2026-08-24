"""Les outils coach qui decident la seance doivent voir la charge hors course."""
import os
import unittest
from unittest.mock import patch

from coach_mcp import _analysis_payload, _static_snapshot_url, _training_payload


class CoachCrossTrainingPayloadTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "genere_le": "2026-08-08",
            "objectif": "Marathon",
            "volume_7j_km": 42.0,
            "seance_du_jour": {"date": "2026-08-08", "seance": "Footing"},
            "projection": [],
            "zones_allure": {},
            "regle_ajustement": "Alleger si fatigue",
            "derniers_runs": [{"date": "2026-08-07", "distance_km": 10}],
            "autres_activites": [
                {
                    "date": "2026-08-08",
                    "sport": "Randonnee",
                    "duree_minutes": 180,
                    "denivele_m": 900,
                }
            ],
        }

    def test_training_payload_includes_cross_training_load(self):
        payload = _training_payload(self.snapshot)

        self.assertEqual(payload["autres_activites"], self.snapshot["autres_activites"])
        self.assertIn("ne comptent pas", payload["consigne_fatigue"])

    def test_run_analysis_payload_includes_cross_training_load(self):
        payload = _analysis_payload(self.snapshot, 1)

        self.assertEqual(payload["autres_activites"], self.snapshot["autres_activites"])
        self.assertIn("hors course", payload["consigne_client"])


class CoachSnapshotUrlTests(unittest.TestCase):
    def test_vercel_production_url_wins_over_base_and_deployment_urls(self):
        env = {
            "COACH_STATIC_BASE_URL": "",
            "VERCEL_PROJECT_PRODUCTION_URL": "example-dashboard.vercel.app",
            "VERCEL_URL": "rfr-preview.vercel.app",
            "BASE_URL": "http://localhost:8080",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                _static_snapshot_url(),
                "https://example-dashboard.vercel.app/coach-journal.json",
            )

    def test_explicit_coach_base_url_remains_the_top_priority(self):
        env = {
            "COACH_STATIC_BASE_URL": "https://coach.example.test/",
            "VERCEL_PROJECT_PRODUCTION_URL": "example-dashboard.vercel.app",
            "VERCEL_URL": "rfr-preview.vercel.app",
            "BASE_URL": "https://example-dashboard.vercel.app",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                _static_snapshot_url(),
                "https://coach.example.test/coach-journal.json",
            )


if __name__ == "__main__":
    unittest.main()
