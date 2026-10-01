"""Tests for account_requirements.py: existing accounts are asked for what new features need.

The rule (docs/frontend/account-requirements.md): an account that lacks
something a feature now needs is asked for it at its next login, or on
its next visit to a gated page if it was already signed in, and is never
left stuck.
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import flask

import account_requirements
import crypto_service
import profile_service
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_crypto_service import KEY_A, _configured
from tests.test_signup_profile_and_theme import TEMPLATES, FakeAI

FORM = {"first_name": "Ana", "last_name": "Rivera", "date_of_birth": "1990-05-17"}


class FakeSupabase:
    def __init__(self, metadata=None, fail_read=False, fail_update=False):
        self.metadata = metadata
        self.fail_read = fail_read
        self.fail_update = fail_update
        self.reads = 0
        self.updates = []

    # login
    def sign_in(self, email, password):
        user = SimpleNamespace(email=email, id="user-1", user_metadata=self.metadata)
        return SimpleNamespace(user=user, session=SimpleNamespace(access_token="acc-1", refresh_token="ref-1"))

    # chat page
    def build_user_scoped_client(self, access_token):
        return object()

    def list_conversations(self, user_client, archived=False):
        return []

    def get_pending_account_deletion(self, user_client, user_id):
        return None

    # requirements
    def get_user_metadata(self, access_token):
        self.reads += 1
        if self.fail_read:
            raise RuntimeError("auth down")
        return self.metadata

    def update_user_metadata(self, access_token, refresh_token, metadata):
        if self.fail_update:
            raise RuntimeError("auth down")
        self.updates.append(metadata)
        return ("acc-2", "ref-2")


def _app(service):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app.test_client()


def _signed_in(service, **extra):
    client = _app(service)
    with client.session_transaction() as sess:
        sess.update(user_id="u1", user_email="ana.r@example.com", access_token="acc-1", refresh_token="ref-1", **extra)
    return client


def _session(client):
    with client.session_transaction() as sess:
        return dict(sess)


class _Keys(unittest.TestCase):
    def setUp(self):
        patcher = _configured(f"k1:{KEY_A}", "k1")
        # Cleanups run last-in-first-out: the env must be restored BEFORE
        # the keys are reloaded, or this test's key leaks into the next.
        self.addCleanup(crypto_service.reload_keys)
        patcher.start()
        self.addCleanup(patcher.stop)
        crypto_service.reload_keys()

    def profile_metadata(self):
        return profile_service.to_metadata(profile_service.validate(**FORM))


class LoginTests(_Keys):
    def _login(self, service):
        client = _app(service)
        return client, client.post("/login", data={"email": "ana.r@example.com", "password": "pw"})

    def test_older_account_is_asked_at_login(self):
        _, response = self._login(FakeSupabase(metadata={"email_verified": True}))
        self.assertTrue(response.headers["Location"].endswith("/account/complete"))

    def test_account_with_everything_goes_straight_to_chat(self):
        _, response = self._login(FakeSupabase(metadata=self.profile_metadata()))
        self.assertTrue(response.headers["Location"].endswith("/chat"))

    def test_nothing_is_asked_when_it_cannot_be_stored(self):
        with mock.patch.dict(os.environ, {"DATA_ENCRYPTION_KEYS": "", "DATA_ENCRYPTION_ACTIVE_KEY_ID": ""}):
            crypto_service.reload_keys()
            _, response = self._login(FakeSupabase(metadata=None))
        self.assertTrue(response.headers["Location"].endswith("/chat"))

    def test_a_fresh_login_forgets_earlier_skips(self):
        client = _app(FakeSupabase(metadata=None))
        with client.session_transaction() as sess:
            sess[account_requirements.SKIPPED_KEY] = ["profile"]
        client.post("/login", data={"email": "a@b.co", "password": "pw"})
        self.assertNotIn(account_requirements.SKIPPED_KEY, _session(client))


class AlreadySignedInTests(_Keys):
    """Sessions from before a requirement existed are checked on their next gated page."""

    def test_old_session_without_a_profile_is_sent_to_the_page(self):
        service = FakeSupabase(metadata=None)
        response = _signed_in(service).get("/chat")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/complete?next=/chat", response.headers["Location"])
        self.assertEqual(service.reads, 1)

    def test_checked_once_per_session(self):
        service = FakeSupabase(metadata=self.profile_metadata())
        client = _signed_in(service)
        for _ in range(3):
            self.assertEqual(client.get("/chat").status_code, 200)
        self.assertEqual(service.reads, 1)

    def test_a_new_requirement_version_rechecks_existing_sessions(self):
        service = FakeSupabase(metadata=self.profile_metadata())
        client = _signed_in(service, **{account_requirements.SESSION_KEY: {"v": "old-version", "missing": []}})
        client.get("/chat")
        self.assertEqual(service.reads, 1)
        self.assertEqual(_session(client)[account_requirements.SESSION_KEY]["v"], account_requirements.REQUIREMENTS_VERSION)

    def test_unreadable_account_is_not_locked_out(self):
        response = _signed_in(FakeSupabase(fail_read=True)).get("/chat")
        self.assertEqual(response.status_code, 200)

    def test_a_failed_check_is_retried_later_not_only_at_next_login(self):
        service = FakeSupabase(fail_read=True)
        client = _signed_in(service)
        self.assertEqual(client.get("/chat").status_code, 200)
        self.assertEqual(client.get("/chat").status_code, 200)
        self.assertEqual(service.reads, 1)  # not on every page
        service.fail_read, service.metadata = False, None
        with mock.patch.object(account_requirements.time, "time",
                               return_value=__import__("time").time() + account_requirements.RETRY_UNKNOWN_SECONDS + 1):
            response = client.get("/chat")
        self.assertEqual(service.reads, 2)
        self.assertIn("/account/complete", response.headers["Location"])

    def test_ungated_pages_stay_reachable(self):
        client = _signed_in(FakeSupabase(metadata=None))
        self.assertEqual(client.get("/chat").status_code, 302)  # records what's missing
        for path in ("/settings", "/learn-more", "/building", "/account/complete"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)


class CompletePageTests(_Keys):
    def _pending_client(self, service):
        return _signed_in(service, **{account_requirements.SESSION_KEY: {
            "v": account_requirements.REQUIREMENTS_VERSION, "missing": ["profile"]}})

    def test_page_explains_and_shows_the_fields_with_no_skip_for_a_required_one(self):
        body = self._pending_client(FakeSupabase()).get("/account/complete?next=/chat").get_data(as_text=True)
        self.assertIn(account_requirements.PROFILE.title, body)
        self.assertIn("Your account was made before we asked.", body)
        self.assertIn('name="first_name"', body)
        self.assertIn("Your details stay confidential.", body)
        self.assertNotIn("Remind me next time", body)
        self.assertIn('name="next" value="/chat"', body)

    def test_save_stores_encrypted_metadata_and_continues(self):
        service = FakeSupabase()
        client = self._pending_client(service)
        response = client.post("/account/complete", data={**FORM, "action": "save", "next": "/chat?conversation_id=c1"})
        self.assertTrue(response.headers["Location"].endswith("/chat?conversation_id=c1"))
        (metadata,) = service.updates
        self.assertTrue(crypto_service.is_encrypted(metadata["profile"]))
        self.assertNotIn("Rivera", json.dumps(metadata))
        sess = _session(client)
        self.assertEqual(sess["first_name"], "Ana")
        self.assertEqual((sess["access_token"], sess["refresh_token"]), ("acc-2", "ref-2"))
        self.assertEqual(sess[account_requirements.SESSION_KEY]["missing"], [])
        self.assertEqual(client.get("/chat").status_code, 200)

    def test_invalid_input_is_shown_again_with_what_was_typed(self):
        service = FakeSupabase()
        response = self._pending_client(service).post(
            "/account/complete", data={**FORM, "date_of_birth": "2020-01-01", "action": "save"})
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        self.assertIn("at least 13", body)
        self.assertIn('value="Rivera"', body)
        self.assertEqual(service.updates, [])

    def test_a_failed_save_lets_the_tenant_continue(self):
        client = self._pending_client(FakeSupabase(fail_update=True))
        response = client.post("/account/complete", data={**FORM, "action": "save", "next": "/chat"})
        self.assertTrue(response.headers["Location"].endswith("/chat"))
        self.assertIn("ask again next time", str(_session(client).get("_flashes")))
        self.assertEqual(client.get("/chat").status_code, 200)

    def test_a_required_requirement_cannot_be_skipped(self):
        client = self._pending_client(FakeSupabase())
        client.post("/account/complete", data={"action": "skip", "next": "/chat"})
        self.assertEqual(client.get("/chat").status_code, 302)

    def test_an_optional_requirement_can_be_skipped_until_next_login(self):
        optional = account_requirements.Requirement(
            key="borough", version=1, title="Your borough", why="To find your local housing court.",
            template="_requirement_profile.html", required=False,
            is_met=lambda m: bool((m or {}).get("borough")), can_collect=lambda: True,
            collect=lambda form, today: account_requirements.Collected({"borough": form.get("borough")}),
        )
        with mock.patch.object(account_requirements, "REQUIREMENTS", (optional,)):
            client = _signed_in(FakeSupabase(), **{account_requirements.SESSION_KEY: {
                "v": account_requirements.REQUIREMENTS_VERSION, "missing": ["borough"]}})
            body = client.get("/account/complete").get_data(as_text=True)
            self.assertIn("Remind me next time", body)
            response = client.post("/account/complete", data={"action": "skip", "next": "/chat"})
            self.assertTrue(response.headers["Location"].endswith("/chat"))
            self.assertEqual(client.get("/chat").status_code, 200)

    def test_next_must_stay_on_this_site(self):
        client = self._pending_client(FakeSupabase())
        for evil in ("https://evil.example", "//evil.example", "/\\evil.example"):
            with self.subTest(evil=evil):
                response = client.post("/account/complete", data={**FORM, "action": "save", "next": evil})
                self.assertTrue(response.headers["Location"].endswith("/chat"), response.headers["Location"])
                with client.session_transaction() as sess:
                    sess[account_requirements.SESSION_KEY] = {
                        "v": account_requirements.REQUIREMENTS_VERSION, "missing": ["profile"]}

    def test_nothing_pending_goes_straight_on(self):
        response = _signed_in(FakeSupabase()).get("/account/complete?next=/settings")
        self.assertTrue(response.headers["Location"].endswith("/settings"))

    def test_logged_out_goes_to_login(self):
        response = _app(FakeSupabase()).get("/account/complete")
        self.assertTrue(response.headers["Location"].endswith("/login"))


class ModuleTests(_Keys):
    def test_a_check_that_raises_counts_as_met(self):
        broken = account_requirements.Requirement(
            key="x", version=1, title="x", why="x", template="_requirement_profile.html", required=True,
            is_met=lambda m: 1 / 0, can_collect=lambda: True, collect=lambda f, t: None)
        with mock.patch.object(account_requirements, "REQUIREMENTS", (broken,)):
            self.assertEqual(account_requirements.missing({}), [])

    def test_every_requirement_has_its_partial_and_a_unique_key(self):
        keys = [r.key for r in account_requirements.REQUIREMENTS]
        self.assertEqual(len(keys), len(set(keys)))
        for requirement in account_requirements.REQUIREMENTS:
            with self.subTest(key=requirement.key):
                self.assertTrue(os.path.exists(os.path.join(TEMPLATES, requirement.template)))
                self.assertTrue(requirement.why.strip())


if __name__ == "__main__":
    unittest.main()
