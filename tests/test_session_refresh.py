"""Sessions older than an hour must keep working (found on the live site, 2026-10-01).

Supabase access tokens expire after an hour; the Flask session doesn't.
/chat returned a 500 ("JWT expired") for anyone who had been signed in
longer than that. routes._keep_session_fresh swaps the token first.
"""

import base64
import json
import time
import unittest

import flask

import routes
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_conversation_create_rename_and_cleanup import FakeSupabaseService
from tests.test_signup_profile_and_theme import TEMPLATES, FakeAI


def _jwt(exp):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': exp, 'sub': 'u1'})}.sig"


class Service(FakeSupabaseService):
    def __init__(self, refreshed=("fresh-access", "fresh-refresh"), fail=False):
        super().__init__()
        self.refreshed, self.fail, self.refresh_calls = refreshed, fail, []
        self.tokens_seen = []

    def refresh_tokens(self, refresh_token):
        self.refresh_calls.append(refresh_token)
        if self.fail:
            raise RuntimeError("refresh token revoked")
        return self.refreshed

    def build_user_scoped_client(self, access_token):
        self.tokens_seen.append(access_token)
        return object()


def _client(service, access_token):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_id="u1", user_email="a@b.co", access_token=access_token, refresh_token="old-refresh")
    return client


class SessionRefreshTests(unittest.TestCase):
    def test_reads_the_expiry_without_verifying(self):
        self.assertEqual(routes._token_expires_at(_jwt(1234567890)), 1234567890)
        for junk in (None, "", "not-a-jwt", "a.b.c", "fake-token"):
            self.assertIsNone(routes._token_expires_at(junk))

    def test_a_fresh_token_is_left_alone(self):
        service = Service()
        response = _client(service, _jwt(time.time() + 3000)).get("/chat")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.refresh_calls, [])

    def test_an_expired_token_is_refreshed_before_the_page_queries(self):
        service = Service()
        client = _client(service, _jwt(time.time() - 60))
        self.assertEqual(client.get("/chat").status_code, 200)
        self.assertEqual(service.refresh_calls, ["old-refresh"])
        self.assertEqual(service.tokens_seen[0], "fresh-access")
        with client.session_transaction() as sess:
            self.assertEqual((sess["access_token"], sess["refresh_token"]), ("fresh-access", "fresh-refresh"))

    def test_a_token_about_to_expire_is_refreshed_too(self):
        service = Service()
        _client(service, _jwt(time.time() + 30)).get("/chat")
        self.assertEqual(len(service.refresh_calls), 1)

    def test_a_dead_refresh_token_logs_out_cleanly_instead_of_a_500(self):
        client = _client(Service(fail=True), _jwt(time.time() - 60))
        response = client.get("/chat")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        with client.session_transaction() as sess:
            self.assertNotIn("user_id", sess)
            self.assertIn("Your session expired", str(sess.get("_flashes")))

    def test_public_pages_carry_on_logged_out(self):
        response = _client(Service(fail=True), _jwt(time.time() - 60)).get("/learn-more")
        self.assertEqual(response.status_code, 200)

    def test_the_chat_api_answers_401_json(self):
        app_client = _client(Service(fail=True), _jwt(time.time() - 60))
        response = app_client.post("/chat/message", json={"conversation_id": "c1", "content": "hi"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
