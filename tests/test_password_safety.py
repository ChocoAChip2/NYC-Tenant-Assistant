"""Tests for the breach check that replaces Supabase's paid one.

The behaviour worth defending here is not "does it catch 'password123'".
It is the two softenings, because both are easy to tighten later by
someone who reads the module as a security gate rather than as
defence-in-depth:

  1. It FAILS OPEN. A tenant who cannot make an account because a third
     party API blinked is a worse outcome than a weak password on an
     account that already has rate limiting and an exponential login
     lockout in front of it.
  2. It only blocks the GENUINELY common. Blocking everything ever seen
     once teaches people to fight the form.

No test here touches the network: every one fakes the HTTP layer, because
a suite that needs api.pwnedpasswords.com to be up is a suite that fails
on a plane.
"""

import os
from tests.app_test_support import SIGNUP_PROFILE
import unittest
import urllib.error
from unittest import mock

import password_safety

# SHA-1("password") = 5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8
PASSWORD_PREFIX, PASSWORD_SUFFIX = "5BAA6", "1E4C9B93F3F0682250B6CF8331B7EE68FD8"


# tests/__init__.py turns the check off for the rest of the suite so no test
# reaches the real API. This module tests the check itself, so it turns it
# back on; every network call below is faked.
_ENABLED = mock.patch.dict(os.environ, {"PASSWORD_BREACH_CHECK": "on"})


def setUpModule():
    _ENABLED.start()


def tearDownModule():
    _ENABLED.stop()


def _range_body(entries):
    return "\r\n".join(f"{suffix}:{count}" for suffix, count in entries)


def _fetch_returns(body):
    return mock.patch.object(password_safety, "_fetch_range", return_value=body)


def _fetch_raises(exc):
    return mock.patch.object(password_safety, "_fetch_range", side_effect=exc)


class KAnonymityTests(unittest.TestCase):
    """Only five hex characters may ever leave this process."""

    def test_only_the_first_five_hash_characters_are_sent(self):
        with mock.patch.object(password_safety, "_fetch_range", return_value="") as fetch:
            password_safety.times_breached("password")

        fetch.assert_called_once()
        sent = fetch.call_args[0][0]
        self.assertEqual(sent, PASSWORD_PREFIX)
        self.assertEqual(len(sent), 5)

    def test_the_password_itself_is_never_sent(self):
        with mock.patch.object(password_safety, "_fetch_range", return_value="") as fetch:
            password_safety.times_breached("correct horse battery staple")

        self.assertNotIn("correct", fetch.call_args[0][0])
        self.assertEqual(len(fetch.call_args[0][0]), 5)

    def test_the_suffix_is_matched_locally(self):
        body = _range_body([("0" * 35, 7), (PASSWORD_SUFFIX, 9_659_365), ("F" * 35, 3)])

        with _fetch_returns(body):
            self.assertEqual(password_safety.times_breached("password"), 9_659_365)

    def test_a_prefix_hit_that_is_not_our_suffix_counts_as_not_found(self):
        """Hundreds of other passwords share the prefix. None of them are
        evidence about this one."""
        with _fetch_returns(_range_body([("A" * 35, 500_000), ("B" * 35, 90)])):
            self.assertEqual(password_safety.times_breached("password"), 0)


class FailOpenTests(unittest.TestCase):
    """Unknown is not the same as breached."""

    def test_a_network_error_returns_unknown_not_a_count(self):
        with _fetch_raises(urllib.error.URLError("no route to host")):
            self.assertIsNone(password_safety.times_breached("password"))

    def test_a_timeout_lets_the_password_through(self):
        with _fetch_raises(TimeoutError()):
            self.assertFalse(password_safety.is_breached("password"))

    def test_an_unexpected_exception_lets_the_password_through(self):
        with _fetch_raises(RuntimeError("something odd")):
            self.assertFalse(password_safety.is_breached("password"))

    def test_a_garbage_response_lets_the_password_through(self):
        with _fetch_returns("<html>502 Bad Gateway</html>"):
            self.assertFalse(password_safety.is_breached("password"))

    def test_a_non_numeric_count_lets_the_password_through(self):
        with _fetch_returns(_range_body([(PASSWORD_SUFFIX, "not-a-number")])):
            self.assertFalse(password_safety.is_breached("password"))

    def test_unknown_is_never_silently_read_as_zero_by_callers(self):
        """is_breached exists so no call site can write
        `times_breached(...) > 0` and get a TypeError, or worse, coerce
        None into a decision."""
        with _fetch_raises(TimeoutError()):
            self.assertIsNone(password_safety.times_breached("password"))
            self.assertIs(password_safety.is_breached("password"), False)


class ThresholdTests(unittest.TestCase):
    def test_a_widely_breached_password_is_refused(self):
        with _fetch_returns(_range_body([(PASSWORD_SUFFIX, 9_659_365)])):
            self.assertTrue(password_safety.is_breached("password"))

    def test_a_password_seen_a_handful_of_times_is_allowed(self):
        """Seen once in a decade of breaches is a different risk from being
        on every credential-stuffing list."""
        with _fetch_returns(_range_body([(PASSWORD_SUFFIX, 3)])):
            self.assertFalse(password_safety.is_breached("password"))

    def test_the_threshold_boundary_is_inclusive(self):
        with _fetch_returns(_range_body([(PASSWORD_SUFFIX, password_safety.BREACH_BLOCK_THRESHOLD)])):
            self.assertTrue(password_safety.is_breached("password"))

    def test_the_threshold_stays_in_a_defensible_range(self):
        """A guard against it drifting to 1 (user-hostile) or 1e9 (useless)."""
        self.assertGreaterEqual(password_safety.BREACH_BLOCK_THRESHOLD, 10)
        self.assertLessEqual(password_safety.BREACH_BLOCK_THRESHOLD, 10_000)

    def test_a_password_absent_from_the_corpus_is_allowed(self):
        with _fetch_returns(_range_body([("C" * 35, 12)])):
            self.assertFalse(password_safety.is_breached("a-long-unique-passphrase"))


class RequestShapeTests(unittest.TestCase):
    def test_padding_is_requested_so_response_size_leaks_nothing(self):
        captured = {}

        class FakeResponse:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.headers)
            captured["timeout"] = timeout
            return FakeResponse()

        with mock.patch.object(password_safety.urllib.request, "urlopen", fake_urlopen):
            password_safety.times_breached("password")

        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers.get("Add-padding".lower()), "true")
        self.assertIn("user-agent", headers)

    def test_the_timeout_is_short_enough_to_sit_in_a_signup_request(self):
        self.assertLessEqual(password_safety.REQUEST_TIMEOUT_SECONDS, 5)


class DisableSwitchTests(unittest.TestCase):
    def test_enabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(password_safety.is_enabled())

    def test_can_be_turned_off_for_a_deployment_with_no_egress(self):
        for value in ("off", "0", "false", "no", "OFF"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"PASSWORD_BREACH_CHECK": value}):
                    self.assertFalse(password_safety.is_enabled())

    def test_when_disabled_no_request_is_made_at_all(self):
        with mock.patch.dict(os.environ, {"PASSWORD_BREACH_CHECK": "off"}), mock.patch.object(
            password_safety, "_fetch_range"
        ) as fetch:
            self.assertFalse(password_safety.is_breached("password"))

        fetch.assert_not_called()

    def test_an_empty_password_never_makes_a_request(self):
        with mock.patch.object(password_safety, "_fetch_range") as fetch:
            self.assertIsNone(password_safety.times_breached(""))

        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class RouteIntegrationTests(unittest.TestCase):
    """All three places a password can be set must consult the check.

    Two of them (reset, settings) already had a length rule; a future edit
    that copies one of those blocks without the breach line would leave a
    silent hole, so each route is asserted separately.
    """

    import flask

    _TEMPLATES = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
    )

    class FakeSupabase:
        def __init__(self):
            self.sign_up_calls = []
            self.update_calls = []

        def sign_up(self, email, password, email_redirect_to=None, metadata=None):
            self.sign_up_calls.append((email, password))
            return object()

        def update_account(self, access_token, refresh_token, email=None, password=None):
            self.update_calls.append(password)
            return object()

        def build_user_scoped_client(self, token):
            return object()

        def fetch_all_conversations_with_messages(self, c):
            return []

        def get_pending_account_deletion(self, c, u):
            return None

    class FakeAI:
        def is_ready(self):
            return True

    def _app(self):
        import flask

        from routes import main_bp
        from tests.app_test_support import configure_test_app

        app = flask.Flask(__name__, template_folder=self._TEMPLATES)
        app.secret_key = "t"
        service = self.FakeSupabase()
        app.config["SUPABASE_SERVICE"] = service
        app.config["AI_SERVICE"] = self.FakeAI()
        app.register_blueprint(main_bp)
        configure_test_app(app)
        # No limiter.init_app here: these tests build a bare Flask app the
        # way every other route test in this suite does, so the shared
        # Flask-Limiter singleton is never attached and its decorators are
        # inert. Rate limiting has its own tests.
        return app, service

    BREACHED = _range_body([(PASSWORD_SUFFIX, 9_659_365)])

    def test_signup_refuses_a_breached_password(self):
        app, service = self._app()

        with _fetch_returns(self.BREACHED):
            response = app.test_client().post(
                "/signup", data={"email": "t@example.com", "password": "password", **SIGNUP_PROFILE}, follow_redirects=True
            )

        self.assertIn("appeared in public data breaches", response.get_data(as_text=True))
        self.assertEqual(service.sign_up_calls, [], "Supabase must not be called at all")

    def test_signup_allows_a_password_that_is_not_breached(self):
        app, service = self._app()

        with _fetch_returns(_range_body([("D" * 35, 4)])):
            app.test_client().post(
                "/signup",
                data={"email": "t@example.com", "password": "a-long-unique-passphrase", **SIGNUP_PROFILE},
                follow_redirects=True,
            )

        self.assertEqual(len(service.sign_up_calls), 1)

    def test_signup_still_works_when_the_breach_api_is_down(self):
        """The fail-open rule, end to end through a real route."""
        app, service = self._app()

        with _fetch_raises(TimeoutError()):
            app.test_client().post(
                "/signup", data={"email": "t@example.com", "password": "password", **SIGNUP_PROFILE}, follow_redirects=True
            )

        self.assertEqual(len(service.sign_up_calls), 1)

    def test_settings_password_change_refuses_a_breached_password(self):
        app, service = self._app()
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = "u1"
            session["user_email"] = "t@example.com"
            session["access_token"] = "a"
            session["refresh_token"] = "r"

        with _fetch_returns(self.BREACHED):
            response = client.post(
                "/settings/account",
                data={"password": "password", "confirm_password": "password"},
                follow_redirects=True,
            )

        self.assertIn("appeared in public data breaches", response.get_data(as_text=True))
        self.assertEqual(service.update_calls, [])

    def test_reset_password_refuses_a_breached_password(self):
        app, service = self._app()

        with _fetch_returns(self.BREACHED):
            response = app.test_client().post(
                "/reset-password",
                data={
                    "access_token": "a",
                    "refresh_token": "r",
                    "password": "password",
                    "confirm_password": "password",
                },
                follow_redirects=True,
            )

        self.assertIn("appeared in public data breaches", response.get_data(as_text=True))
        self.assertEqual(service.update_calls, [])
