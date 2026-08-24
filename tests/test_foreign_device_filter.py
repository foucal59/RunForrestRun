"""Une montre qui n'est pas la tienne ne doit pas alimenter la base.

Un compte Garmin peut recevoir les activités d'une montre appairée au profil
sans être la tienne : ses sorties entrent en base au milieu des vraies, avec
leurs records. Le filtre doit l'écarter — sans jamais perdre tes propres runs,
ce qui est le piège : Garmin RENUMÉROTE une même montre à chaque réappairage, et
`get_devices()` ne renvoie que l'identifiant courant.

Aucun identifiant réel n'est écrit ici : ce sont des données propres à un
compte. Les valeurs ci-dessous ne sont que des étiquettes de scénario.
"""
import unittest
from unittest import mock

import garmin_freshness as gf

INTRUS = 3_461_000_001      # la montre qui n'est pas la tienne
MONTRE = 3_427_000_001      # ta montre, telle qu'enregistrée aujourd'hui
MONTRE_ANCIEN = 3_442_000_001   # la même montre, identifiant précédent
MONTRE_2024 = 3_428_000_001     # la même montre encore, deux réappairages plus tôt
MONTRE_2025 = 3_352_000_001     # et encore un


def _activity(aid, device_id, name="Course", start="2026-08-05T09:20:00"):
    return {
        "id": aid,
        "name": name,
        "start_date_local": start,
        "garmin_device_id": device_id,
    }


class ForeignDeviceTests(unittest.TestCase):
    def setUp(self):
        # Le filtre lit l'environnement à chaque appel : une variable héritée du
        # poste ferait passer ou échouer ces tests sans rapport avec le code.
        patcher = mock.patch.dict(
            gf.os.environ,
            {"GARMIN_ALLOWED_DEVICE_IDS": "", "GARMIN_BLOCKED_DEVICE_IDS": ""},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_foreign_device_is_rejected_and_your_watch_kept(self):
        allowed = {MONTRE}
        self.assertTrue(gf._is_foreign_device(_activity(1, INTRUS), allowed))
        self.assertFalse(gf._is_foreign_device(_activity(2, MONTRE), allowed))

    def test_an_unknown_allowlist_lets_everything_through(self):
        # Fail-open : mieux vaut une rando en trop qu'une sortie longue perdue
        # en silence parce que Garmin n'a pas repondu. Seuls les appareils
        # formellement bannis restent refuses — cf. le test dedie.
        self.assertFalse(gf._is_foreign_device(_activity(1, 999_222), set()))

    def test_activities_without_a_device_are_none_of_the_filter_s_business(self):
        # Imports Strava et saisies manuelles : aucune montre derriere, et
        # Garmin y met tantot None, tantot 0.
        allowed = {MONTRE}
        for raw in (None, 0, "", "abc"):
            with self.subTest(device=raw):
                self.assertFalse(gf._is_foreign_device(_activity(1, raw), allowed))

    def test_a_device_seen_only_in_the_database_is_admitted(self):
        # Une montre absente de get_devices() mais qui a deja alimente la base a
        # ete acceptee une fois : elle le reste. C'est par la que l'historique
        # survit a une renumerotation.
        api = mock.Mock()
        api.get_devices.return_value = [{"deviceId": MONTRE}]
        with mock.patch.object(gf.db, "get_known_device_ids", return_value={MONTRE_2024}):
            allowed = gf._allowed_device_ids(api)

        self.assertIn(MONTRE_2024, allowed)
        self.assertFalse(gf._is_foreign_device(_activity(1, MONTRE_2024), allowed))
        self.assertTrue(gf._is_foreign_device(_activity(2, INTRUS), allowed))

    def test_a_dead_garmin_api_and_a_mute_database_lose_nothing(self):
        # Garmin injoignable ET base muette : impossible de conclure, donc le
        # filtre s'abstient plutot que de rejeter en masse. Aucune graine codee
        # en dur ne peut jouer ce role — des identifiants d'appareils sont
        # propres a un compte.
        api = mock.Mock()
        api.get_devices.side_effect = RuntimeError("Garmin 503")
        with mock.patch.object(gf.db, "get_known_device_ids", side_effect=RuntimeError):
            allowed = gf._allowed_device_ids(api)

        self.assertEqual(allowed, set())
        for device in (MONTRE, MONTRE_ANCIEN, MONTRE_2024, INTRUS):
            with self.subTest(device=device):
                self.assertFalse(gf._is_foreign_device(_activity(1, device), allowed))

    def test_retired_ids_of_your_own_watch_are_never_rejected(self):
        # Regression : `garmin_device_id` n'est renseigne que depuis la mise en
        # place du filtre, donc les activites anterieures ont la colonne a NULL
        # et la base ne connait pas les anciens identifiants. Sans echappatoire,
        # la synchro rejetait des centaines de sorties bien reelles.
        api = mock.Mock()
        api.get_devices.return_value = [{"deviceId": MONTRE}]
        retired = f"{MONTRE_ANCIEN},{MONTRE_2024},{MONTRE_2025}"
        with mock.patch.dict(gf.os.environ, {"GARMIN_ALLOWED_DEVICE_IDS": retired}):
            with mock.patch.object(gf.db, "get_known_device_ids", return_value=set()):
                allowed = gf._allowed_device_ids(api)

        for device in (MONTRE, MONTRE_ANCIEN, MONTRE_2024, MONTRE_2025):
            with self.subTest(device=device):
                self.assertFalse(gf._is_foreign_device(_activity(1, device), allowed))
        self.assertTrue(gf._is_foreign_device(_activity(2, INTRUS), allowed))

    def test_a_blocked_device_stays_out_even_if_garmin_lists_it(self):
        # Une montre etrangere encore appairee apparait dans l'inventaire du
        # compte : c'est le seul cas que get_devices() ne peut pas trancher, et
        # la seule raison d'exister de la liste de blocage.
        api = mock.Mock()
        api.get_devices.return_value = [{"deviceId": MONTRE}, {"deviceId": INTRUS}]
        with mock.patch.dict(gf.os.environ, {"GARMIN_BLOCKED_DEVICE_IDS": str(INTRUS)}):
            with mock.patch.object(gf.db, "get_known_device_ids", return_value={INTRUS}):
                allowed = gf._allowed_device_ids(api)

            self.assertNotIn(INTRUS, allowed)
            self.assertTrue(gf._is_foreign_device(_activity(1, INTRUS), allowed))
            # ... y compris quand le filtre est par ailleurs desactive
            self.assertTrue(gf._is_foreign_device(_activity(2, INTRUS), set()))

    def test_the_environment_override_can_readmit_a_device(self):
        api = mock.Mock()
        api.get_devices.return_value = []
        with mock.patch.dict(gf.os.environ, {"GARMIN_ALLOWED_DEVICE_IDS": "42, 43 ,oops"}):
            with mock.patch.object(gf.db, "get_known_device_ids", return_value=set()):
                allowed = gf._allowed_device_ids(api)

        self.assertEqual(allowed, {42, 43})


if __name__ == "__main__":
    unittest.main()
