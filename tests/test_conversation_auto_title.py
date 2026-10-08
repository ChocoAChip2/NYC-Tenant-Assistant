"""New chats are named from their first message (conversation_titles.py).

Found on the live site, 2026-10-01: every chat started with "+ New chat"
stayed "New conversation" forever.
"""

import os
import unittest
from unittest import mock

import flask

import conversation_titles
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_conversation_create_rename_and_cleanup import _logged_in_session

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class FakeSupabase:
    def __init__(self, title="New conversation", history=None, fail_title_read=False):
        self.title = title
        self.history = history
        self.messages = []
        self.renames = []
        self.fail_title_read = fail_title_read

    def build_user_scoped_client(self, access_token):
        return object()

    def ensure_conversation_for_user(self, user_client, conversation_id, user_id):
        return None

    def insert_message(self, user_client, message):
        self.messages.append(message)

    def fetch_messages_for_conversation(self, user_client, conversation_id):
        if self.history is not None:
            return self.history
        return [{"role": m["role"], "content": m["content"]} for m in self.messages]

    def get_conversation_title(self, user_client, conversation_id, user_id):
        if self.fail_title_read:
            raise RuntimeError("db down")
        return self.title

    def rename_conversation(self, user_client, conversation_id, user_id, title):
        self.renames.append(title)
        self.title = title

    def get_case_context(self, client, conversation_id):
        import case_context
        return {"context": case_context.defaults(), "revision": 0}


class FakeAI:
    def __init__(self, title="Broken radiator in bedroom", fail_title=False):
        self.title = title
        self.fail_title = fail_title
        self.title_calls = []

    def is_ready(self):
        return True

    def generate_reply(self, messages):
        return "Your landlord must keep the apartment at least 68 degrees during the day."

    def generate_title(self, user_message, assistant_message=""):
        self.title_calls.append(user_message)
        if self.fail_title:
            raise RuntimeError("no model")
        return self.title


def _client(service, ai):
    app = flask.Flask(__name__, template_folder=TEMPLATES)
    app.secret_key = "test"
    app.config["SUPABASE_SERVICE"] = service
    app.config["AI_SERVICE"] = ai
    app.register_blueprint(main_bp)
    configure_test_app(app)
    client = app.test_client()
    _logged_in_session(client)
    return client


def _send(client, text="The radiator in my bedroom has been broken for a week"):
    return client.post("/chat/message", json={"conversation_id": "c1", "content": text})


@mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": ""})
class RouteTests(unittest.TestCase):
    def test_first_reply_names_a_new_conversation(self):
        service, ai = FakeSupabase(), FakeAI()
        response = _send(_client(service, ai))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["title"], "Broken radiator in bedroom")
        self.assertEqual(service.renames, ["Broken radiator in bedroom"])

    def test_a_title_the_tenant_chose_is_never_replaced(self):
        service, ai = FakeSupabase(title="My building"), FakeAI()
        response = _send(_client(service, ai))
        self.assertNotIn("title", response.get_json())
        self.assertEqual(service.renames, [])
        self.assertEqual(ai.title_calls, [])

    def test_only_the_first_exchange_names_it(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "my heat is off"},
        ]
        service, ai = FakeSupabase(history=history), FakeAI()
        response = _send(_client(service, ai), "my heat is off")
        self.assertNotIn("title", response.get_json())
        self.assertEqual(service.renames, [])

    def test_model_failure_falls_back_to_a_topic_title(self):
        service, ai = FakeSupabase(), FakeAI(fail_title=True)
        response = _send(_client(service, ai), "There's been no heat or hot water since Monday")
        self.assertEqual(response.get_json()["title"], "Heat and hot water")

    def test_no_usable_title_keeps_the_default(self):
        service, ai = FakeSupabase(), FakeAI(title='"New conversation"')
        response = _send(_client(service, ai), "hello there")
        self.assertNotIn("title", response.get_json())
        self.assertEqual(service.renames, [])

    def test_naming_trouble_never_breaks_the_reply(self):
        service, ai = FakeSupabase(fail_title_read=True), FakeAI()
        response = _send(_client(service, ai))
        self.assertEqual(response.status_code, 200)
        self.assertIn("68 degrees", response.get_json()["reply"])
        self.assertNotIn("title", response.get_json())


class CleanTests(unittest.TestCase):
    def test_strips_quotes_prefixes_and_punctuation(self):
        self.assertEqual(conversation_titles.clean('Title: "landlord kept my deposit."'), "Landlord kept my deposit")

    def test_caps_words_and_length(self):
        title = conversation_titles.clean("one two three four five six seven eight")
        self.assertEqual(title, "One two three four five six")
        self.assertLessEqual(len(conversation_titles.clean("x" * 200)), conversation_titles.MAX_CHARS)

    def test_rejects_empty_default_and_symbol_only(self):
        for raw in ("", None, "   ", "New conversation", "...", "**"):
            with self.subTest(raw=raw):
                self.assertEqual(conversation_titles.clean(raw), "")

    def test_takes_the_first_line_only(self):
        self.assertEqual(conversation_titles.clean("Mold in bathroom\nHere is why"), "Mold in bathroom")


class KeywordTests(unittest.TestCase):
    def test_topics(self):
        cases = {
            "I got court papers for a nonpayment case": "Eviction case",
            "no heat in my apartment": "Heat and hot water",
            "how do I fill out the RA-81": "RA-81 rent reduction",
            "they kept my security deposit": "Security deposit",
            "there are mice everywhere": "Pests",
            "is my apartment rent stabilized": "Rent stabilization",
        }
        for text, title in cases.items():
            with self.subTest(text=text):
                self.assertEqual(conversation_titles.from_keywords(text), title)

    def test_never_echoes_the_tenants_words(self):
        self.assertEqual(conversation_titles.from_keywords("Hi, I'm Ana Rivera at 12 Main St apt 4B"), "")


class OlderChats(FakeSupabase):
    def __init__(self, title, messages):
        super().__init__(title=title)
        self.stored = messages

    def list_conversations(self, user_client, archived=False):
        if archived:
            return []
        return [{"id": "c1", "title": self.title}, {"id": "c2", "title": "New conversation"}]

    def fetch_messages_for_conversation(self, user_client, conversation_id):
        return [dict(m, created_at="") for m in self.stored]

    def get_pending_account_deletion(self, user_client, user_id):
        return None

    def get_case_context(self, client, conversation_id):
        import case_context
        return {"context": case_context.defaults(), "revision": 0}


OLD_CHAT = [{"role": "user", "content": "My landlord kept my deposit"}, {"role": "assistant", "content": "Under GOL 7-108..."}]


class OlderConversationTests(unittest.TestCase):
    def _open(self, service, ai):
        client = _client(service, ai)
        with mock.patch("account_requirements.is_current", return_value=True):
            return client.get("/chat?conversation_id=c1")

    def test_an_older_default_titled_chat_is_named_when_opened(self):
        service, ai = OlderChats("New conversation", OLD_CHAT), FakeAI(title="Landlord kept security deposit")
        body = self._open(service, ai).get_data(as_text=True)
        self.assertEqual(service.renames, ["Landlord kept security deposit"])
        self.assertIn("Landlord kept security deposit", body)
        self.assertEqual(ai.title_calls, ["My landlord kept my deposit"])

    def test_named_or_empty_chats_are_left_alone(self):
        for title, messages in (("Deposit help", OLD_CHAT), ("New conversation", [])):
            with self.subTest(title=title):
                service, ai = OlderChats(title, messages), FakeAI()
                self.assertEqual(self._open(service, ai).status_code, 200)
                self.assertEqual(service.renames, [])
                self.assertEqual(ai.title_calls, [])


class GenerateTitleTests(unittest.TestCase):
    def _service(self, outcomes):
        from types import SimpleNamespace

        import ai_service

        calls = []

        def generate_content(model, contents, config):
            calls.append((model, config))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(text=outcome)

        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        return ai_service.AIService(client=client), calls

    def test_cheapest_model_first_without_thinking(self):
        service, calls = self._service(["Mold in bathroom"])
        self.assertEqual(service.generate_title("there is mold"), "Mold in bathroom")
        model, config = calls[0]
        self.assertEqual(model, "gemini-2.5-flash-lite")
        self.assertEqual(config["thinking_config"], {"thinking_budget": 0})
        self.assertIn("Never include a person's name", config["system_instruction"])

    def test_model_errors_fall_through_and_never_raise(self):
        from google.genai.errors import ClientError, ServerError

        busy = ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
        quota = ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        service, calls = self._service([busy, "Heat outage"])
        self.assertEqual(service.generate_title("no heat"), "Heat outage")
        service, calls = self._service([busy, quota])
        self.assertEqual(service.generate_title("no heat"), "")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
