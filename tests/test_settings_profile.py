"""Tests for Settings -> "Your name": adding or updating the profile after sign-up.

Accounts made before sign-up asked for a name had no way to add one, so
chat greeted them by their email handle forever. Settings now shows the
same three fields, pre-filled when a profile exists, and saves them as the
same encrypted envelope through the tenant's own session.
"""

import json
import os
import unittest
from datetime import date
from unittest import mock

import crypto_service
import profile_service
from tests.test_crypto_service import KEY_A, _configured
from tests.test_signup_profile_and_theme import FakeAI, TEMPLATES

import flask

from routes import main_bp
from tests.app_test_support import configure_test_app


class FakeSupabase:
    def __init__(self, metadata=None, fail_read=False, fail_update=False, new_tokens=("acc-2", "ref-2")):
        self.metadata = metadata
        self.fail_read = fail_read
        self.fail_update = fail_update
        self.new_tokens = new_tokens
        self.updates = []

    def build_user_scoped_client(self, access_token):
        return object()

    def get_pending_account_deletion(self, user_client, user_id):
        return None

    def get_user_metadata(self, access_token):
        if self.fail_read:
            raise RuntimeError("auth down")
        return self.metadata

    def update_profile(self, access_token, refresh_token, metadata):
        if self.fail_update:
            raise RuntimeError("auth down")
        self.updates.append((access_token, refresh_token, metadata))
        return self.new_tokens


def _client(service):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_id="u1", user_email="ana.r@example.com", access_token="acc-1", refresh_token="ref-1")
    return client


FORM = {"first_name": "Ana", "last_name": "Rivera", "date_of_birth": "1990-05-17"}


class _Keys(unittest.TestCase):
    def setUp(self):
        patcher = _configured(f"k1:{KEY_A}", "k1")
        patcher.start()
        self.addCleanup(patcher.stop)
        crypto_service.reload_keys()
        self.addCleanup(crypto_service.reload_keys)


class SettingsProfileCardTests(_Keys):
    def test_older_account_sees_an_empty_form_inviting_a_name(self):
        body = _client(FakeSupabase(metadata={"email_verified": True})).get("/settings").get_data(as_text=True)
        self.assertIn('id="profile-form"', body)
        self.assertIn("Add your name so we can greet you.", body)
        self.assertIn('name="first_name" value=""', body)
        self.assertIn("Your details stay confidential.", body)
        latest = profile_service.latest_allowed_birthday(date.today()).isoformat()
        self.assertIn(f'max="{latest}"', body)

    def test_saved_profile_is_prefilled(self):
        metadata = profile_service.to_metadata(profile_service.validate("José", "O'Brien", "1990-05-17"))
        body = _client(FakeSupabase(metadata=metadata)).get("/settings").get_data(as_text=True)
        self.assertIn('value="José"', body)
        self.assertIn('value="O&#39;Brien"', body)
        self.assertIn('value="1990-05-17"', body)
        self.assertIn("This is how we greet you.", body)

    def test_settings_still_render_when_auth_cannot_be_read(self):
        response = _client(FakeSupabase(fail_read=True)).get("/settings")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="profile-form"', body)
        # Unknown is not "you have no name": don't tell someone who has one to add one.
        self.assertNotIn("Add your name so we can greet you.", body)


class SaveProfileTests(_Keys):
    def _post(self, service, **overrides):
        client = _client(service)
        response = client.post("/settings/profile", data={**FORM, **overrides})
        with client.session_transaction() as sess:
            return response, dict(sess)

    def test_saves_an_encrypted_envelope_and_greets_by_the_new_name(self):
        service = FakeSupabase()
        response, sess = self._post(service)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/settings#profile"))
        (access, refresh, metadata), = service.updates
        self.assertEqual((access, refresh), ("acc-1", "ref-1"))
        self.assertEqual(set(metadata), {"profile"})
        self.assertTrue(crypto_service.is_encrypted(metadata["profile"]))
        self.assertNotIn("Rivera", json.dumps(metadata))
        self.assertEqual(profile_service.read_metadata(metadata),
                         {"first_name": "Ana", "last_name": "Rivera", "date_of_birth": "1990-05-17"})
        self.assertEqual(sess["first_name"], "Ana")

    def test_rotated_tokens_are_kept(self):
        _, sess = self._post(FakeSupabase(new_tokens=("acc-9", "ref-9")))
        self.assertEqual((sess["access_token"], sess["refresh_token"]), ("acc-9", "ref-9"))
        _, sess = self._post(FakeSupabase(new_tokens=None))
        self.assertEqual((sess["access_token"], sess["refresh_token"]), ("acc-1", "ref-1"))

    def test_invalid_input_saves_nothing(self):
        for overrides in ({"first_name": ""}, {"last_name": "R1vera"}, {"date_of_birth": "2020-01-01"},
                          {"date_of_birth": "nope"}):
            with self.subTest(overrides=overrides):
                service = FakeSupabase()
                _, sess = self._post(service, **overrides)
                self.assertEqual(service.updates, [])
                self.assertNotIn("first_name", sess)

    def test_nothing_is_saved_without_encryption(self):
        with mock.patch.dict(os.environ, {"DATA_ENCRYPTION_KEYS": "", "DATA_ENCRYPTION_ACTIVE_KEY_ID": ""}):
            crypto_service.reload_keys()
            service = FakeSupabase()
            _, sess = self._post(service)
        self.assertEqual(service.updates, [])
        self.assertIn("can't be saved right now", str(sess.get("_flashes")))

    def test_an_auth_failure_is_reported_and_the_greeting_is_unchanged(self):
        _, sess = self._post(FakeSupabase(fail_update=True))
        self.assertNotIn("first_name", sess)
        self.assertIn("couldn't be saved", str(sess.get("_flashes")))

    def test_logged_out_is_sent_to_login(self):
        app_client = _client(FakeSupabase())
        with app_client.session_transaction() as sess:
            sess.clear()
        response = app_client.post("/settings/profile", data=FORM)
        self.assertTrue(response.headers["Location"].endswith("/login"))


class ReadMetadataTests(_Keys):
    def test_round_trip_and_garbage(self):
        metadata = profile_service.to_metadata(profile_service.validate("Ana", "Rivera", "1990-05-17"))
        self.assertEqual(profile_service.read_metadata(metadata)["last_name"], "Rivera")
        for bad in (None, [], {"profile": "x"}, {"profile": crypto_service.encrypt("[1, 2]")},
                    {"profile": crypto_service.encrypt(json.dumps({"first_name": "A"}))}):
            with self.subTest(bad=bad):
                self.assertIsNone(profile_service.read_metadata(bad))


if __name__ == "__main__":
    unittest.main()
