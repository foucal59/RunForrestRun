import unittest

import garmin_freshness as gf
from garmin_freshness import _api_from_token_payload, _build_profile


class FakeClient:
    def connectapi(self, _path):
        return {"userInfo": {}}


class FakeGarmin:
    client = FakeClient()
    full_name = "Test Runner"
    display_name = "garmin-display-name"

    def get_user_profile(self):
        return {"id": 62585735, "userData": {}}


class GarminProfileTests(unittest.TestCase):
    def test_uses_public_profile_id_when_personal_information_omits_it(self):
        profile = _build_profile(FakeGarmin())

        self.assertEqual(profile["user_id"], 62585735)
        self.assertEqual(profile["full_name"], "Test Runner")

    def test_token_load_survives_social_profile_failure(self):
        class FakeTokenClient:
            di_refresh_token = "refresh"

            def loads(self, raw):
                self.raw = raw

            def _token_expires_soon(self):
                return False

        class ProfileFailingGarmin:
            def __init__(self):
                self.client = FakeTokenClient()

            def _load_profile_and_settings(self):
                raise RuntimeError("Failed to retrieve social profile")

        original_garmin = gf.Garmin
        try:
            gf.Garmin = ProfileFailingGarmin
            api = _api_from_token_payload({
                "di_token": "access",
                "di_refresh_token": "refresh",
                "di_client_id": "client",
            })
        finally:
            gf.Garmin = original_garmin

        self.assertIsInstance(api, ProfileFailingGarmin)

    def test_check_and_populate_marks_reauth_when_tokens_missing(self):
        original_load = gf.load_garmin_api
        try:
            gf.load_garmin_api = lambda token_dir="": None
            result = gf.check_and_populate()
        finally:
            gf.load_garmin_api = original_load

        self.assertEqual(result["skipped"], "garmin_not_authenticated")
        self.assertTrue(result["reauth_required"])

    def test_garmin_login_mirrors_local_tokens(self):
        class LoginGarmin:
            def __init__(self, **_kwargs):
                self.login_tokenstore = None

            def login(self, tokenstore):
                self.login_tokenstore = tokenstore

        saved = []
        original_garmin = gf.Garmin
        original_save = gf._save_api_tokens
        original_build_profile = gf._build_profile
        try:
            gf.Garmin = LoginGarmin
            gf._save_api_tokens = lambda api, token_dir="": saved.append((api, token_dir))
            gf._build_profile = lambda _api: {"user_id": 123}
            profile = gf.garmin_login("email@example.com", "secret", token_dir="/tmp/garmin")
        finally:
            gf.Garmin = original_garmin
            gf._save_api_tokens = original_save
            gf._build_profile = original_build_profile

        self.assertEqual(profile["user_id"], 123)
        self.assertEqual(saved[0][1], "/tmp/garmin")


if __name__ == "__main__":
    unittest.main()
