"""Regression tests for the defects the 2026-09-29 browser sweep found.

Each class names the defect it pins down. The sweep ran the real
create_app() in Chromium with every external service faked; see
log/2026-09-29-site-sweep-fixes.txt.
"""

import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-for-site-sweep-tests")

import re
import unittest
from unittest import mock

import flask

import branding
from app import create_app
from rate_limit import limiter
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_conversation_create_rename_and_cleanup import FakeSupabaseService as ChatFakeSupabase
from tests.test_conversation_create_rename_and_cleanup import _logged_in_session
from tests.test_password_reset import FakeSupabaseService as ResetFakeSupabase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")


class FakeAIService:
    def is_ready(self):
        return True


def _bare_app(service):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = FakeAIService()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app


def _read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
        return handle.read()


class ResetPasswordTypoKeepsTheLinkTests(unittest.TestCase):
    """A mismatch, a short or a breached password used to re-render the page
    without the recovery tokens, so the form vanished behind "This reset
    link is invalid or has expired" and the tenant needed a new email."""

    def _post(self, **form):
        service = ResetFakeSupabase()
        client = _bare_app(service).test_client()
        data = {"access_token": "acc-123", "refresh_token": "ref-456"}
        data.update(form)
        with mock.patch("password_safety.is_breached", return_value=form.pop("_breached", False)):
            return client.post("/reset-password", data=data), service

    def _assert_retryable(self, response):
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="access_token" value="acc-123"', body)
        self.assertIn('id="refresh_token" value="ref-456"', body)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_mismatch_keeps_the_tokens(self):
        response, service = self._post(password="abcdefgh", confirm_password="abcdefgX")
        self._assert_retryable(response)
        self.assertEqual(service.update_account_calls, [])

    def test_too_short_keeps_the_tokens(self):
        response, _ = self._post(password="abc", confirm_password="abc")
        self._assert_retryable(response)

    def test_breached_keeps_the_tokens(self):
        service = ResetFakeSupabase()
        client = _bare_app(service).test_client()
        with mock.patch("password_safety.is_breached", return_value=True):
            response = client.post("/reset-password", data={
                "access_token": "acc-123", "refresh_token": "ref-456",
                "password": "password1", "confirm_password": "password1"})
        self._assert_retryable(response)
        self.assertEqual(service.update_account_calls, [])

    def test_page_script_shows_the_form_when_the_server_returned_tokens(self):
        source = _read("reset_password.html")
        self.assertIn('value="{{ access_token or \'\' }}"', source)
        self.assertIn('document.getElementById("access_token").value && document.getElementById("refresh_token").value', source)

    def test_a_fresh_get_carries_no_tokens(self):
        body = _bare_app(ResetFakeSupabase()).test_client().get("/reset-password").get_data(as_text=True)
        self.assertIn('id="access_token" value=""', body)


class ChatMessageRejectsMalformedJsonTests(unittest.TestCase):
    """Crafted bodies used to raise AttributeError -> 500."""

    def _post(self, payload):
        app = _bare_app(ChatFakeSupabase())
        app.config["WTF_CSRF_ENABLED"] = False
        client = app.test_client()
        _logged_in_session(client)
        return client.post("/chat/message", json=payload)

    def test_non_object_bodies(self):
        for payload in ([1, 2], "hello", 42, None):
            with self.subTest(payload=payload):
                self.assertEqual(self._post(payload).status_code, 400)

    def test_non_string_fields(self):
        for payload in ({"conversation_id": "c1", "content": 5},
                        {"conversation_id": "c1", "content": ["a"]},
                        {"conversation_id": 7, "content": "hello"},
                        {"conversation_id": {"x": 1}, "content": "hello"}):
            with self.subTest(payload=payload):
                self.assertEqual(self._post(payload).status_code, 400)


class PendingPromptOnlyAutoSendsWhenArmedTests(unittest.TestCase):
    """A pending building prompt auto-sent into whichever empty conversation
    the tenant opened next, even one picked from the sidebar."""

    def test_chat_auto_send_requires_a_fresh_arm_stamp(self):
        source = _read("chat.html")
        block = source[source.index("if (chatBox && chatBox.children.length === 0)"):]
        self.assertIn('sessionStorage.getItem("pendingChatArmedAt")', block)
        self.assertLess(block.index("pendingChatArmedAt"), block.index("chatForm.requestSubmit()"))
        self.assertIn("Date.now() - armedAt < 120000", block)

    def test_every_prompt_carrying_action_arms(self):
        chat = _read("chat.html")
        chip = chat[chat.index('.suggestion-chip[data-prompt]'):]
        self.assertIn('sessionStorage.setItem("pendingChatArmedAt"', chip[:600])
        cont = chat[chat.index("continueForm.hidden = false;"):]
        self.assertIn('sessionStorage.setItem("pendingChatArmedAt"', cont[:400])
        building = _read("building.html")
        form = building[building.index('querySelectorAll("[data-new-chat-form]")'):]
        self.assertIn('sessionStorage.setItem("pendingChatArmedAt"', form[:400])

    def test_landing_back_on_the_greeting_disarms(self):
        # A create that failed (429, Supabase error) redirects back to the
        # greeting; the stamp it left must not arm a later sidebar click.
        chat = _read("chat.html")
        block = chat[chat.index('const continueForm = document.querySelector("[data-continue-form]");'):]
        self.assertLess(block.index('sessionStorage.removeItem("pendingChatArmedAt")'),
                        block.index('continueForm.hidden = false;'))

    def test_new_chat_clears_the_arm_stamp_too(self):
        chat = _read("chat.html")
        block = chat[chat.index('document.querySelectorAll(".new-chat-form")'):]
        self.assertIn('sessionStorage.removeItem("pendingChatArmedAt")', block[:500])


class StorageAccessIsGuardedTests(unittest.TestCase):
    """With site data blocked, localStorage throws. An unguarded read at the
    top of settings.html's script killed every handler on the page."""

    def test_every_local_storage_call_is_inside_a_try(self):
        for name in sorted(os.listdir(TEMPLATES)):
            if not name.endswith(".html"):
                continue
            for number, line in enumerate(_read(name).splitlines(), 1):
                if "localStorage." in line:
                    with self.subTest(template=name, line=number):
                        self.assertIn("try {", line, f"{name}:{number} touches localStorage outside a try")


def _contrast(fg, bg):
    def lum(h):
        h = h.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TextContrastTests(unittest.TestCase):
    """Three pairs measured just under WCAG AA 4.5:1 in the sweep."""

    def _tokens(self, source, block_start):
        block = source[source.index(block_start):]
        block = block[:block.index("}")]
        return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))

    def test_light_success_text_on_its_tint(self):
        for name in sorted(os.listdir(TEMPLATES)):
            if not name.endswith(".html"):
                continue
            source = _read(name)
            if "--success-tint" not in source:
                continue
            tokens = self._tokens(source, ":root {")
            with self.subTest(template=name):
                self.assertGreaterEqual(_contrast(tokens["success"], tokens["success-tint"]), 4.5)

    def test_building_chips_in_both_themes(self):
        source = _read("building.html")
        self.assertIn("color: var(--chip-text);", source)
        for start in (":root {", ':root:not([data-theme="light"]) {', ':root[data-theme="dark"] {'):
            tokens = self._tokens(source, start)
            with self.subTest(block=start):
                self.assertGreaterEqual(_contrast(tokens["chip-text"], tokens["accent-tint"]), 4.5)

    def test_active_archived_conversation_uses_the_active_color(self):
        source = _read("chat.html")
        self.assertIn(".conversation-row.archived.active .conversation-link { color: var(--accent-hover); }", source)
        tokens = self._tokens(source, ":root {")
        self.assertGreaterEqual(_contrast(tokens["accent-hover"], tokens["accent-tint"]), 4.5)


class BrandedErrorPageTests(unittest.TestCase):
    """404, CSRF 400 and 429 used to be Werkzeug/Flask-Limiter defaults with
    no branding, no way back and no disclaimer."""

    def setUp(self):
        limiter.reset()
        self.app = create_app()
        self.client = self.app.test_client()

    def _assert_branded(self, response, status):
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, status)
        self.assertIn(branding.PRODUCT_NAME, body)
        self.assertIn(branding.SHORT_DISCLAIMER, body)
        self.assertIn(f"Error {status}", body)
        self.assertIn('href="/"', body)  # the lookup (street search since 2026-10-07)
        self.assertNotIn("<!--", body)

    def test_404(self):
        self._assert_branded(self.client.get("/no-such-page"), 404)

    def test_csrf_failure(self):
        self._assert_branded(self.client.post("/login", data={"email": "a@b.c", "password": "x"}), 400)

    def test_405_keeps_the_allow_header(self):
        response = self.client.delete("/learn-more")
        self._assert_branded(response, 405)
        self.assertIn("GET", response.headers.get("Allow", ""))

    def test_429_is_branded_and_says_when_to_retry(self):
        # A real lookup is needed: since 2026-09-30 a bare page load (no
        # address) doesn't count against the lookup budget.
        import building_service

        response = None
        with mock.patch.object(building_service, "lookup",
                               side_effect=building_service.LookupUnavailable("offline in tests")):
            for _ in range(31):
                response = self.client.get("/building?address=231+echo+place")
        self._assert_branded(response, 429)
        self.assertTrue(response.headers.get("Retry-After"))

    def test_chat_endpoint_gets_json_errors(self):
        response = self.client.post("/chat/message", json={"conversation_id": "c", "content": "hi"})
        self.assertEqual(response.status_code, 400)  # CSRF, before any route code
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("error", response.get_json())

    def test_redirects_are_untouched(self):
        response = self.client.get("/chat")
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))

    def test_unhandled_exception_is_a_branded_500(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False

        @self.app.route("/boom-for-test")
        def boom():
            raise RuntimeError("boom")

        self._assert_branded(self.client.get("/boom-for-test"), 500)


class SmallFixesTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def test_favicon_is_the_st_mark(self):
        response = create_app().test_client().get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertIn(f">{branding.LOGO_MONOGRAM}</text>", response.get_data(as_text=True))

    def test_error_template_follows_the_template_rules(self):
        source = _read("error.html")
        self.assertIn("docs/frontend/", source)
        self.assertNotIn(branding.PRODUCT_NAME, source)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "frontend", "error-pages.md")))
