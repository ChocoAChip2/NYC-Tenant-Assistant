"""The password rule, the forced change for old weak passwords, and emailed sign-in codes.

Everything runs offline against an in-memory stand-in for Supabase
(tests/review_support.MemorySupabase plus the auth calls these features
use). No email is sent and no real account is touched.
"""

import base64
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from flask import Flask

import email_codes
import login_lockout
import password_safety
import routes
from routes import main_bp
from supabase_service import SupabaseService
from tests.app_test_support import SIGNUP_PROFILE, configure_test_app, reset_rate_limits
from tests.review_support import MemorySupabase, ReviewAI

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
STRONG = "Str0ng!pass"
WEAK = "abc123"
EMAIL = "tenant@example.invalid"
CODE = "123456"


def _jwt(expires_at):
    body = base64.urlsafe_b64encode(json.dumps({"exp": expires_at}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class AuthFake(MemorySupabase):
    """MemorySupabase plus sign-in, emailed codes and account updates."""

    def __init__(self, metadata=None):
        super().__init__()
        self.metadata = dict(metadata or {})
        self.codes_sent = []
        self.code_checks = []
        self.ended = []
        self.sign_ups = []
        self.account_updates = []
        self.metadata_updates = []
        self.refreshed = []
        self.send_error = None
        self.code_user_id = "review-user"
        self.code_access_token = "code-access"

    def _response(self, email, access, refresh, user_id="review-user"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, email=email, user_metadata=dict(self.metadata)),
            session=SimpleNamespace(access_token=access, refresh_token=refresh),
        )

    def sign_in(self, email, password):
        if password.startswith("wrong"):
            raise RuntimeError("Invalid login credentials")
        return self._response(email, "pw-access", "pw-refresh")

    def sign_up(self, email, password, email_redirect_to=None, metadata=None):
        self.sign_ups.append((email, password))
        return True

    def send_sign_in_code(self, email):
        if self.send_error:
            raise self.send_error
        self.codes_sent.append(email)

    def verify_sign_in_code(self, email, code):
        self.code_checks.append((email, code))
        if code != CODE:
            raise RuntimeError(f"Token has expired or is invalid: {code}")
        return self._response(email, self.code_access_token, "code-refresh", self.code_user_id)

    def end_session(self, access_token):
        self.ended.append(access_token)

    def get_user_metadata(self, token):
        return dict(self.metadata)

    def update_user_metadata(self, access_token, refresh_token, metadata):
        self.metadata_updates.append((access_token, refresh_token, dict(metadata)))
        self.metadata.update(metadata)
        return None

    def update_account(self, access_token, refresh_token, email=None, password=None):
        self.account_updates.append({"access_token": access_token, "refresh_token": refresh_token,
                                     "email": email, "password": password})

    def refresh_tokens(self, refresh_token):
        self.refreshed.append(refresh_token)
        return _jwt(time.time() + 3600), "rotated-refresh"


class _Case(unittest.TestCase):
    metadata = None

    def setUp(self):
        reset_rate_limits()
        login_lockout.reset_all()
        self.addCleanup(login_lockout.reset_all)
        self.service = AuthFake(self.metadata)
        self.app = Flask(__name__, template_folder=str(_TEMPLATES))
        self.app.secret_key = "test-only"
        self.app.config.update(SUPABASE_SERVICE=self.service, AI_SERVICE=ReviewAI(), TESTING=True)
        configure_test_app(self.app)
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()

    def login(self, password=STRONG, email=EMAIL):
        return self.client.post("/login", data={"email": email, "password": password})

    def session(self):
        with self.client.session_transaction() as session:
            return dict(session)

    def signed_in(self):
        return bool(self.session().get("user_id"))

    def text(self, response):
        return response.get_data(as_text=True)


class PasswordRuleTests(unittest.TestCase):
    def test_each_class_is_required(self):
        self.assertEqual(password_safety.unmet_rules("Abcdef1!"), [])
        for password, missing in (("Abcde1!", ["length"]), ("ABCDEF1!", ["lower"]), ("abcdef1!", ["upper"]),
                                  ("Abcdefg!", ["digit"]), ("Abcdefg1", ["symbol"]),
                                  ("", ["length", "lower", "upper", "digit", "symbol"]),
                                  (None, ["length", "lower", "upper", "digit", "symbol"])):
            with self.subTest(password=password):
                self.assertEqual(password_safety.unmet_rules(password), missing)
                self.assertFalse(password_safety.meets_rules(password))

    def test_length_boundary_is_eight(self):
        self.assertEqual(password_safety.MIN_LENGTH, 8)
        self.assertIn("length", password_safety.unmet_rules("Abcd12!"))
        self.assertTrue(password_safety.meets_rules("Abcd12!x"))
        self.assertTrue(password_safety.meets_rules("Abcd12!x" * 9))

    def test_the_symbol_list_is_the_auth_providers_list_exactly(self):
        self.assertEqual(password_safety.SYMBOLS, "!@#$%^&*()_+-=[]{};'\\:\"|<>?,./`~")
        self.assertEqual(len(set(password_safety.SYMBOLS)), 32)
        for symbol in password_safety.SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertTrue(password_safety.meets_rules("Abcdefg1" + symbol))

    def test_other_characters_are_allowed_but_do_not_count(self):
        for extra in (" ", "£", "é", "É", "٣", "中"):
            with self.subTest(extra=extra):
                self.assertEqual(password_safety.unmet_rules("Abcdefg1" + extra), ["symbol"])
                self.assertTrue(password_safety.meets_rules("Abcdefg1!" + extra))
        self.assertEqual(password_safety.unmet_rules("ÉÉÉÉabc1!"), ["upper"])
        self.assertEqual(password_safety.unmet_rules("ABCDEF!x٣"), ["digit"])

    def test_the_message_names_every_part_of_the_rule(self):
        for word in ("8 characters", "lowercase", "uppercase", "number", "symbol"):
            self.assertIn(word, password_safety.RULE_MESSAGE)
        self.assertEqual([key for key, _ in password_safety.RULES], ["length", "lower", "upper", "digit", "symbol"])


class WeakPasswordIsRefusedEverywhereTests(_Case):
    WEAK_ONES = ("abc", "abcdefgh", "Abcdefg1", "abcdef1!", "ABCDEF1!", "Abcdefg!")

    def test_sign_up_refuses_before_calling_the_auth_service(self):
        for weak in self.WEAK_ONES:
            with self.subTest(weak=weak):
                response = self.client.post("/signup", data={"email": EMAIL, "password": weak, **SIGNUP_PROFILE})
                self.assertIn(password_safety.RULE_MESSAGE, self.text(response))
                self.assertNotIn(weak, self.text(response))
        self.assertEqual(self.service.sign_ups, [])
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            self.client.post("/signup", data={"email": EMAIL, "password": STRONG, **SIGNUP_PROFILE})
        self.assertEqual(self.service.sign_ups, [(EMAIL, STRONG)])

    def test_reset_link_refuses_before_calling_the_auth_service(self):
        for weak in self.WEAK_ONES:
            with self.subTest(weak=weak):
                response = self.client.post("/reset-password", data={
                    "access_token": "a", "refresh_token": "r", "password": weak, "confirm_password": weak})
                self.assertIn(password_safety.RULE_MESSAGE, self.text(response))
                self.assertIn('value="a"', self.text(response), "the reset link's tokens are handed back")
        self.assertEqual(self.service.account_updates, [])

    def test_settings_refuses_before_calling_the_auth_service(self):
        self.login()
        for weak in self.WEAK_ONES:
            with self.subTest(weak=weak):
                response = self.client.post("/settings/account", data={"password": weak, "confirm_password": weak},
                                            follow_redirects=True)
                self.assertIn(password_safety.RULE_MESSAGE, self.text(response))
        self.assertEqual(self.service.account_updates, [])
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            self.client.post("/settings/account", data={"password": "N3w!password", "confirm_password": "N3w!password"})
        self.assertEqual(self.service.account_updates[0]["password"], "N3w!password")

    def test_every_new_password_field_shows_the_rule_and_none_says_six(self):
        self.login()
        pages = {
            "/signup": self.text(self.client.get("/signup")),
            "/reset-password": self.text(self.client.get("/reset-password")),
            "/settings": self.text(self.client.get("/settings")),
        }
        for path, body in pages.items():
            with self.subTest(path=path):
                self.assertIn('minlength="8"', body)
                self.assertIn("data-pw-rules=", body)
                for _, label in password_safety.RULES:
                    self.assertIn(label, body)
                self.assertIn(json.dumps(password_safety.SYMBOLS), body.replace("\\u0027", "'").replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
        for template in _TEMPLATES.glob("*.html"):
            source = template.read_text(encoding="utf-8")
            self.assertNotIn('minlength="6"', source, template.name)
            self.assertNotIn("At least 6 characters", source, template.name)


class ForcedPasswordChangeTests(_Case):
    def test_an_old_weak_password_signs_in_but_must_be_replaced(self):
        response = self.login(WEAK)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/account/password"))
        self.assertTrue(self.signed_in())
        self.assertTrue(self.session()[routes.PASSWORD_CHANGE_KEY])
        page = self.text(self.client.get("/account/password"))
        self.assertIn("Set a new password", page)
        self.assertIn("data-pw-rules=", page)
        self.assertNotIn("Remind me", page)
        self.assertNotIn('value="skip"', page)

    def test_no_signed_in_page_opens_until_it_is_replaced(self):
        self.login(WEAK)
        cid = self.service.create_conversation(self.service, "review-user", "Chat")
        for path in ("/chat", "/settings", "/settings/download-logs", "/account/complete",
                     f"/conversations/{cid}/situation", f"/conversations/{cid}/forms/x"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/account/password", response.location)
        self.assertIn("next=/settings", self.client.get("/settings").location)
        for path, data in (("/conversations", {}), ("/settings/account", {"email": "x@example.invalid"}),
                           ("/settings/sign-in-codes", {"turn": "on"}), (f"/conversations/{cid}/delete", {})):
            with self.subTest(post=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/account/password", response.location)
        for path, payload in (("/chat/message", {"conversation_id": cid, "content": "hi"}),
                              (f"/conversations/{cid}/situation/refresh", {})):
            with self.subTest(json=path):
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 403)
                self.assertIn("new password", response.get_json()["error"])
        self.assertEqual(self.service.messages[cid], [])
        self.assertEqual(self.service.account_updates, [])
        self.assertEqual(self.service.codes_sent, [])
        self.assertIn(cid, self.service.conversations)

    def test_public_pages_and_logout_still_work(self):
        self.login(WEAK)
        for path in ("/learn-more", "/resources", "/login", "/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.post("/logout")
        self.assertTrue(response.location.endswith("/login"))
        self.assertFalse(self.signed_in())
        self.assertNotIn(routes.PASSWORD_CHANGE_KEY, self.session())

    def test_a_weak_or_mismatched_replacement_is_refused(self):
        self.login(WEAK)
        for data, message in (({"password": "abcdefgh", "confirm_password": "abcdefgh"}, password_safety.RULE_MESSAGE),
                              ({"password": STRONG, "confirm_password": STRONG + "x"}, "Passwords do not match."),
                              ({"password": "", "confirm_password": ""}, "Passwords do not match."),
                              ({"action": "skip"}, "Passwords do not match.")):
            with self.subTest(data=data):
                response = self.client.post("/account/password", data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, self.text(response))
        with mock.patch("routes.password_safety.is_breached", return_value=True):
            response = self.client.post("/account/password", data={"password": STRONG, "confirm_password": STRONG})
        self.assertIn(password_safety.MESSAGE, self.text(response))
        self.assertEqual(self.service.account_updates, [])
        self.assertIn("/account/password", self.client.get("/chat").location)

    def test_a_compliant_password_is_saved_and_everything_opens(self):
        self.login(WEAK)
        self.assertIn("/account/password", self.client.get("/settings").location)
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            response = self.client.post("/account/password",
                                        data={"password": STRONG, "confirm_password": STRONG, "next": "/settings"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/settings"))
        self.assertEqual(self.service.account_updates, [
            {"access_token": "pw-access", "refresh_token": "pw-refresh", "email": None, "password": STRONG}])
        self.assertNotIn(routes.PASSWORD_CHANGE_KEY, self.session())
        self.assertEqual(self.client.get("/chat").status_code, 200)
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.assertTrue(self.client.get("/account/password").location.endswith("/chat"))

    def test_a_failed_save_keeps_the_tenant_on_the_page(self):
        self.login(WEAK)
        with mock.patch.object(self.service, "update_account", side_effect=RuntimeError("internal detail")), \
             mock.patch("routes.password_safety.is_breached", return_value=False):
            response = self.client.post("/account/password", data={"password": STRONG, "confirm_password": STRONG})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("internal detail", self.text(response))
        self.assertIn("/account/password", self.client.get("/chat").location)

    def test_the_next_page_cannot_be_another_site(self):
        self.login(WEAK)
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            response = self.client.post("/account/password", data={
                "password": STRONG, "confirm_password": STRONG, "next": "https://evil.example/"})
        self.assertTrue(response.location.endswith("/chat"))

    def test_a_strong_password_is_never_asked_to_change(self):
        response = self.login(STRONG)
        self.assertTrue(response.location.endswith("/chat"))
        self.assertNotIn(routes.PASSWORD_CHANGE_KEY, self.session())
        self.assertTrue(self.client.get("/account/password").location.endswith("/chat"))

    def test_signing_in_again_with_a_strong_password_clears_the_requirement(self):
        self.login(WEAK)
        self.login(STRONG)
        self.assertEqual(self.client.get("/chat").status_code, 200)

    def test_the_page_needs_a_login(self):
        self.assertTrue(self.client.get("/account/password").location.endswith("/login"))
        self.assertTrue(self.client.post("/account/password", data={"password": STRONG, "confirm_password": STRONG})
                        .location.endswith("/login"))


class SignInWithoutCodesTests(_Case):
    def test_sign_in_is_unchanged_and_sends_no_email(self):
        response = self.login()
        self.assertTrue(response.location.endswith("/chat"))
        session = self.session()
        self.assertEqual((session["user_id"], session["user_email"], session["access_token"], session["refresh_token"]),
                         ("review-user", EMAIL, "pw-access", "pw-refresh"))
        self.assertEqual(self.service.codes_sent, [])
        self.assertEqual(self.service.ended, [])
        self.assertNotIn(email_codes.SIGN_IN_KEY, session)

    def test_only_the_exact_switch_turns_codes_on(self):
        for value in (None, False, "true", 1, "on"):
            with self.subTest(value=value):
                self.assertFalse(email_codes.is_on({email_codes.METADATA_KEY: value}))
        self.assertFalse(email_codes.is_on(None))
        self.assertFalse(email_codes.is_on(mock.MagicMock()))
        self.assertTrue(email_codes.is_on({email_codes.METADATA_KEY: True}))

    def test_the_code_page_is_not_reachable_without_a_pending_sign_in(self):
        self.assertTrue(self.client.get("/login/code").location.endswith("/login"))
        self.assertTrue(self.client.post("/login/code", data={"code": CODE}).location.endswith("/login"))
        self.login()
        self.assertTrue(self.client.get("/login/code").location.endswith("/login"))
        self.assertEqual(self.service.code_checks, [])


class CodeSignInTests(_Case):
    metadata = {email_codes.METADATA_KEY: True}

    def test_a_correct_password_sends_one_code_and_does_not_sign_in(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login/code"))
        self.assertEqual(self.service.codes_sent, [EMAIL])
        session = self.session()
        for key in ("user_id", "access_token", "refresh_token", "user_email"):
            self.assertNotIn(key, session)
        self.assertNotIn("pw-access", json.dumps(session))
        self.assertEqual(self.service.ended, ["pw-access"])
        pending = session[email_codes.SIGN_IN_KEY]
        self.assertEqual((pending["email"], pending["user_id"], pending["weak"]), (EMAIL, "review-user", False))
        page = self.text(self.client.get("/login/code"))
        self.assertIn(EMAIL, page)
        self.assertIn('autocomplete="one-time-code"', page)
        self.assertIn('inputmode="numeric"', page)
        self.assertIn("send a new code", page)

    def test_signed_in_pages_stay_closed_until_the_right_code(self):
        self.login()
        cid = self.service.create_conversation(self.service, "review-user", "Chat")
        for path in ("/chat", "/settings", "/settings/download-logs", "/account/password", "/account/complete"):
            with self.subTest(path=path):
                self.assertTrue(self.client.get(path).location.endswith("/login"))
        self.assertEqual(self.client.post("/chat/message", json={"conversation_id": cid, "content": "hi"}).status_code, 401)
        self.assertEqual(self.client.get(f"/conversations/{cid}/situation").status_code, 401)
        self.client.post("/login/code", data={"code": "000000"})
        self.assertTrue(self.client.get("/chat").location.endswith("/login"))

    def test_the_right_code_signs_in_with_the_codes_own_session(self):
        self.login()
        response = self.client.post("/login/code", data={"code": CODE})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/chat"))
        session = self.session()
        self.assertEqual((session["user_id"], session["user_email"], session["access_token"], session["refresh_token"]),
                         ("review-user", EMAIL, "code-access", "code-refresh"))
        self.assertNotIn(email_codes.SIGN_IN_KEY, session)
        self.assertEqual(self.service.code_checks, [(EMAIL, CODE)])
        self.assertEqual(self.client.get("/chat").status_code, 200)

    def test_spaces_and_dashes_in_the_code_are_forgiven(self):
        self.login()
        self.assertTrue(self.client.post("/login/code", data={"code": " 123-456 "}).location.endswith("/chat"))

    def test_a_malformed_code_is_not_a_guess(self):
        self.login()
        for bad in ("", "12345", "1234567", "abcdef", "12 34", "１２３４５６"):
            with self.subTest(bad=bad):
                response = self.client.post("/login/code", data={"code": bad})
                self.assertEqual(response.status_code, 200)
                self.assertIn("Enter the 6-digit code", self.text(response))
        self.assertEqual(self.service.code_checks, [])
        self.assertEqual(login_lockout._STORE, {})
        self.assertFalse(self.signed_in())

    def test_wrong_codes_count_toward_the_same_lockout_as_wrong_passwords(self):
        self.login("wrong-password")
        self.assertEqual(login_lockout._STORE["127.0.0.1"]["failures"], 1)
        self.login()
        reset_rate_limits()
        for attempt in range(login_lockout.MAX_FAILED_ATTEMPTS - 1):
            response = self.client.post("/login/code", data={"code": "000000"})
            self.assertFalse(self.signed_in())
        self.assertIn("Too many failed login attempts", self.text(response))
        self.assertGreater(login_lockout.seconds_until_unlocked("127.0.0.1"), 0)
        self.assertEqual(list(login_lockout._STORE), ["127.0.0.1"])
        checks = len(self.service.code_checks)
        response = self.client.post("/login/code", data={"code": CODE})
        self.assertIn("Too many failed login attempts", self.text(response))
        self.assertEqual(len(self.service.code_checks), checks, "a locked-out caller's code is not even checked")
        self.assertFalse(self.signed_in())
        self.assertIn("Too many failed login attempts", self.text(self.login()))

    def test_a_wrong_code_says_so_and_a_right_one_clears_the_streak(self):
        self.login()
        response = self.client.post("/login/code", data={"code": "000000"})
        self.assertIn("That code didn't work", self.text(response).replace("&#39;", "'"))
        self.assertEqual(login_lockout._STORE["127.0.0.1"]["failures"], 1)
        self.client.post("/login/code", data={"code": CODE})
        self.assertEqual(login_lockout._STORE["127.0.0.1"]["failures"], 0)

    def test_a_correct_password_alone_does_not_clear_the_streak(self):
        for _ in range(3):
            self.login("wrong-password")
        self.login()
        self.assertEqual(login_lockout._STORE["127.0.0.1"]["failures"], 3)

    def test_send_a_new_code_respects_the_cooldown_and_says_so(self):
        self.login()
        response = self.client.post("/login/code", data={"action": "resend"})
        self.assertRegex(self.text(response), r"You can send a new code in \d+ seconds?\.")
        self.assertEqual(self.service.codes_sent, [EMAIL])
        with self.client.session_transaction() as session:
            pending = dict(session[email_codes.SIGN_IN_KEY])
            pending["sent_at"] = time.time() - email_codes.RESEND_SECONDS - 1
            session[email_codes.SIGN_IN_KEY] = pending
        response = self.client.post("/login/code", data={"action": "resend"})
        self.assertIn("We sent a new code", self.text(response))
        self.assertEqual(self.service.codes_sent, [EMAIL, EMAIL])
        self.assertRegex(self.text(self.client.post("/login/code", data={"action": "resend"})), r"in \d+ seconds?\.")
        self.assertEqual(len(self.service.codes_sent), 2)
        self.assertFalse(self.signed_in())

    def test_cooldown_arithmetic(self):
        self.assertEqual(email_codes.seconds_until_resend(1000, now=1000), 60)
        self.assertEqual(email_codes.seconds_until_resend(1000, now=1059.2), 1)
        self.assertEqual(email_codes.seconds_until_resend(1000, now=1060), 0)
        self.assertEqual(email_codes.seconds_until_resend(None), 0)
        self.assertEqual(email_codes.RESEND_SECONDS, 60)

    def test_a_stale_pending_sign_in_goes_back_to_login(self):
        self.login()
        with self.client.session_transaction() as session:
            pending = dict(session[email_codes.SIGN_IN_KEY])
            pending["at"] = time.time() - email_codes.PENDING_SECONDS - 1
            session[email_codes.SIGN_IN_KEY] = pending
        response = self.client.post("/login/code", data={"code": CODE})
        self.assertTrue(response.location.endswith("/login"))
        self.assertEqual(self.service.code_checks, [])
        self.assertFalse(self.signed_in())
        self.assertNotIn(email_codes.SIGN_IN_KEY, self.session())

    def test_a_code_session_for_a_different_account_is_refused(self):
        self.login()
        self.service.code_user_id = "someone-else"
        self.client.post("/login/code", data={"code": CODE})
        session = self.session()
        self.assertFalse(session.get("user_id"))
        self.assertNotIn("access_token", session)
        self.assertEqual(login_lockout._STORE["127.0.0.1"]["failures"], 1)

    def test_if_the_code_cannot_be_sent_nobody_is_signed_in(self):
        self.service.send_error = RuntimeError("smtp down: internal detail")
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = self.text(response).replace("&#39;", "'")
        self.assertIn("We couldn't send your sign-in code", body)
        self.assertNotIn("internal detail", body)
        self.assertFalse(self.signed_in())
        self.assertNotIn(email_codes.SIGN_IN_KEY, self.session())
        self.assertEqual(self.service.ended, ["pw-access"])

    def test_a_wrong_password_never_sends_a_code(self):
        self.login("wrong-password")
        self.assertEqual(self.service.codes_sent, [])
        self.assertNotIn(email_codes.SIGN_IN_KEY, self.session())

    def test_starting_a_code_sign_in_ends_any_session_already_in_the_browser(self):
        self.service.metadata = {}
        self.login()
        self.assertTrue(self.signed_in())
        self.service.metadata = {email_codes.METADATA_KEY: True}
        self.login()
        self.assertFalse(self.signed_in())
        self.assertNotIn("access_token", self.session())

    def test_order_is_password_then_code_then_forced_change(self):
        response = self.login(WEAK)
        self.assertTrue(response.location.endswith("/login/code"))
        self.assertFalse(self.signed_in())
        self.assertNotIn(routes.PASSWORD_CHANGE_KEY, self.session())
        response = self.client.post("/login/code", data={"code": CODE})
        self.assertTrue(response.location.endswith("/account/password"))
        self.assertTrue(self.signed_in())
        self.assertIn("/account/password", self.client.get("/chat").location)
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            self.client.post("/account/password", data={"password": STRONG, "confirm_password": STRONG})
        self.assertEqual(self.service.account_updates[0]["access_token"], "code-access")
        self.assertEqual(self.client.get("/chat").status_code, 200)

    def test_codes_and_tokens_are_never_logged(self):
        with self.assertLogs("routes", level="DEBUG") as logs:
            self.login()
            self.client.post("/login/code", data={"code": "654321"})
            self.service.send_error = RuntimeError("boom 654321")
            with self.client.session_transaction() as session:
                pending = dict(session[email_codes.SIGN_IN_KEY])
                pending["sent_at"] = 0
                session[email_codes.SIGN_IN_KEY] = pending
            self.client.post("/login/code", data={"action": "resend"})
        output = "\n".join(logs.output)
        for secret in ("654321", "pw-access", "pw-refresh", "code-access", EMAIL):
            self.assertNotIn(secret, output)

    def test_session_refresh_after_an_hour_still_works_for_a_code_sign_in(self):
        self.service.code_access_token = _jwt(time.time() + 30)
        self.login()
        self.client.post("/login/code", data={"code": CODE})
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.assertEqual(self.service.refreshed, ["code-refresh"])
        session = self.session()
        self.assertEqual(session["refresh_token"], "rotated-refresh")
        self.assertEqual(session["user_id"], "review-user")
        self.assertEqual(self.client.get("/chat").status_code, 200)
        self.assertEqual(self.service.refreshed, ["code-refresh"])


class SettingsSignInCodesTests(_Case):
    def setUp(self):
        super().setUp()
        self.login()

    def page(self):
        return self.text(self.client.get("/settings")).replace("&#39;", "'")

    def expire_cooldown(self):
        with self.client.session_transaction() as session:
            change = dict(session[email_codes.SETTINGS_KEY])
            change["sent_at"] = time.time() - email_codes.RESEND_SECONDS - 1
            session[email_codes.SETTINGS_KEY] = change

    def test_off_by_default_with_a_way_to_turn_it_on(self):
        page = self.page()
        self.assertIn('id="sign-in-codes"', page)
        self.assertIn("<strong>Off.</strong>", page)
        self.assertIn("Turn on sign-in codes", page)
        self.assertNotIn('name="code"', page)

    def test_turning_it_on_needs_a_code(self):
        response = self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.assertTrue(response.location.endswith("/settings#sign-in-codes"))
        self.assertEqual(self.service.codes_sent, [EMAIL])
        self.assertEqual(self.service.metadata_updates, [])
        page = self.page()
        self.assertIn('name="code"', page)
        self.assertIn('autocomplete="one-time-code"', page)
        self.assertIn("to turn sign-in codes on", page)
        self.client.post("/settings/sign-in-codes/confirm", data={"code": "000000"})
        self.assertEqual(self.service.metadata_updates, [])
        self.assertIn("That code didn't work", self.page())
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.assertEqual(self.service.metadata_updates, [("code-access", "code-refresh", {email_codes.METADATA_KEY: True})])
        self.assertEqual(self.session()["access_token"], "code-access")
        page = self.page()
        self.assertIn("<strong>On.</strong>", page)
        self.assertIn("Turn off sign-in codes", page)
        self.assertNotIn(email_codes.SETTINGS_KEY, self.session())

    def test_turning_it_off_needs_a_code(self):
        self.service.metadata[email_codes.METADATA_KEY] = True
        self.client.post("/settings/sign-in-codes", data={"turn": "off"})
        self.assertEqual(self.service.codes_sent, [EMAIL])
        self.assertTrue(self.service.metadata[email_codes.METADATA_KEY])
        self.assertIn("to turn sign-in codes off", self.page())
        self.client.post("/settings/sign-in-codes/confirm", data={"code": "999999"})
        self.assertTrue(self.service.metadata[email_codes.METADATA_KEY])
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.assertIs(self.service.metadata[email_codes.METADATA_KEY], False)
        self.assertIn("<strong>Off.</strong>", self.page())

    def test_there_is_no_way_to_change_it_without_a_code(self):
        self.service.metadata[email_codes.METADATA_KEY] = True
        for data in ({"code": CODE}, {"code": CODE, "enable": "false", "turn": "off"}, {}):
            self.client.post("/settings/sign-in-codes/confirm", data=data)
        self.client.post("/settings/sign-in-codes", data={"turn": "sideways"})
        self.client.get("/settings/sign-in-codes?turn=off")
        self.assertEqual(self.service.metadata_updates, [])
        self.assertEqual(self.service.code_checks, [])
        self.assertEqual(self.service.codes_sent, [])

    def test_five_wrong_codes_end_the_change(self):
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        for _ in range(email_codes.MAX_SETTINGS_ATTEMPTS):
            self.client.post("/settings/sign-in-codes/confirm", data={"code": "000000"})
        self.assertNotIn(email_codes.SETTINGS_KEY, self.session())
        self.assertIn("Too many wrong codes", self.page())
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.assertEqual(self.service.metadata_updates, [])

    def test_resend_respects_the_cooldown_and_cancel_drops_it(self):
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.assertEqual(len(self.service.codes_sent), 1)
        self.assertRegex(self.page(), r"You can send a new code in \d+ seconds?\.")
        self.expire_cooldown()
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.assertEqual(len(self.service.codes_sent), 2)
        self.client.post("/settings/sign-in-codes/confirm", data={"action": "cancel"})
        self.assertNotIn(email_codes.SETTINGS_KEY, self.session())
        self.assertIn("Turn on sign-in codes", self.page())
        self.assertEqual(self.service.metadata_updates, [])

    def test_a_code_for_another_account_changes_nothing(self):
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.service.code_user_id = "someone-else"
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.assertEqual(self.service.metadata_updates, [])
        self.assertEqual(self.session()["access_token"], "pw-access")

    def test_a_stale_change_needs_a_new_code(self):
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        with self.client.session_transaction() as session:
            change = dict(session[email_codes.SETTINGS_KEY])
            change["at"] = time.time() - email_codes.PENDING_SECONDS - 1
            session[email_codes.SETTINGS_KEY] = change
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.assertEqual(self.service.metadata_updates, [])
        self.assertEqual(self.service.code_checks, [])

    def test_send_and_save_failures_are_plain_and_change_nothing(self):
        self.service.send_error = RuntimeError("internal detail")
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        page = self.page()
        self.assertIn("We couldn't send a code", page)
        self.assertNotIn("internal detail", page)
        self.assertNotIn(email_codes.SETTINGS_KEY, self.session())
        self.service.send_error = None
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        with mock.patch.object(self.service, "update_user_metadata", side_effect=RuntimeError("internal detail")):
            self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        page = self.page()
        self.assertIn("We couldn't save that just now", page)
        self.assertNotIn("internal detail", page)
        self.assertNotIn(email_codes.METADATA_KEY, self.service.metadata)

    def test_unknown_status_offers_no_switch(self):
        with mock.patch.object(self.service, "get_user_metadata", side_effect=RuntimeError("auth down")):
            page = self.page()
        self.assertIn("We couldn't check this setting right now", page)
        self.assertNotIn("Turn on sign-in codes", page)
        self.assertNotIn("Turn off sign-in codes", page)

    def test_both_routes_need_a_login(self):
        self.client.post("/logout")
        for path in ("/settings/sign-in-codes", "/settings/sign-in-codes/confirm"):
            self.assertTrue(self.client.post(path, data={"turn": "on", "code": CODE}).location.endswith("/login"))
        self.assertEqual(self.service.codes_sent, [])

    def test_once_on_the_next_sign_in_asks_for_a_code(self):
        self.client.post("/settings/sign-in-codes", data={"turn": "on"})
        self.client.post("/settings/sign-in-codes/confirm", data={"code": CODE})
        self.client.post("/logout")
        self.assertTrue(self.login().location.endswith("/login/code"))


class RouteLimitsTests(unittest.TestCase):
    def test_code_routes_carry_the_login_limit(self):
        import inspect

        source = inspect.getsource(routes)
        for route in ('@main_bp.route("/login/code", methods=["GET", "POST"])\n@limiter.limit("10 per minute", methods=["POST"])',
                      '@main_bp.route("/account/password", methods=["GET", "POST"])\n@limiter.limit("10 per minute", methods=["POST"])',
                      '@main_bp.route("/settings/sign-in-codes", methods=["POST"])\n@limiter.limit("10 per minute")',
                      '@main_bp.route("/settings/sign-in-codes/confirm", methods=["POST"])\n@limiter.limit("10 per minute")'):
            self.assertIn(route, source)

    def test_new_signed_in_routes_are_closed_by_default_while_a_password_is_owed(self):
        open_endpoints = routes._OPEN_WITHOUT_NEW_PASSWORD
        for endpoint in ("main.chat", "main.settings", "main.chat_message", "main.complete_account",
                         "main.download_chat_history", "main.situation", "main.refresh_situation", "main.review_form",
                         "main.request_sign_in_code_change", "main.confirm_sign_in_code_change", "main.update_account"):
            self.assertNotIn(endpoint, open_endpoints)
        app = Flask(__name__)
        app.register_blueprint(main_bp)
        known = {rule.endpoint for rule in app.url_map.iter_rules()}
        self.assertLessEqual(open_endpoints, known, "every open endpoint must be a real route")


class SupabaseCodeCallsTests(unittest.TestCase):
    def setUp(self):
        self.shared, self.isolated = mock.MagicMock(), mock.MagicMock()
        self.service = SupabaseService(client=self.shared)
        patcher = mock.patch("supabase_service.create_client", return_value=self.isolated)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def test_sending_a_code_never_creates_an_account(self):
        self.service.send_sign_in_code(EMAIL)
        self.isolated.auth.sign_in_with_otp.assert_called_once_with(
            {"email": EMAIL, "options": {"should_create_user": False}})
        self.shared.auth.sign_in_with_otp.assert_not_called()

    def test_verifying_uses_an_email_code_on_an_isolated_client(self):
        result = self.service.verify_sign_in_code(EMAIL, CODE)
        self.isolated.auth.verify_otp.assert_called_once_with({"email": EMAIL, "token": CODE, "type": "email"})
        self.assertIs(result, self.isolated.auth.verify_otp.return_value)
        self.shared.auth.verify_otp.assert_not_called()
        options = self.factory.call_args.kwargs["options"]
        self.assertFalse(options.persist_session)
        self.assertFalse(options.auto_refresh_token)

    def test_ending_a_session_revokes_only_that_session(self):
        self.service.end_session("pw-access")
        self.isolated.auth.admin.sign_out.assert_called_once_with("pw-access", "local")
        self.service.end_session("")
        self.assertEqual(self.isolated.auth.admin.sign_out.call_count, 1)


if __name__ == "__main__":
    unittest.main()
