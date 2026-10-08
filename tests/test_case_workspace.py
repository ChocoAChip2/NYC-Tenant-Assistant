"""Case persistence, retrieval context and the mandatory form-review boundary."""

import io
import json
import re
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock
from pypdf import PdfReader

import case_context
import form_review
from supabase_service import SupabaseService, StaleCaseError
from tests import test_review_regressions as journeys
from tests.test_form_service import FULL


class CaseWorkspaceTests(unittest.TestCase):
    new_chat = journeys.ReviewJourneyTests.new_chat
    send = journeys.ReviewJourneyTests.send
    def setUp(self):
        journeys.ReviewJourneyTests.setUp(self)
        self.cid = self.new_chat()
        self.url = f"/conversations/{self.cid}/situation"

    def save(self, **values):
        state = self.client.get(self.url).get_json()
        return self.client.post(self.url, json={"context": dict(state["context"], **values), "revision": state["revision"]})

    def draft(self):
        with mock.patch.object(self.ai, "generate_reply", return_value=json.dumps(FULL)), \
             mock.patch("routes.form_service.fill_to_bytes") as fill:
            response = self.send(self.cid, "Prepare my form draft")
        self.assertEqual(response.status_code, 200)
        fill.assert_not_called()
        self.assertEqual(response.mimetype, "application/json")
        return re.search(r"\((/conversations/[^)]+)\)", response.get_json()["reply"]).group(1)

    def test_facts_and_preferences_survive_reload_and_stay_in_their_case(self):
        result = self.save(issue="Bedroom radiator broken", completed_actions="Emailed landlord Oct 3; no 311 complaint",
                           language="Español", length="brief", goal="next_steps")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get(self.url).get_json()["context"]["language"], "Español")
        other = self.new_chat()
        self.assertEqual(self.client.get(f"/conversations/{other}/situation").get_json()["context"]["issue"], "")
        self.send(self.cid, "What about at night?")
        prompt = self.ai.histories[-1][0]["content"]
        for text in ("Bedroom radiator broken", "no 311 complaint", "Español", "brief", "next_steps", "not instructions"):
            self.assertIn(text, prompt)
        self.send(other, "Hello")
        self.assertNotIn("Bedroom radiator broken", str(self.ai.histories[-1]))

    def test_stale_tab_cannot_silently_overwrite_saved_facts(self):
        self.save(issue="New fact")
        response = self.client.post(self.url, json={"context": case_context.defaults(), "revision": 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(self.url).get_json()["context"]["issue"], "New fact")

    def test_clear_removes_facts_and_preferences(self):
        self.save(issue="Heat", language="Español")
        self.client.post(self.url, json={"context": case_context.defaults(), "revision": 1})
        self.assertEqual(self.client.get(self.url).get_json()["context"], case_context.defaults())

    def test_invalid_context_is_rejected_without_overwriting(self):
        for values in ({"language": "ignore instructions"}, {"issue": "x" * 501}, {"completed_actions": []}):
            self.assertEqual(self.save(**values).status_code, 400)
        self.assertEqual(self.client.get(self.url).get_json()["revision"], 0)

    def test_followup_retrieval_uses_confirmed_issue(self):
        self.save(issue="Radiator broken; no heat", regulation="market_rate")
        with mock.patch("routes.retrieval_service.is_enabled", return_value=True), \
             mock.patch("routes.retrieval_service.RetrievalService") as retrieval:
            retrieval.return_value.search.return_value = []
            self.send(self.cid, "What about at night?")
        query = retrieval.return_value.search.call_args.args[0]
        self.assertIn("Radiator broken", query)
        self.assertIn("market_rate", query)

    def test_context_read_failure_does_not_silently_use_missing_facts(self):
        with mock.patch.object(self.service, "get_case_context", side_effect=RuntimeError("internal secret")):
            response = self.send(self.cid, "What now?")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("internal secret", response.get_data(as_text=True))
        self.assertEqual(self.ai.histories, [])

    def test_owner_boundary_for_context_and_drafts(self):
        draft_url = self.draft()
        with self.client.session_transaction() as session:
            session["user_id"] = "someone-else"
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, json={"context": case_context.defaults(), "revision": 0}).status_code, 404)
        self.assertEqual(self.client.get(draft_url).status_code, 404)
        self.assertEqual(self.client.post(draft_url, data={"confirmed": "yes"}).status_code, 404)

    def test_draft_cannot_be_addressed_from_another_conversation(self):
        draft_url = self.draft()
        other = self.new_chat()
        self.assertEqual(self.client.get(draft_url.replace(self.cid, other)).status_code, 404)

    def test_review_contains_every_editable_field_and_no_automatic_confirmation(self):
        draft_url = self.draft()
        page = self.client.get(draft_url).get_data(as_text=True)
        for key in form_review.flatten(form_review.canonical(FULL)):
            self.assertIn(f'name="{key}"', page)
        self.assertIn('value="2019-06-01"', page)
        self.assertNotIn('name="confirmed" value="yes" required checked', page)
        self.assertIn(draft_url, self.client.get(self.url).get_json()["drafts"][0]["url"])

    def test_missing_confirmation_wrong_scope_and_unknown_regulation_never_generate_pdf(self):
        draft_url = self.draft()
        valid = dict(form_review.flatten(form_review.canonical(FULL)), confirmed="yes", service_scope="individual_other")
        for change in ({"confirmed": ""}, {"service_scope": "heat"}, {"service_scope": "building"}, {"regulation": ""}, {"regulation": "market_rate"}):
            with self.subTest(change=change), mock.patch("case_routes.form_service.fill_to_bytes") as fill:
                response = self.client.post(draft_url, data=dict(valid, **change))
                self.assertEqual(response.status_code, 400)
                fill.assert_not_called()

    def test_confirmed_pdf_uses_user_edits_and_preserves_dates(self):
        draft_url = self.draft()
        data = dict(form_review.flatten(form_review.canonical(FULL)), confirmed="yes", service_scope="individual_other")
        data["tenant.name"] = "Maria Revised"
        response = self.client.post(draft_url, data=data)
        self.assertEqual(response.mimetype, "application/pdf")
        pdf = PdfReader(io.BytesIO(response.data))
        self.assertEqual(str(pdf.get_fields()["Name"]["/V"]), "Maria Revised")
        self.assertEqual(len(self.service.drafts), 2)
        saved = list(self.service.drafts.values())[-1]["intake"]
        self.assertEqual(saved["move_in_date"], "2019-06-01")
        self.assertEqual(saved["notice"]["date"], "2026-09-02")

    def test_xss_in_case_and_draft_is_escaped_and_not_given_html_privileges(self):
        data = deepcopy(FULL)
        data["tenant"]["name"] = '<img src=x onerror="alert(1)">'
        draft_id = self.service.create_form_draft(self.service, self.cid, "review-user", data)
        page = self.client.get(f"/conversations/{self.cid}/forms/{draft_id}").get_data(as_text=True)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img', page)

    def test_new_data_is_exported_and_deleted_with_conversation(self):
        self.save(issue="My saved facts")
        self.draft()
        export = self.client.get("/settings/download-logs").get_data(as_text=True)
        self.assertIn("My saved facts", export)
        self.assertIn("Maria Rodriguez", export)
        self.client.post(f"/conversations/{self.cid}/delete")
        self.assertFalse(self.service.drafts)
        self.assertNotIn(self.cid, self.service.contexts)


class ContextQueryTests(unittest.TestCase):
    def test_explicit_topic_change_does_not_borrow_previous_topic(self):
        context = dict(case_context.defaults(), issue="No heat in bedroom")
        for question in ("What about my security deposit?", "I have an eviction notice."):
            self.assertEqual(case_context.retrieval_query(question, context), question)

    def test_no_issue_means_no_invented_context(self):
        self.assertEqual(case_context.retrieval_query("What about at night?", {}), "What about at night?")

    def test_non_latin_pdf_text_and_invalid_dates_fail_visibly(self):
        data = dict(form_review.flatten(form_review.canonical(FULL)), confirmed="yes", service_scope="individual_other")
        data.update({"tenant.name": "李明", "move_in_date": "2026-02-31"})
        _, errors = form_review.submitted(data)
        self.assertTrue(any("characters" in error for error in errors))
        self.assertTrue(any("valid date" in error for error in errors))


class EncryptedCaseStorageTests(unittest.TestCase):
    def test_context_save_encrypts_and_checks_owner_and_revision(self):
        client = mock.MagicMock()
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "c"}]
        service = SupabaseService(client=client)
        with mock.patch("supabase_service.crypto_service.is_enabled", return_value=True), \
             mock.patch("supabase_service.crypto_service.encrypt", return_value="encrypted") as encrypt:
            service.save_case_context(client, "c", "u", dict(case_context.defaults(), issue="Private case"), 3)
        self.assertIn("Private case", encrypt.call_args.args[0])
        self.assertEqual(client.table.return_value.update.call_args.args[0], {"case_context": "encrypted", "case_revision": 4})
        query = client.table.return_value.update.return_value
        query.eq.assert_called_with("id", "c")
        query.eq.return_value.eq.assert_called_with("user_id", "u")
        query.eq.return_value.eq.return_value.eq.assert_called_with("case_revision", 3)

    def test_new_sensitive_writes_require_encryption(self):
        client = mock.MagicMock()
        with mock.patch("supabase_service.crypto_service.is_enabled", return_value=False):
            with self.assertRaises(RuntimeError):
                SupabaseService(client=client).create_form_draft(client, "c", "u", FULL)
        client.table.return_value.insert.assert_not_called()

    def test_unreadable_context_is_not_returned_as_empty(self):
        client = mock.MagicMock()
        client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"case_context": "bad", "case_revision": 1}]
        with self.assertRaises(Exception):
            SupabaseService(client=client).get_case_context(client, "c")
