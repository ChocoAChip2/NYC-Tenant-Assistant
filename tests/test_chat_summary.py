"""The printable chat summary (chat_summary.py and POST /conversations/<id>/summary.pdf).

Owner request 2026-11-01: a PDF with the issues, next steps, where the
assistant isn't sure (and who to ask), and links to check, so a tenant can
log off and still act on it. Nothing invented, nothing stored.
"""

import io
import json
import os
import unittest

import flask
from flask_babel import Babel
from pypdf import PdfReader

import branding
import chat_summary as cs
from routes import main_bp
from tests.app_test_support import configure_test_app, reset_rate_limits

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

LAW_URL = "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-27-2029"
MESSAGES = [
    {"role": "user", "content": "My heat has been off for 3 days."},
    {"role": "assistant", "content": (
        "Heat is required in the heat season ([NYC Admin Code § 27-2029](" + LAW_URL + ")). "
        "Call 311 to file a complaint. A neighbour's [blog](https://example.com/heat) is not a source, "
        "and neither is [this](http://www.nyc.gov/insecure).")},
]
GOOD = {
    "issues": ["No heat for 3 days."],
    "next_steps": ["Call 311 to file a heat complaint.", "Keep a log of the temperature."],
    "unsure": [{"point": "Whether the landlord will be fined.", "ask": "HPD"}],
}


def _text(pdf_bytes):
    return " ".join(" ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages).split())


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.conversation = cs.transcript(MESSAGES)

    def test_a_good_answer_is_kept(self):
        summary = cs.parse(json.dumps(GOOD), self.conversation)
        self.assertEqual(summary.issues, ["No heat for 3 days."])
        self.assertEqual(len(summary.next_steps), 2)
        self.assertEqual(summary.unsure, [("Whether the landlord will be fined.", "HPD")])
        self.assertEqual(summary.left_out, 0)

    def test_a_number_the_chat_never_said_is_dropped_and_counted(self):
        data = dict(GOOD, next_steps=["Call 311.", "Expect a $250 fine within 30 days."])
        summary = cs.parse(json.dumps(data), self.conversation)
        self.assertEqual(summary.next_steps, ["Call 311."])
        self.assertEqual(summary.left_out, 1)

    def test_a_section_sign_needs_the_chat_to_have_one(self):
        conversation = cs.transcript([{"role": "user", "content": "no heat"}, {"role": "assistant", "content": "Call 311."}])
        summary = cs.parse(json.dumps(dict(GOOD, issues=["No heat (§ 27-2029)."])), conversation)
        self.assertEqual(summary.issues, [])
        self.assertEqual(summary.left_out, 1)

    def test_markdown_and_links_become_plain_text(self):
        summary = cs.parse(json.dumps(dict(GOOD, issues=["**No heat**, see [this](https://x.example)."])), self.conversation)
        self.assertEqual(summary.issues, ["No heat, see this."])

    def test_unusable_answers_are_none(self):
        for raw in ("", "not json", "[]", json.dumps({"issues": [], "next_steps": [], "unsure": []})):
            with self.subTest(raw=raw):
                self.assertIsNone(cs.parse(raw, self.conversation))

    def test_long_lists_are_capped(self):
        summary = cs.parse(json.dumps(dict(GOOD, issues=["Cold."] * 30)), self.conversation)
        self.assertEqual(len(summary.issues), cs.MAX_ITEMS)


class SourceTests(unittest.TestCase):
    def test_only_official_https_links_from_replies(self):
        found = cs.sources(MESSAGES + [{"role": "user", "content": "[mine](https://www.nyc.gov/mine)"}])
        self.assertEqual([s.url for s in found], [LAW_URL])
        self.assertEqual(found[0].label, "NYC Admin Code § 27-2029")

    def test_help_organisations_count_as_sources(self):
        found = cs.sources([{"role": "assistant", "content": "[Housing Court Answers](https://housingcourtanswers.org/)"}])
        self.assertEqual(len(found), 1)

    def test_a_look_alike_host_is_not_official(self):
        found = cs.sources([{"role": "assistant", "content": "[HPD](https://nyc.gov.example.com/x) [HPD](https://evilnyc.gov/x)"}])
        self.assertEqual(found, [])


class LanguageTests(unittest.TestCase):
    def test_languages_the_font_can_draw_are_kept(self):
        self.assertEqual(cs.summary_language("es"), ("es", "Spanish"))
        self.assertEqual(cs.summary_language("sw"), ("sw", "Swahili"))

    def test_chinese_falls_back_to_english(self):
        self.assertEqual(cs.summary_language("zh_Hans"), ("en", "English"))
        self.assertEqual(cs.summary_language("zh_Hant"), ("en", "English"))

    def test_characters_the_font_lacks_are_removed(self):
        self.assertEqual(cs.printable("Heat 🔥 暖气 — ok"), "Heat   — ok")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.app = flask.Flask(__name__)
        Babel(self.app)

    def _render(self, summary, links=()):
        with self.app.test_request_context():
            return cs.render(summary, list(links), branding.HELP_RESOURCES, title="No heat 🔥", made_on="November 1, 2026")

    def test_every_section_is_in_the_pdf(self):
        summary = cs.parse(json.dumps(GOOD), cs.transcript(MESSAGES))
        pdf = self._render(summary, cs.sources(MESSAGES))
        self.assertTrue(pdf.startswith(b"%PDF"))
        text = _text(pdf)
        for expected in ("Chat summary", "No heat for 3 days.", "Next steps", "Call 311 to file a heat complaint.",
                         "ask a person", "Ask: HPD.", "not legal advice", "Sources to check", "27-2029",
                         "Where to get help", "Housing Court Answers"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_the_unsure_section_is_never_empty_and_says_what_was_left_out(self):
        summary = cs.Summary(issues=["Cold."], left_out=2)
        text = _text(self._render(summary))
        self.assertIn("talk to a tenant attorney or legal aid", text)
        self.assertIn("left out because they couldn't be checked", text)
        self.assertIn("didn't cite a specific law", text)


class FakeSupabase:
    def __init__(self, messages=MESSAGES, owned=True):
        self.messages = messages
        self.owned = owned

    def build_user_scoped_client(self, access_token):
        return object()

    def ensure_conversation_for_user(self, client, conversation_id, user_id):
        if not self.owned:
            raise ValueError("not yours")

    def fetch_messages_for_conversation(self, client, conversation_id):
        return list(self.messages)

    def get_conversation_title(self, client, conversation_id, user_id):
        return "No heat in apartment"


class FakeAI:
    def __init__(self, answer=json.dumps(GOOD)):
        self.answer = answer
        self.calls = []

    def is_ready(self):
        return True

    def summarize_chat(self, conversation, instruction):
        self.calls.append((conversation, instruction))
        return self.answer


class RouteTests(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()

    def _client(self, supabase=None, ai=None, logged_in=True):
        app = flask.Flask(__name__, template_folder=_TEMPLATES_DIR)
        app.secret_key = "test"
        app.config["SUPABASE_SERVICE"] = supabase or FakeSupabase()
        app.config["AI_SERVICE"] = ai or FakeAI()
        Babel(app)
        app.register_blueprint(main_bp)
        configure_test_app(app)
        client = app.test_client()
        if logged_in:
            with client.session_transaction() as s:
                s["user_id"], s["access_token"] = "user-1", "token"
        return client

    def test_logged_out_goes_to_login(self):
        response = self._client(logged_in=False).post("/conversations/c1/summary.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))

    def test_get_is_not_allowed(self):
        self.assertEqual(self._client().get("/conversations/c1/summary.pdf").status_code, 405)

    def test_a_pdf_download_that_is_not_cached(self):
        ai = FakeAI()
        response = self._client(ai=ai).post("/conversations/c1/summary.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("No heat in apartment", _text(response.data))
        self.assertIn("Write in English.", ai.calls[0][1])
        self.assertIn("Tenant: My heat has been off", ai.calls[0][0])

    def test_someone_elses_conversation_is_not_found(self):
        response = self._client(supabase=FakeSupabase(owned=False)).post("/conversations/c1/summary.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/chat"))

    def test_nothing_to_summarize_yet(self):
        ai = FakeAI()
        response = self._client(supabase=FakeSupabase(messages=[{"role": "user", "content": "hi"}]), ai=ai).post(
            "/conversations/c1/summary.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ai.calls, [])

    def test_a_failed_model_answer_goes_back_to_the_chat(self):
        response = self._client(ai=FakeAI(answer="")).post("/conversations/c1/summary.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertIn("conversation_id=c1", response.headers["Location"])

    def test_a_chinese_page_gets_an_english_summary(self):
        ai = FakeAI()
        response = self._client(ai=ai).post("/conversations/c1/summary.pdf?lang=zh_Hans")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Write in English.", ai.calls[0][1])
        self.assertIn("Chat summary", _text(response.data))


class ChatPageTests(unittest.TestCase):
    def test_the_menu_offers_the_summary(self):
        with open(os.path.join(_TEMPLATES_DIR, "chat.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("main.conversation_summary_pdf", page)
        self.assertIn('_("Save summary (PDF)")', page)


if __name__ == "__main__":
    unittest.main()
