"""Le script du coach et l'endpoint du site doivent rester UNE seule methode.

Le coach matinal lit sa seance via `scripts/seance_du_jour.py`. Si ce script
s'ecartait de l'endpoint `/api/data/daily-training`, on retomberait dans le bug
d'aout 2026 : deux methodes sur la meme trame, donc un decalage a chaque fois
qu'une des deux couches bouge. Ces tests verifient statiquement que les deux
chemins appellent le meme constructeur avec les memes entrees.
"""

import ast
import unittest
from pathlib import Path

from scripts.seance_du_jour import _json_payload, _render

ROOT = Path(__file__).resolve().parents[1]


def _calls(source: Path) -> list[ast.Call]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _call_names(source: Path) -> set[str]:
    names = set()
    for call in _calls(source):
        func = call.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


class SeanceDuJourSingleSourceTests(unittest.TestCase):
    script = ROOT / "scripts" / "seance_du_jour.py"
    server = ROOT / "server.py"

    def test_script_exists_and_is_executable_by_the_coach(self):
        self.assertTrue(self.script.exists(), "scripts/seance_du_jour.py a disparu")

    def test_script_uses_the_same_builder_as_the_site(self):
        names = _call_names(self.script)
        # Meme constructeur que /api/data/daily-training.
        self.assertIn("build_three_day_training_guidance", names)
        # Memes overrides coach que l'endpoint, sinon le script ignorerait les
        # ajustements deja poses.
        self.assertIn("_apply_coach_plan_overrides", names)
        # Memes entrees : runs charges du plan + dernier score de sommeil.
        self.assertIn("get_recent_runs_for_plan", names)
        self.assertIn("get_latest_sleep_score", names)

    def test_script_never_rebuilds_the_session_itself(self):
        # Le script doit lire, pas recomposer : aucune fabrication de seance.
        forbidden = {"_build_calendar", "_schedule_for", "_easy_plan", "_quality_plan", "_long_plan"}
        leaked = forbidden & _call_names(self.script)
        self.assertEqual(leaked, set(), f"le script recompose la seance : {leaked}")

    def test_script_truncates_runs_like_the_endpoint(self):
        # L'endpoint ne garde que les 10 derniers entrainements charges. Une
        # fenetre differente donnerait une autre adaptation, donc un decalage.
        for source in (self.script, self.server):
            slices = [
                node
                for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
            ]
            upper_bounds = {
                node.slice.upper.value
                for node in slices
                if isinstance(node.slice.upper, ast.Constant)
            }
            self.assertIn(10, upper_bounds, f"{source.name} ne tronque plus a 10 runs")

    def test_output_markers_are_stable(self):
        # Le SKILL du coach lit entre ces deux marqueurs. Les renommer casserait
        # silencieusement la lecture cote tache planifiee.
        text = self.script.read_text(encoding="utf-8")
        self.assertIn("=== DEBUT SEANCE DU JOUR ===", text)
        self.assertIn("=== FIN SEANCE DU JOUR ===", text)


class SeanceDuJourContractTests(unittest.TestCase):
    reference = {
        "value": 179.0,
        "source": "observed_90d",
        "observed90d": 179.0,
        "observedOn": "2026-06-28",
        "windowDays": 90,
    }

    @staticmethod
    def race_guidance():
        return {
            "sessions": [{
                "date": "2026-10-25",
                "dateLabel": "dimanche 25 octobre",
                "title": "Marathon",
                "status": "planned",
                "statusLabel": "A venir",
                "category": "race",
                "estimatedKm": 42.2,
                "estimatedMinutes": 200,
                "estimatedDuration": "3h20",
                "adjustment": "Plan code, sans ajustement",
                "session": {
                    "warmup": "10' tres facile",
                    "main": "Marathon",
                    "cooldown": "Marche et ravitaillement",
                },
                "paces": [],
                "hr": [{"label": "Course", "pctMin": 0.80, "pctMax": 0.88}],
            }],
        }

    def test_json_carries_the_shared_heart_rate_reference(self):
        payload = _json_payload(self.race_guidance(), self.reference)

        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["maxHr"], 179)
        self.assertEqual(payload["heartRateReference"], {
            **self.reference,
            "browserLocalOverrideVisible": False,
        })
        # Le JSON part de la meme seance que le texte : les deux sorties du coach
        # ne doivent jamais decrire deux jours differents.
        self.assertEqual(payload["sessions"][0]["date"], "2026-10-25")

    def test_text_explains_the_heart_rate_value_and_its_observation_date(self):
        rendered = _render(self.race_guidance(), 0, self.reference)

        self.assertIn("FC max utilisee 179 bpm, observee sur 90 jours, pic du 2026-06-28", rendered)
        self.assertIn("override manuel propre a un navigateur", rendered)


if __name__ == "__main__":
    unittest.main()
