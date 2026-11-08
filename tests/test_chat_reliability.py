"""Chat reliability fixes from the 2026-11-08 outage (log/2026-11-08-chat-models.txt)."""

import os
import unittest
from unittest import mock

from tests.test_conversation_auto_title import FakeAI, FakeSupabase, _client, _send

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class DeletedMidReply(FakeSupabase):
    def insert_message(self, user_client, message):
        if message["role"] == "assistant":
            self.deleted = True
            raise RuntimeError("new row violates row-level security policy")
        super().insert_message(user_client, message)

    def ensure_conversation_for_user(self, user_client, conversation_id, user_id):
        if getattr(self, "deleted", False):
            raise ValueError("gone")


class Broken(FakeSupabase):
    def insert_message(self, user_client, message):
        if message["role"] == "assistant":
            raise RuntimeError("db down")
        super().insert_message(user_client, message)


@mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": ""})
class SaveReplyTests(unittest.TestCase):
    def test_a_conversation_deleted_while_replying_is_a_404_not_an_outage(self):
        response = _send(_client(DeletedMidReply(), FakeAI()))
        self.assertEqual(response.status_code, 404)
        self.assertIn("deleted", response.get_json()["error"])

    def test_other_save_failures_are_still_errors(self):
        self.assertGreaterEqual(_send(_client(Broken(), FakeAI())).status_code, 500)


class SlowReplyPageTests(unittest.TestCase):
    def test_a_gateway_timeout_says_the_reply_may_still_arrive(self):
        with open(os.path.join(TEMPLATES, "chat.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("[502, 504, 522, 524].includes(response.status)", page)
        self.assertIn("Your reply may still arrive", page)


if __name__ == "__main__":
    unittest.main()
