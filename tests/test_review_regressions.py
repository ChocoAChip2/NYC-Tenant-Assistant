"""User-flow regressions found during the October 6 review."""

from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import unittest
from unittest import mock

from flask import Flask

from routes import main_bp
from tests.app_test_support import SIGNUP_PROFILE, configure_test_app
from tests.review_support import MemorySupabase, ReviewAI
from supabase_service import SupabaseService


class ReviewJourneyTests(unittest.TestCase):
    def setUp(self):
        self.service = MemorySupabase()
        self.ai = ReviewAI()
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        self.app.secret_key = "review-only"
        self.app.config.update(SUPABASE_SERVICE=self.service, AI_SERVICE=self.ai, TESTING=True)
        configure_test_app(self.app)
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id="review-user", user_email="review@example.invalid", access_token="review-token")

    def new_chat(self):
        response = self.client.post("/conversations")
        return response.location.split("conversation_id=")[1]

    def send(self, cid, content):
        return self.client.post("/chat/message", json={"conversation_id": cid, "content": content})

    def test_new_chat_in_second_tab_preserves_first_tabs_draft_destination(self):
        first = self.new_chat()
        second = self.new_chat()
        self.assertNotEqual(first, second)
        self.assertEqual(self.send(first, "My radiator is broken").status_code, 200)

    def test_retry_after_ai_failure_does_not_duplicate_tenants_message(self):
        cid = self.new_chat()
        self.assertEqual(self.send(cid, "Please fail once").status_code, 503)
        self.assertEqual(self.send(cid, "Please fail once").status_code, 200)
        self.assertEqual([m["role"] for m in self.service.messages[cid]], ["user", "assistant"])

    def test_conversation_lookup_outage_returns_json(self):
        cid = self.new_chat()
        with mock.patch.object(self.service, "ensure_conversation_for_user", side_effect=OSError("database down")):
            response = self.send(cid, "Help with my heat")
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.get_json())

    def test_repeating_a_message_after_a_success_is_a_new_turn(self):
        cid = self.new_chat()
        for _ in range(2):
            self.assertEqual(self.send(cid, "What about heat?").status_code, 200)
        self.assertEqual(len(self.service.messages[cid]), 4)

    def test_changed_message_after_failure_is_not_discarded(self):
        cid = self.new_chat()
        self.send(cid, "Please fail once")
        self.send(cid, "Actually, my problem is a water leak")
        self.assertEqual(self.ai.histories[-1][-1]["content"], "Actually, my problem is a water leak")

    def test_messages_from_other_conversations_never_reach_the_model(self):
        first, second = self.new_chat(), self.new_chat()
        self.send(first, "Private deposit problem")
        self.send(second, "Separate heating problem")
        self.assertNotIn("Private deposit problem", str(self.ai.histories[-1]))

    def test_failed_source_formatting_cannot_leak_unresolvable_markers(self):
        cid = self.new_chat()
        with mock.patch("routes.retrieval_service.is_enabled", return_value=True), \
             mock.patch("routes.retrieval_service.RetrievalService") as retrieval, \
             mock.patch("routes.retrieval_service.format_for_prompt", side_effect=ValueError("bad corpus")), \
             mock.patch.object(self.ai, "generate_reply", return_value="Check with HPD. [S1]"):
            retrieval.return_value.search.return_value = [object()]
            response = self.send(cid, "Question about heating")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("[S1]", response.get_json()["reply"])
        self.assertNotIn("[S1]", self.service.messages[cid][-1]["content"])

    def test_auth_errors_do_not_disclose_internal_exception_details(self):
        secret_detail = "INTERNAL_ONLY supabase.invalid/private_table service_role example-secret"
        cases = [
            ("sign_in", "/login", {"email": "review@example.invalid", "password": "example-password"}, "Login failed"),
            ("sign_up", "/signup", {"email": "review@example.invalid", "password": "example-password", **SIGNUP_PROFILE}, "Sign-up failed"),
            ("update_account", "/settings/account", {"email": "new@example.invalid"}, "Could not update account"),
        ]
        with self.client.session_transaction() as session:
            session["refresh_token"] = "review-refresh"
        for method, path, data, expected in cases:
            with self.subTest(path=path), \
                 mock.patch.object(self.service, method, side_effect=RuntimeError(secret_detail), create=True), \
                 mock.patch("routes.password_safety.is_breached", return_value=False):
                response = self.client.post(path, data=data, follow_redirects=True)
                body = response.get_data(as_text=True)
                self.assertIn(expected, body)
                for forbidden in ("INTERNAL_ONLY", "supabase.invalid", "service_role", "example-secret"):
                    self.assertNotIn(forbidden, body)

    def test_source_and_configuration_files_are_not_web_routes(self):
        for path in ("/.env", "/.git/config", "/routes.py", "/config.py", "/templates/chat.html",
                     "/docs/data-encryption.md", "/supabase/migrations/20260920_setup_vector_db.sql"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_archive_restore_export_and_delete_journey(self):
        cid = self.new_chat()
        self.send(cid, "My radiator is broken")
        self.client.post(f"/conversations/{cid}/rename", data={"title": "Bedroom radiator"})
        self.client.post(f"/conversations/{cid}/archive")
        self.assertTrue(self.service.conversations[cid]["archived_at"])
        export = self.client.get("/settings/download-logs").get_data(as_text=True)
        self.assertIn("Bedroom radiator", export)
        self.assertIn("My radiator is broken", export)
        self.client.post(f"/conversations/{cid}/unarchive")
        self.assertIsNone(self.service.conversations[cid]["archived_at"])
        self.client.post(f"/conversations/{cid}/delete")
        self.assertEqual(self.send(cid, "Hello").status_code, 404)


class AuthIsolationTests(unittest.TestCase):
    def test_sign_in_never_establishes_a_session_on_shared_client(self):
        shared, isolated = mock.MagicMock(), mock.MagicMock()
        service = SupabaseService(client=shared)
        with mock.patch("supabase_service.create_client", return_value=isolated) as factory:
            service.sign_in("tenant@example.invalid", "test-password")
        shared.auth.sign_in_with_password.assert_not_called()
        isolated.auth.sign_in_with_password.assert_called_once()
        options = factory.call_args.kwargs["options"]
        self.assertFalse(options.auto_refresh_token)
        self.assertFalse(options.persist_session)

    def test_signup_never_establishes_a_session_on_shared_client(self):
        shared, isolated = mock.MagicMock(), mock.MagicMock()
        service = SupabaseService(client=shared)
        with mock.patch("supabase_service.create_client", return_value=isolated):
            service.sign_up("tenant@example.invalid", "test-password")
        shared.auth.sign_up.assert_not_called()
        isolated.auth.sign_up.assert_called_once()


class PagedQuery:
    """Models the API's row cap, including inclusive range boundaries."""

    def __init__(self, rows):
        self.rows = rows
        self.start, self.end = 0, 999
        self.orders = []
        self.ranges = []

    def select(self, *args):
        return self

    def eq(self, *args):
        return self

    def is_(self, *args):
        return self

    @property
    def not_(self):
        return self

    def order(self, column, **kwargs):
        self.orders.append(column)
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        self.ranges.append((start, end))
        return self

    def execute(self):
        return SimpleNamespace(data=deepcopy(self.rows[self.start:min(self.end + 1, self.start + 1000)]))


class FullHistoryTests(unittest.TestCase):
    def test_history_over_api_cap_includes_latest_turn(self):
        query = PagedQuery([dict(id=str(i), role="user", content=f"Turn {i}", created_at="2026-10-06")
                            for i in range(1205)])
        client = mock.Mock()
        client.table.return_value = query
        rows = SupabaseService(client=client).fetch_messages_for_conversation(client, "c1")
        self.assertEqual(len(rows), 1205)
        self.assertEqual(rows[-1]["content"], "Turn 1204")
        self.assertEqual(query.orders, ["created_at", "id"])

    def test_conversation_list_over_api_cap_is_complete(self):
        query = PagedQuery([dict(id=str(i), title=f"Chat {i}") for i in range(1101)])
        client = mock.Mock()
        client.table.return_value = query
        rows = SupabaseService(client=client).list_conversations(client)
        self.assertEqual(len(rows), 1101)
        self.assertEqual(rows[-1]["title"], "Chat 1100")
        self.assertEqual(query.orders, ["updated_at", "id"])
