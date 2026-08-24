"""Un chrono aidé par la pente n'est pas un record.

Un 14,5 km de montagne descendant de 1350 m à 910 m sortait un
« 5K en 22:27 » qui n'était que de la gravité (-385 m sur la fenêtre). Ces tests
figent le garde-fou : au-delà de db.MAX_NET_DROP_PER_KM de perte nette, la
fenêtre est refusée, et on retient la meilleure fenêtre restante — ou aucune.
"""
import unittest

from database_pg import MAX_NET_DROP_PER_KM
from garmin_freshness import _compute_best_efforts


def _series(pace_s_per_km: float, drop_per_km: float, meters: int = 6000,
            step: int = 100, start_alt: float = 500.0):
    """Série cumulée (distance, temps) + altitude, à allure et pente constantes."""
    cum, alt = [], []
    for d in range(step, meters + 1, step):
        cum.append((float(d), d * pace_s_per_km / 1000.0))
        alt.append(start_alt - drop_per_km * d / 1000.0)
    return cum, alt


def _by_name(efforts):
    return {e["name"]: e for e in efforts}


class BestEffortElevationTests(unittest.TestCase):
    def test_flat_run_keeps_its_effort_and_records_the_delta(self):
        cum, alt = _series(pace_s_per_km=300, drop_per_km=0)
        effort = _by_name(_compute_best_efforts(1, cum, alt))["5K"]
        self.assertEqual(effort["moving_time"], 1500)
        self.assertAlmostEqual(effort["elevation_delta"], 0.0, places=6)

    def test_steep_descent_yields_no_effort_at_all(self):
        # 40 m/km de perte : aucune fenêtre de 5 km n'est acceptable.
        cum, alt = _series(pace_s_per_km=240, drop_per_km=40)
        self.assertEqual(_compute_best_efforts(1, cum, alt), [])

    def test_slower_flat_window_beats_the_faster_downhill_one(self):
        # 5 km de descente rapide (4:00/km, -40 m/km) puis 5 km plats plus lents.
        cum, alt = [], []
        t = 0.0
        a = 900.0
        for d in range(100, 5001, 100):
            t += 24.0        # 4:00/km
            a -= 4.0         # 40 m/km
            cum.append((float(d), t))
            alt.append(a)
        for d in range(5100, 10001, 100):
            t += 30.0        # 5:00/km
            cum.append((float(d), t))
            alt.append(a)
        effort = _by_name(_compute_best_efforts(1, cum, alt))["5K"]
        # 1200 s (la descente pure) est refusé. La fenêtre retenue peut mordre
        # sur le début de la descente tant que la perte nette tient dans le
        # quota — elle reste donc proche des 1500 s du plat, jamais de 1200.
        self.assertGreater(effort["moving_time"], 1400)
        self.assertGreaterEqual(
            effort["elevation_delta"], -MAX_NET_DROP_PER_KM * 5,
        )

    def test_gentle_descent_under_the_threshold_still_counts(self):
        cum, alt = _series(pace_s_per_km=300, drop_per_km=MAX_NET_DROP_PER_KM - 1)
        effort = _by_name(_compute_best_efforts(1, cum, alt))["5K"]
        self.assertEqual(effort["moving_time"], 1500)
        # À 1 échantillon près : l'altitude de départ est lue au premier point
        # de la série, alors que la fenêtre part de l'origine de l'activité.
        self.assertAlmostEqual(
            effort["elevation_delta"], -(MAX_NET_DROP_PER_KM - 1) * 5, delta=1.0,
        )

    def test_without_altitude_nothing_is_filtered_and_delta_stays_unknown(self):
        # Séries d'origine « laps » : pas d'altitude, on garde le comportement
        # historique plutôt que de rejeter faute d'information.
        cum, _ = _series(pace_s_per_km=240, drop_per_km=40)
        effort = _by_name(_compute_best_efforts(1, cum))["5K"]
        self.assertEqual(effort["moving_time"], 1200)
        self.assertIsNone(effort["elevation_delta"])

    def test_altitude_gaps_do_not_disqualify_a_window(self):
        cum, alt = _series(pace_s_per_km=300, drop_per_km=0)
        alt = [None] * len(alt)
        effort = _by_name(_compute_best_efforts(1, cum, alt))["5K"]
        self.assertEqual(effort["moving_time"], 1500)
        self.assertIsNone(effort["elevation_delta"])


if __name__ == "__main__":
    unittest.main()
