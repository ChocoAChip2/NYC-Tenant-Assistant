"""Tests for the sign-up profile (name + date of birth) and the browser-matched theme.

Sign-up now asks for first name, last name and date of birth, checks the
tenant is at least 13, and stores the three fields ENCRYPTED in the new
account's metadata (never in the clear -- if encryption isn't configured,
nothing is stored). The first name greets the tenant in chat.

Every page follows the browser's light/dark preference, with dark being
AMOLED black. See log/2026-09-30-signup-profile-and-theme.txt.
"""

import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-for-signup-profile")

import json
import re
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import flask

import crypto_service
import profile_service
from routes import main_bp
from tests.app_test_support import SIGNUP_PROFILE, configure_test_app
from tests.test_conversation_create_rename_and_cleanup import FakeSupabaseService as ChatFakeSupabase
from tests.test_conversation_create_rename_and_cleanup import _logged_in_session
from tests.test_crypto_service import KEY_A, _configured
from tests.test_site_sweep_fixes import _contrast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")
PAGES = sorted(n for n in os.listdir(TEMPLATES) if n.endswith(".html") and not n.startswith("_"))
TODAY = date(2026, 9, 30)


def _read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
        return handle.read()


class _Keys(unittest.TestCase):
    """Runs each test with encryption switched on."""

    def setUp(self):
        patcher = _configured(f"k1:{KEY_A}", "k1")
        # Cleanups run last-in-first-out: the env must be restored BEFORE
        # the keys are reloaded, or this test's key leaks into the next.
        self.addCleanup(crypto_service.reload_keys)
        patcher.start()
        self.addCleanup(patcher.stop)
        crypto_service.reload_keys()


class NameValidationTests(unittest.TestCase):
    def _ok(self, first, last="Rivera"):
        return profile_service.validate(first, last, "1990-05-17", today=TODAY)

    def test_real_names_in_any_script_are_accepted(self):
        for name in ("Ana", "José", "Mary-Jane", "O'Brien", "D’Angelo", "Nguyễn", "محمد", "李", "Jr.",
                     "St. John", "Anne Marie"):
            with self.subTest(name=name):
                self.assertEqual(self._ok(name).first_name, name)

    def test_extra_spaces_are_collapsed(self):
        self.assertEqual(self._ok("  Ana   Maria ").first_name, "Ana Maria")

    def test_digits_symbols_and_markup_are_refused(self):
        for name in ("Ana1", "<script>", "a@b", "Ana_", "--", "'", "Ana!!", "https://x"):
            with self.subTest(name=name), self.assertRaises(profile_service.ProfileError):
                self._ok(name)

    def test_empty_and_too_long_are_refused_with_the_field_named(self):
        with self.assertRaisesRegex(profile_service.ProfileError, "first name"):
            self._ok("   ")
        with self.assertRaisesRegex(profile_service.ProfileError, "last name"):
            self._ok("Ana", "x" * 51)
        self.assertEqual(len(self._ok("x" * 50).first_name), 50)


class DateOfBirthTests(unittest.TestCase):
    def _check(self, dob, today=TODAY):
        return profile_service.validate("Ana", "Rivera", dob, today=today)

    def test_turning_13_today_is_allowed_and_the_day_before_is_not(self):
        self.assertEqual(self._check("2013-09-30").date_of_birth, date(2013, 9, 30))
        with self.assertRaisesRegex(profile_service.ProfileError, "at least 13"):
            self._check("2013-10-01")

    def test_future_impossible_and_missing_dates_are_refused(self):
        for dob in ("2030-01-01", "2026-10-01", "1990-02-30", "not a date", "", "05/17/1990"):
            with self.subTest(dob=dob), self.assertRaises(profile_service.ProfileError):
                self._check(dob)

    def test_implausibly_old_is_refused(self):
        with self.assertRaises(profile_service.ProfileError):
            self._check("1900-01-01")
        self.assertTrue(self._check("1906-10-01"))

    def test_leap_day_birthdays(self):
        # Born Feb 29 2012: turns 13 on Mar 1 2025 in a non-leap year.
        with self.assertRaises(profile_service.ProfileError):
            self._check("2012-02-29", today=date(2025, 2, 28))
        self._check("2012-02-29", today=date(2025, 3, 1))

    def test_latest_allowed_birthday_on_a_leap_day(self):
        self.assertEqual(profile_service.latest_allowed_birthday(date(2028, 2, 29)), date(2015, 2, 28))
        self.assertEqual(profile_service.latest_allowed_birthday(TODAY), date(2013, 9, 30))


class MetadataTests(_Keys):
    def _profile(self):
        return profile_service.validate("José", "O'Brien", "1990-05-17", today=TODAY)

    def test_metadata_is_one_encrypted_envelope_with_no_plaintext(self):
        metadata = profile_service.to_metadata(self._profile())
        self.assertEqual(set(metadata), {"profile"})
        self.assertTrue(crypto_service.is_encrypted(metadata["profile"]))
        dumped = json.dumps(metadata, ensure_ascii=False)
        for secret in ("José", "Brien", "1990", "05-17"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(json.loads(crypto_service.decrypt(metadata["profile"])),
                         {"first_name": "José", "last_name": "O'Brien", "date_of_birth": "1990-05-17"})

    def test_first_name_round_trips(self):
        self.assertEqual(profile_service.first_name_from_metadata(profile_service.to_metadata(self._profile())), "José")

    def test_first_name_from_garbage_is_none_and_never_raises(self):
        for metadata in (None, {}, [], "x", {"profile": None}, {"profile": "José"}, {"profile": "enc:v1:k1:AAAA"},
                         {"profile": crypto_service.encrypt("not json")},
                         {"profile": crypto_service.encrypt(json.dumps({"first_name": 5}))},
                         {"profile": crypto_service.encrypt(json.dumps({"first_name": "  "}))}):
            with self.subTest(metadata=metadata):
                self.assertIsNone(profile_service.first_name_from_metadata(metadata))

    def test_nothing_is_stored_when_encryption_is_off(self):
        with mock.patch.dict(os.environ, {"DATA_ENCRYPTION_KEYS": "", "DATA_ENCRYPTION_ACTIVE_KEY_ID": ""}):
            crypto_service.reload_keys()
            self.assertIsNone(profile_service.to_metadata(self._profile()))

    def test_nothing_is_stored_if_encrypt_ever_returns_plaintext(self):
        with mock.patch.object(crypto_service, "encrypt", side_effect=lambda text: text):
            self.assertIsNone(profile_service.to_metadata(self._profile()))


class FakeSupabase:
    def __init__(self, created=True, user_metadata=None):
        self.created = created
        self.sign_up_calls = []
        self.user_metadata = user_metadata

    def sign_up(self, email, password, email_redirect_to=None, metadata=None):
        self.sign_up_calls.append({"email": email, "password": password, "metadata": metadata})
        return self.created

    def sign_in(self, email, password):
        user = SimpleNamespace(email=email, id="user-1", user_metadata=self.user_metadata)
        return SimpleNamespace(user=user, session=SimpleNamespace(access_token="a", refresh_token="r"))


class FakeAI:
    def is_ready(self):
        return True


def _app(service):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app


class SignupRouteTests(_Keys):
    def setUp(self):
        super().setUp()
        breach = mock.patch("password_safety.is_breached", return_value=False)
        breach.start()
        self.addCleanup(breach.stop)

    def _post(self, service=None, **overrides):
        service = service or FakeSupabase()
        data = {"email": "ana@example.com", "password": "correct horse battery", **SIGNUP_PROFILE, **overrides}
        return _app(service).test_client().post("/signup", data=data), service

    def test_profile_reaches_supabase_encrypted(self):
        response, service = self._post()
        self.assertEqual(response.status_code, 302)
        metadata = service.sign_up_calls[0]["metadata"]
        self.assertTrue(crypto_service.is_encrypted(metadata["profile"]))
        self.assertNotIn("Rivera", json.dumps(metadata))
        self.assertEqual(profile_service.first_name_from_metadata(metadata), "Ana")

    def test_under_13_never_creates_an_account(self):
        young = (date.today().replace(day=1).replace(year=date.today().year - 10)).isoformat()
        response, service = self._post(date_of_birth=young)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.sign_up_calls, [])
        self.assertIn("at least 13", response.get_data(as_text=True))

    def test_missing_name_never_creates_an_account(self):
        response, service = self._post(first_name="")
        self.assertEqual(service.sign_up_calls, [])
        self.assertIn("Please enter your first name.", response.get_data(as_text=True))

    def test_errors_keep_what_was_typed_except_the_password(self):
        response, _ = self._post(last_name="R1vera")
        body = response.get_data(as_text=True)
        self.assertIn('value="Ana"', body)
        self.assertIn('value="R1vera"', body)
        self.assertIn('value="1990-05-17"', body)
        self.assertIn('value="ana@example.com"', body)
        self.assertNotIn("correct horse battery", body)

    def test_typed_values_are_escaped_when_echoed(self):
        body = self._post(first_name='"><script>x</script>')[0].get_data(as_text=True)
        self.assertNotIn("<script>x</script>", body)

    def test_existing_account_keeps_the_form_too(self):
        body = self._post(FakeSupabase(created=False))[0].get_data(as_text=True)
        self.assertIn("already exists", body.lower())
        self.assertIn('value="Ana"', body)

    def test_page_explains_confidentiality_and_limits_the_date_picker(self):
        body = _app(FakeSupabase()).test_client().get("/signup").get_data(as_text=True)
        self.assertIn("Your details stay confidential.", body)
        self.assertIn("Encrypted and never shared.", body)
        latest = profile_service.latest_allowed_birthday(date.today()).isoformat()
        self.assertIn(f'max="{latest}"', body)
        for field, hint in (("first_name", "given-name"), ("last_name", "family-name"), ("date_of_birth", "bday")):
            self.assertRegex(body, rf'name="{field}"[^>]*autocomplete="{hint}"[^>]*required')


class GreetingTests(_Keys):
    def _login(self, user_metadata):
        client = _app(FakeSupabase(user_metadata=user_metadata)).test_client()
        client.post("/login", data={"email": "ana.r@example.com", "password": "pw"})
        with client.session_transaction() as session:
            return session.get("first_name")

    def test_login_puts_the_first_name_in_the_session(self):
        metadata = profile_service.to_metadata(profile_service.validate("Ana", "Rivera", "1990-05-17", today=TODAY))
        self.assertEqual(self._login(metadata), "Ana")

    def test_accounts_without_a_profile_log_in_normally(self):
        self.assertIsNone(self._login(None))
        self.assertIsNone(self._login({"profile": "garbage"}))

    def test_chat_greets_by_first_name_in_the_page_and_its_script(self):
        client = _app(ChatFakeSupabase()).test_client()
        _logged_in_session(client)
        with client.session_transaction() as session:
            session["first_name"] = "Ana"
        body = client.get("/chat").get_data(as_text=True)
        self.assertIn(">Welcome, Ana</h1>", body)
        self.assertIn('const firstName = "Ana";', body)

    def test_chat_falls_back_to_the_email_handle(self):
        client = _app(ChatFakeSupabase()).test_client()
        _logged_in_session(client)
        body = client.get("/chat").get_data(as_text=True)
        self.assertIn(">Welcome, Tenant</h1>", body)
        self.assertIn('const firstName = "";', body)


def _block(source, start):
    block = source[source.index(start):]
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block[:block.index("}")]))


class ThemeTests(unittest.TestCase):
    DARK_STARTS = (':root:not([data-theme="light"]) {', ':root[data-theme="dark"] {')

    def test_every_page_follows_the_browser_with_amoled_dark(self):
        for name in PAGES:
            source = _read(name)
            with self.subTest(page=name):
                self.assertIn("@media (prefers-color-scheme: dark)", source)
                self.assertIn('<meta name="color-scheme" content="light dark">', source)
                self.assertIn('<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">', source)
                for start in self.DARK_STARTS:
                    self.assertEqual(_block(source, start)["bg"], "#000000")

    def test_media_block_and_manual_override_agree(self):
        for name in PAGES:
            source = _read(name)
            with self.subTest(page=name):
                self.assertEqual(*(_block(source, start) for start in self.DARK_STARTS))

    def test_a_saved_choice_is_applied_before_the_styles(self):
        for name in PAGES:
            source = _read(name)
            with self.subTest(page=name):
                self.assertLess(source.index('localStorage.getItem("theme")'), source.index("<style>"))

    def test_no_hard_coded_white_text_on_auth_pages(self):
        for name in ("login.html", "signup.html", "forgot_password.html", "reset_password.html"):
            with self.subTest(page=name):
                self.assertNotRegex(_read(name), r"(?<!-)color:\s*#fff(fff)?\s*;")

    def test_dark_text_tokens_meet_aa(self):
        for name in PAGES:
            source = _read(name)
            tokens = _block(source, self.DARK_STARTS[1])
            for fg, bg in (("text", "bg"), ("text", "surface"), ("muted", "bg"), ("muted", "surface"),
                           ("accent", "bg"), ("accent", "surface")):
                if fg in tokens and bg in tokens:
                    with self.subTest(page=name, pair=(fg, bg)):
                        self.assertGreaterEqual(_contrast(tokens[fg], tokens[bg]), 4.5)
            if "on-accent" in tokens:
                with self.subTest(page=name, pair=("on-accent", "accent")):
                    self.assertGreaterEqual(_contrast(tokens["on-accent"], tokens["accent"]), 4.5)


if __name__ == "__main__":
    unittest.main()
