"""Regression tests for the intake-clerk -> PDF-download flow in
routes.py:chat_message.

This guards against a real bug that shipped to main: FormService was
called with template_path="templates/RA-81.pdf", but the PDF actually
committed to the repo is templates/ra-81-fillable.pdf. On a case-sensitive
filesystem (which is what Render runs) that mismatch means pypdf's
PdfReader raises FileNotFoundError the moment a user finishes the intake
flow -- the one time this code path matters in production.

No real Gemini or Supabase call is made. Since 2026-10-01 the route checks
the intake before filling (form_service.missing_fields) and these tests
fill a real PDF in memory to check what the tenant downloads.
"""

import io
import json
import os
import unittest
from unittest import mock

from pypdf import PdfReader

import ai_service
import form_service
from tests.test_form_service import FULL

import flask

from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.review_support import MemorySupabase

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPECTED_TEMPLATE_PATH = "templates/ra-81-fillable.pdf"


class FakeSupabaseService(MemorySupabase):
    def build_user_scoped_client(self, access_token):
        return object()

    def ensure_conversation_for_user(self, user_client, conversation_id, user_id):
        return None

    def fetch_messages_for_conversation(self, user_client, conversation_id):
        return [{"role": "user", "content": "My info is ready", "created_at": ""}]

    def insert_message(self, user_client, message):
        return None


class FakeAIService:
    def __init__(self, reply):
        self._reply = reply

    def generate_reply(self, messages):
        return self._reply


def _build_test_app(ai_reply):
    app = flask.Flask(__name__, template_folder=os.path.join(_REPO_ROOT, "templates"))
    app.secret_key = "test-secret"
    app.config["SUPABASE_SERVICE"] = FakeSupabaseService()
    app.config["AI_SERVICE"] = FakeAIService(ai_reply)
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app


def _logged_in_session(client):
    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["user_email"] = "tenant@example.com"
        session["access_token"] = "fake-token"


class TemplateFileTests(unittest.TestCase):
    def test_the_referenced_pdf_template_actually_exists(self):
        """The exact file FormService.fill_tenant_form is called with must exist on disk."""

        full_path = os.path.join(_REPO_ROOT, _EXPECTED_TEMPLATE_PATH)
        self.assertTrue(
            os.path.isfile(full_path),
            f"Expected the RA-81 PDF template at {full_path} -- if it moved or was "
            "renamed, update _EXPECTED_TEMPLATE_PATH here *and* the template_path "
            "argument in routes.py:chat_message together.",
        )


class RecordingSupabase(FakeSupabaseService):
    def __init__(self):
        super().__init__()
        self.inserted = []

    def insert_message(self, user_client, message):
        self.inserted.append(message)


def _post(reply, content="That's all correct"):
    app = _build_test_app(reply)
    service = RecordingSupabase()
    app.config["SUPABASE_SERVICE"] = service
    client = app.test_client()
    _logged_in_session(client)
    response = client.post("/chat/message", json={"conversation_id": "c1", "content": content})
    return response, service


COMPLETE = json.dumps(FULL)


class CompletionJsonTriggersPdfDownloadTests(unittest.TestCase):
    def test_other_form_types_are_not_silently_rendered_as_ra81(self):
        for requested_form in ("RA-84", "HHW-1", None, {"unexpected": "object"}):
            with self.subTest(form=requested_form), mock.patch("routes.form_service.fill_to_bytes") as fill:
                response, service = _post(json.dumps(dict(FULL, form=requested_form)))
                fill.assert_not_called()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/json")
                self.assertIn("can't generate that other form", response.get_json()["reply"])
                self.assertEqual(service.inserted[-1]["content"], response.get_json()["reply"])

    def test_a_complete_intake_downloads_a_filled_pdf(self):
        with mock.patch("routes.form_service.fill_to_bytes") as fill:
            response, service = _post(COMPLETE)
        fill.assert_not_called()
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("Review and confirm", response.get_json()["reply"])
        self.assertEqual(len(service.drafts), 1)

    def test_the_stored_message_is_the_next_steps_not_the_raw_json(self):
        _, service = _post(COMPLETE)
        assistant = [m for m in service.inserted if m["role"] == "assistant"]
        self.assertIn("Review and confirm", assistant[-1]["content"])
        self.assertNotIn("Maria Rodriguez", assistant[-1]["content"])

    def test_a_fenced_intake_still_works(self):
        response, _ = _post(f"```json\n{COMPLETE}\n```")
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("Review and confirm", response.get_json()["reply"])

    def test_an_incomplete_intake_asks_for_what_is_missing_instead_of_a_pdf(self):
        legacy = '{"status": "complete", "name": "A Tenant", "address": "123 Main St", "complaint": "No heat"}'
        with mock.patch("routes.form_service.fill_to_bytes") as fill:
            response, service = _post(legacy)
        fill.assert_not_called()
        payload = response.get_json()
        self.assertIn("Before I can fill in your RA-81, I still need", payload["reply"])
        self.assertIn("landlord's mailing address", payload["reply"])
        self.assertEqual(service.inserted[-1]["content"], payload["reply"])

    def test_non_json_reply_is_returned_as_a_normal_chat_message(self):
        with mock.patch("routes.form_service.fill_to_bytes") as fill:
            response, _ = _post("This is a normal housing-law answer, not JSON.", "What are my rights?")
        fill.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "This is a normal housing-law answer, not JSON.")


class PromptContractTests(unittest.TestCase):
    """The prompt and the form code must describe the same JSON."""

    def test_every_key_the_form_reads_is_in_the_prompt(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT
        for key in ("tenant", "owner", "regulation", "coop_condo", "seven_a_administrator", "move_in_date",
                    "apartments_in_building", "scrie_drie", "section8", "voucher_number", "notice", "conditions",
                    "phone_day", "phone_home", "city_state_zip", *form_service.ROOM_KEYS,
                    *form_service.REGULATION_CHECKBOXES, *form_service.SECTION8_CHECKBOXES,
                    *form_service.NOTICE_METHOD_CHECKBOXES):
            with self.subTest(key=key):
                self.assertIn(key, prompt)

    def test_the_prompt_forbids_guessing_and_requires_confirmation(self):
        prompt = ai_service.INTAKE_SYSTEM_PROMPT
        self.assertIn("Never guess", prompt)
        self.assertIn("confirm", prompt)


if __name__ == "__main__":
    unittest.main()
