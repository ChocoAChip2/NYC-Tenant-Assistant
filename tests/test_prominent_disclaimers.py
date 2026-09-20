"""Tests for the two disclaimers added on top of the standing footer note.

There are now three layers, and they do different jobs:

- branding.TOP_DISCLAIMER  -- the loud amber banner under the header. It is
  the one that tells someone actively looking for a lawyer what to do, and
  it makes a PROMISE: ask the chatbot and it will hand you a list. The last
  class in this file is what keeps that promise honest, by checking the
  system prompt still carries the referral rule and the real resources.
- branding.PER_MESSAGE_DISCLAIMER -- under every assistant reply, so no
  single message can be screenshotted or pasted without the qualifier.
  Rendered twice: by Jinja for stored history, and by renderMessage() in
  JS for the reply that just arrived. Both paths are asserted, because
  fixing one and forgetting the other is the obvious failure.
- branding.SHORT_DISCLAIMER -- the pre-existing footer note, unchanged.
"""

import os
import unittest

import flask

import ai_service
import branding
from routes import main_bp
from tests.app_test_support import configure_test_app

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class FakeSupabaseService:
    def build_user_scoped_client(self, access_token):
        return object()

    def list_conversations(self, user_client, archived=False):
        return []

    def ensure_conversation_for_user(self, user_client, conversation_id, user_id):
        return None

    def fetch_messages_for_conversation(self, user_client, conversation_id):
        return [
            {"role": "user", "content": "My landlord will not fix the heat"},
            {"role": "assistant", "content": "Here is what the code says.", "content_html": "<p>Here is what the code says.</p>"},
        ]

    def fetch_all_conversations_with_messages(self, user_client):
        return []

    def get_pending_account_deletion(self, user_client, user_id):
        return None


class FakeAIService:
    def is_ready(self):
        return True


def _build_test_app():
    app = flask.Flask(__name__, template_folder=_TEMPLATES_DIR)
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = FakeSupabaseService()
    app.config["AI_SERVICE"] = FakeAIService()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app


def _client():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["user_email"] = "tenant@example.com"
        session["access_token"] = "fake-token"
    return client


class TopDisclaimerTests(unittest.TestCase):
    def test_banner_renders_on_the_chat_page(self):
        body = _client().get("/chat").get_data(as_text=True)

        self.assertIn('class="top-disclaimer"', body)
        self.assertIn(branding.TOP_DISCLAIMER, body)

    def test_banner_renders_on_the_settings_page(self):
        body = _client().get("/settings").get_data(as_text=True)

        self.assertIn('class="top-disclaimer"', body)
        self.assertIn(branding.TOP_DISCLAIMER, body)

    def test_banner_survives_inside_an_open_conversation(self):
        body = _client().get("/chat?conversation_id=c1").get_data(as_text=True)

        self.assertIn(branding.TOP_DISCLAIMER, body)

    def test_banner_says_the_three_things_it_has_to_say(self):
        """Wording can be edited; these three claims are the reason it exists."""
        text = branding.TOP_DISCLAIMER

        self.assertIn("legal assistance", text)
        self.assertIn("list of sources", text)
        self.assertIn("does NOT provide legal advice", text)

    def test_banner_is_visually_flagged_in_the_warning_colour_not_the_body_colour(self):
        body = _client().get("/chat").get_data(as_text=True)
        rule_start = body.index(".top-disclaimer {")
        rule = body[rule_start:body.index("}", rule_start)]

        self.assertIn("background: var(--warning-tint)", rule)
        self.assertIn("color: var(--warning)", rule)
        self.assertIn("border-left: 4px solid var(--warning)", rule)

    def test_banner_is_not_dismissible(self):
        """A legal notice with a close button is a legal notice nobody sees."""
        body = _client().get("/chat").get_data(as_text=True)
        rule_start = body.index('<p class="top-disclaimer"')
        banner = body[rule_start:body.index("</p>", rule_start)]

        self.assertNotIn("dismiss", banner.lower())
        self.assertNotIn("<button", banner.lower())

    def test_banner_is_not_hardcoded_into_the_templates(self):
        """It has to come from branding.py, or the two pages will drift."""
        for name in ("chat.html", "settings.html"):
            with self.subTest(template=name):
                with open(os.path.join(_TEMPLATES_DIR, name), encoding="utf-8") as handle:
                    source = handle.read()
                self.assertIn("{{ top_disclaimer }}", source)
                self.assertNotIn("does NOT provide legal advice", source)


class PerMessageDisclaimerTests(unittest.TestCase):
    def test_stored_assistant_messages_carry_the_note(self):
        body = _client().get("/chat?conversation_id=c1").get_data(as_text=True)

        self.assertIn('class="message-disclaimer"', body)
        self.assertIn(branding.PER_MESSAGE_DISCLAIMER, body)

    def test_the_note_appears_once_per_assistant_message_and_not_on_user_messages(self):
        """The fixture has one user turn and one assistant turn."""
        body = _client().get("/chat?conversation_id=c1").get_data(as_text=True)

        self.assertEqual(body.count('class="message-disclaimer"'), 1)

    def test_live_replies_get_the_note_too(self):
        """renderMessage() builds the reply that just arrived; if only the
        Jinja loop were updated, the note would appear on reload but not on
        the message the user is actually reading."""
        body = _client().get("/chat?conversation_id=c1").get_data(as_text=True)

        self.assertIn("const PER_MESSAGE_DISCLAIMER =", body)
        self.assertIn('note.className = "message-disclaimer"', body)
        self.assertIn('if (role === "assistant") {', body)

    def test_the_note_is_injected_as_text_not_html(self):
        body = _client().get("/chat?conversation_id=c1").get_data(as_text=True)

        self.assertIn("note.textContent = PER_MESSAGE_DISCLAIMER", body)

    def test_the_note_stays_short(self):
        """It repeats on every single turn. A paragraph here is a paragraph
        nobody reads, and it would crowd the reply it belongs to."""
        self.assertLessEqual(len(branding.PER_MESSAGE_DISCLAIMER), 80)
        self.assertIn("not legal advice", branding.PER_MESSAGE_DISCLAIMER)

    def test_the_note_is_visually_quieter_than_the_banner(self):
        body = _client().get("/chat").get_data(as_text=True)
        rule_start = body.index(".message-disclaimer {")
        rule = body[rule_start:body.index("}", rule_start)]

        self.assertIn("color: var(--muted)", rule)
        self.assertIn("font-size: 11px", rule)


class ReferralPromiseTests(unittest.TestCase):
    """The banner tells tenants the chatbot will give them a list of places
    to contact. That is a promise made in the UI and kept in the prompt --
    if the prompt loses the rule, the banner becomes a lie."""

    def test_prompt_tells_the_assistant_to_hand_over_the_list(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT

        self.assertIn("asks for a lawyer", prompt)
        self.assertIn("legal help", prompt)

    def test_every_help_resource_and_its_contact_is_in_the_prompt(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT

        for resource in branding.HELP_RESOURCES:
            with self.subTest(resource=resource["name"]):
                self.assertIn(resource["name"], prompt)
                self.assertIn(resource["contact"], prompt)

    def test_prompt_forbids_inventing_other_organisations(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT

        self.assertIn("do not invent any other", prompt)

    def test_prompt_still_carries_the_earlier_escalation_rules(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT

        for trigger in branding.ESCALATION_TRIGGERS:
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, prompt)

    def test_the_json_handoff_contract_is_untouched(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT

        self.assertIn('"status": "complete"', prompt)


if __name__ == "__main__":
    unittest.main()
