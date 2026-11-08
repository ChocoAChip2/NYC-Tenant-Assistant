"""Asking for feedback at a good moment (feedback.py, POST /feedback).

Owner, 2026-11-08: ask when a longer chat is coming to an end, not in the
middle of someone's problem; keep the answer without tying it to anyone.
"""

import os
import re
import unittest
from unittest import mock

import crypto_service
import feedback
from tests.test_conversation_auto_title import FakeAI, FakeSupabase, _client, _send
from tests.app_test_support import reset_rate_limits

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
KEY_ENV = {"DATA_ENCRYPTION_KEYS": "k1:" + "A" * 43 + "=", "DATA_ENCRYPTION_ACTIVE_KEY_ID": "k1",
           "LEGAL_CORPUS_ENABLED": ""}


class SignOffTests(unittest.TestCase):
    def test_goodbyes_and_thanks(self):
        for text in ("Thanks!", "thank you so much", "ok got it, thanks", "That's all", "that’s all for now",
                     "This helps a lot", "bye", "Gracias", "merci beaucoup", "Asante", "谢谢"):
            with self.subTest(text=text):
                self.assertTrue(feedback.is_sign_off(text))

    def test_not_a_sign_off(self):
        for text in ("", "my heat is off", "thanks, but what about the deposit?", "Thanksgiving week the heat broke",
                     "bye " + "x" * 120, "thanks？"):
            with self.subTest(text=text):
                self.assertFalse(feedback.is_sign_off(text))


class OfferTests(unittest.TestCase):
    def test_short_chats_are_never_asked(self):
        self.assertEqual(feedback.offer(2, "thanks", False), {"now": False, "idle_seconds": 0})

    def test_a_sign_off_after_a_real_chat_asks_now(self):
        self.assertEqual(feedback.offer(3, "thanks, that's all", False), {"now": True, "idle_seconds": 0})

    def test_a_longer_chat_asks_after_a_pause(self):
        self.assertEqual(feedback.offer(4, "and the deposit", False), {"now": False, "idle_seconds": 0})
        self.assertEqual(feedback.offer(5, "and the deposit", False), {"now": False, "idle_seconds": feedback.IDLE_SECONDS})

    def test_asked_recently_means_not_again(self):
        self.assertEqual(feedback.offer(9, "thanks", True), {"now": False, "idle_seconds": 0})


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, KEY_ENV)
        patcher.start()
        crypto_service.reload_keys()
        self.addCleanup(crypto_service.reload_keys)
        self.addCleanup(patcher.stop)

    def test_a_sent_answer_is_checked_and_its_comment_encrypted(self):
        args = feedback.submission({"action": "sent", "rating": "somewhat", "trigger": "idle",
                                    "comment": "  more   on deposits  "})
        self.assertEqual(args["p_rating"], 2)
        self.assertEqual(args["p_trigger"], "idle")
        self.assertTrue(args["p_comment"].startswith("enc:v"))
        self.assertEqual(crypto_service.decrypt(args["p_comment"]), "more on deposits")

    def test_no_comment_is_null(self):
        args = feedback.submission({"action": "sent", "rating": "helpful", "trigger": "closing", "comment": "  "})
        self.assertIsNone(args["p_comment"])

    def test_later_and_never_store_nothing(self):
        self.assertIsNone(feedback.submission({"action": "later"}))
        self.assertIsNone(feedback.submission({"action": "never"}))

    def test_malformed_answers_are_refused(self):
        for payload in ({}, {"action": "sent", "rating": "great", "trigger": "idle"},
                        {"action": "sent", "rating": "helpful", "trigger": "other"}, {"action": "delete"}):
            with self.subTest(payload=payload), self.assertRaises(feedback.Invalid):
                feedback.submission(payload)

    def test_a_long_comment_is_cut(self):
        args = feedback.submission({"action": "sent", "rating": "helpful", "trigger": "idle", "comment": "x" * 5000})
        self.assertEqual(len(crypto_service.decrypt(args["p_comment"])), feedback.MAX_COMMENT)

    def test_without_encryption_the_comment_is_dropped(self):
        with mock.patch.dict(os.environ, {"DATA_ENCRYPTION_KEYS": "", "DATA_ENCRYPTION_ACTIVE_KEY_ID": ""}):
            crypto_service.reload_keys()
            args = feedback.submission({"action": "sent", "rating": "helpful", "trigger": "idle", "comment": "hi"})
        self.assertIsNone(args["p_comment"])


class FakeRpc:
    def __init__(self, fail):
        self.fail = fail

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")


class FakeUserClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def rpc(self, name, params):
        self.calls.append((name, params))
        return FakeRpc(self.fail)


class FeedbackSupabase(FakeSupabase):
    def __init__(self, fail=False, **kw):
        super().__init__(**kw)
        self.user_client = FakeUserClient(fail)

    def build_user_scoped_client(self, access_token):
        return self.user_client


class RouteTests(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()
        patcher = mock.patch.dict(os.environ, KEY_ENV)
        patcher.start()
        crypto_service.reload_keys()
        self.addCleanup(crypto_service.reload_keys)
        self.addCleanup(patcher.stop)

    def test_logged_out_is_refused(self):
        client = _client(FeedbackSupabase(), FakeAI())
        with client.session_transaction() as s:
            s.clear()
        self.assertEqual(client.post("/feedback", json={"action": "later"}).status_code, 401)

    def test_an_answer_is_stored_and_the_question_rests_for_a_month(self):
        service = FeedbackSupabase()
        response = _client(service, FakeAI()).post("/feedback", json={
            "action": "sent", "rating": "helpful", "trigger": "closing", "comment": "clear and quick"})
        self.assertEqual(response.status_code, 200)
        name, params = service.user_client.calls[0]
        self.assertEqual(name, "submit_feedback")
        self.assertEqual(params["p_rating"], 3)
        self.assertEqual(params["p_language"], "en")
        self.assertNotIn("clear", params["p_comment"])
        cookie = response.headers["Set-Cookie"]
        self.assertIn("feedback_asked=1", cookie)
        self.assertIn("Max-Age=%d" % (30 * 24 * 3600), cookie)
        self.assertIn("HttpOnly", cookie)

    def test_not_now_rests_three_days_and_stores_nothing(self):
        service = FeedbackSupabase()
        response = _client(service, FakeAI()).post("/feedback", json={"action": "later"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.user_client.calls, [])
        self.assertIn("Max-Age=%d" % (3 * 24 * 3600), response.headers["Set-Cookie"])

    def test_bad_answers_and_database_trouble(self):
        self.assertEqual(_client(FeedbackSupabase(), FakeAI()).post("/feedback", json={"action": "sent"}).status_code, 400)
        response = _client(FeedbackSupabase(fail=True), FakeAI()).post("/feedback", json={
            "action": "sent", "rating": "helpful", "trigger": "idle"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Set-Cookie", response.headers)

    def _history(self, turns):
        rows = []
        for i in range(turns - 1):
            rows += [{"role": "user", "content": f"question {i}"}, {"role": "assistant", "content": "answer"}]
        return rows

    def test_the_chat_reply_says_when_to_ask(self):
        service = FeedbackSupabase(history=self._history(3) + [{"role": "user", "content": "thanks, that's all"}])
        body = _send(_client(service, FakeAI()), "thanks, that's all").get_json()
        self.assertEqual(body["feedback"], {"now": True, "idle_seconds": 0})

    def test_a_short_chat_is_not_asked(self):
        body = _send(_client(FeedbackSupabase(), FakeAI()), "thanks").get_json()
        self.assertEqual(body["feedback"], {"now": False, "idle_seconds": 0})

    def test_after_answering_the_chat_doesnt_ask_again(self):
        client = _client(FeedbackSupabase(history=self._history(6)), FakeAI())
        client.set_cookie(feedback.COOKIE, "1")
        body = _send(client, "thanks").get_json()
        self.assertEqual(body["feedback"], {"now": False, "idle_seconds": 0})


class PageTests(unittest.TestCase):
    def test_the_question_is_on_the_chat_page_and_hidden_until_offered(self):
        with open(os.path.join(TEMPLATES, "chat.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('id="feedback-card" aria-labelledby="feedback-title" hidden', page)
        self.assertIn("feedbackPrompt.consider(payload.feedback)", page)
        self.assertIn('maxlength="%d"' % feedback.MAX_COMMENT, page)
        for rating in feedback.RATINGS:
            self.assertIn('data-feedback-rating="%s"' % rating, page)
        self.assertEqual(set(re.findall(r'data-feedback-dismiss="(\w+)"', page)), set(feedback.SNOOZE_DAYS) - {"sent"})


class MigrationTests(unittest.TestCase):
    def test_the_table_is_closed_except_through_the_function(self):
        folder = os.path.join(os.path.dirname(TEMPLATES), "supabase", "migrations")
        sql = next(open(os.path.join(folder, n)).read() for n in os.listdir(folder) if n.endswith("_feedback.sql"))
        self.assertIn("revoke all on table public.feedback from public, anon, authenticated", sql)
        self.assertIn("grant execute on function public.submit_feedback(integer, text, text, text) to authenticated", sql)
        self.assertIn("security definer", sql)
        self.assertIn("p_comment not like 'enc:v%'", sql)
        self.assertNotIn("user_id", sql)
        self.assertIn("interval '12 months'", sql)


if __name__ == "__main__":
    unittest.main()
