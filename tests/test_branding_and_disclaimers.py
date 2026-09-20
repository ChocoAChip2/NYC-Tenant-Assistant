"""Tests for the SideKick Tidbit branding and the legal disclaimers.

The disclaimer is the point of this file. It is the one piece of text in
the app with legal weight, it has to appear on every page including any
page added later, and it has to say the specific words "not legal advice"
-- a reworded version that drops that phrase is worse than useless.
"""

import html
import os
import unittest

import flask

import branding
from routes import main_bp
from tests.app_test_support import configure_test_app

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

# Every page a visitor can reach, authenticated or not.
ALL_PAGES = ["/", "/login", "/forgot-password", "/reset-password", "/settings", "/chat", "/learn-more"]


class FakeSupabaseService:
    def build_user_scoped_client(self, access_token):
        return object()

    def list_conversations(self, user_client, archived=False):
        return []

    def ensure_conversation_for_user(self, *args, **kwargs):
        return None

    def fetch_messages_for_conversation(self, *args, **kwargs):
        return []

    def get_pending_account_deletion(self, *args, **kwargs):
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


class BrandingTestCase(unittest.TestCase):
    def setUp(self):
        self.client = _build_test_app().test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-1"
            session["user_email"] = "tenant@example.com"
            session["access_token"] = "fake-token"
            session["refresh_token"] = "fake-refresh"

    def _pages(self):
        for path in ALL_PAGES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path} did not render")
            yield path, response.get_data(as_text=True)


class ProductNameTests(BrandingTestCase):
    def test_every_page_uses_the_product_name(self):
        for path, body in self._pages():
            self.assertIn(branding.PRODUCT_NAME, body, f"{path} does not show the product name")

    def test_the_old_name_is_gone_everywhere(self):
        for path, body in self._pages():
            self.assertNotIn("NYC Tenant Assistant", body, f"{path} still shows the old name")
            self.assertNotIn("Tenant Helper", body, f"{path} still shows the old name")

    def test_the_name_is_not_hardcoded_in_the_templates(self):
        """It comes from branding.py through a context processor, so a
        future rename is one edit rather than a search across six files."""
        for name in os.listdir(_TEMPLATES_DIR):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(_TEMPLATES_DIR, name), encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn(branding.PRODUCT_NAME, source, f"{name} hardcodes the product name")

    def test_the_st_monogram_appears_on_branded_pages(self):
        for path in ["/", "/login", "/chat", "/learn-more"]:
            body = self.client.get(path).get_data(as_text=True)
            self.assertIn(f">{branding.LOGO_MONOGRAM}<", body, f"{path} is missing the ST mark")


class ShortDisclaimerTests(BrandingTestCase):
    def test_every_page_carries_the_standing_disclaimer(self):
        for path, body in self._pages():
            self.assertIn(branding.SHORT_DISCLAIMER, body, f"{path} has no disclaimer")

    def test_the_disclaimer_actually_says_not_legal_advice(self):
        """Guards against someone softening the wording later. The phrase
        is the part that matters; a friendlier sentence without it does not
        do the job it exists to do."""
        self.assertIn("not legal advice", branding.SHORT_DISCLAIMER.lower())

    def test_every_page_except_learn_more_links_to_learn_more(self):
        for path, body in self._pages():
            if path == "/learn-more":
                continue
            self.assertIn("/learn-more", body, f"{path} does not link to the Learn More page")

    def test_the_disclaimer_is_not_hardcoded_in_the_templates(self):
        """The standing footer notice must come from branding.py on every
        page, so the six standalone templates cannot drift apart on it.

        learn_more.html is exempt: it is the dedicated page about exactly
        this, and "This is not legal advice" is its section heading rather
        than a copy of the footer text.
        """
        for name in os.listdir(_TEMPLATES_DIR):
            if not name.endswith(".html") or name == "learn_more.html":
                continue
            with open(os.path.join(_TEMPLATES_DIR, name), encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("not legal advice", source.lower(), f"{name} hardcodes disclaimer wording")


class LearnMorePageTests(BrandingTestCase):
    def test_it_is_reachable_without_logging_in(self):
        """Someone deciding whether to trust this with their housing
        situation should be able to read what it claims to be before
        handing over an email address."""
        anonymous = _build_test_app().test_client()

        response = anonymous.get("/learn-more")

        self.assertEqual(response.status_code, 200)
        self.assertIn("not legal advice", response.get_data(as_text=True).lower())

    def test_it_shows_the_full_disclaimer(self):
        body = self.client.get("/learn-more").get_data(as_text=True)
        for paragraph in branding.FULL_DISCLAIMER_PARAGRAPHS:
            # Templates escape apostrophes, so compare on a distinctive slice.
            self.assertIn(paragraph.split(".")[0][:60], body)

    def test_it_states_there_is_no_attorney_client_relationship(self):
        body = self.client.get("/learn-more").get_data(as_text=True).lower()
        self.assertIn("attorney-client relationship", body)

    def test_it_says_the_ai_can_be_wrong(self):
        body = self.client.get("/learn-more").get_data(as_text=True).lower()
        self.assertIn("can be wrong", body)

    def test_it_lists_every_help_resource_with_a_working_shape(self):
        body = self.client.get("/learn-more").get_data(as_text=True)
        self.assertTrue(branding.HELP_RESOURCES, "no help resources configured")
        for resource in branding.HELP_RESOURCES:
            # Escaped, because a name like "Homeowner & Tenant Helpline"
            # renders its ampersand as &amp;.
            self.assertIn(html.escape(resource["name"]), body)
            self.assertIn(resource["url"], body)
            self.assertTrue(resource["url"].startswith("https://"), resource["url"])

    def test_outbound_resource_links_are_safe_and_open_in_a_new_tab(self):
        """A tenant mid-conversation should not lose it by tapping a
        helpline link."""
        body = self.client.get("/learn-more").get_data(as_text=True)
        self.assertIn('target="_blank"', body)
        self.assertIn('rel="noopener noreferrer"', body)


class AssistantEscalationPromptTests(unittest.TestCase):
    """The footer carries the standing notice; the assistant is supposed to
    say it out loud only when the stakes are real. These pin down that the
    prompt actually encodes that decision, and encodes both halves of it."""

    def test_the_prompt_lists_every_escalation_trigger(self):
        from ai_service import INTAKE_SYSTEM_PROMPT

        self.assertTrue(branding.ESCALATION_TRIGGERS)
        for trigger in branding.ESCALATION_TRIGGERS:
            self.assertIn(trigger, INTAKE_SYSTEM_PROMPT)

    def test_the_prompt_tells_it_to_say_it_out_loud_when_stakes_are_real(self):
        from ai_service import INTAKE_SYSTEM_PROMPT

        self.assertIn("not a lawyer", INTAKE_SYSTEM_PROMPT)
        self.assertIn("SAY THIS OUT LOUD", INTAKE_SYSTEM_PROMPT)

    def test_the_prompt_still_suppresses_boilerplate_on_routine_questions(self):
        """The other half of the decision. Without this the assistant goes
        back to stapling the same sentence onto every reply, which is what
        trains people to skip past it when it matters."""
        from ai_service import INTAKE_SYSTEM_PROMPT

        self.assertIn("Do NOT attach a disclaimer to routine factual questions", INTAKE_SYSTEM_PROMPT)

    def test_the_intake_json_contract_is_untouched(self):
        from ai_service import INTAKE_SYSTEM_PROMPT

        self.assertIn('"status": "complete"', INTAKE_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
