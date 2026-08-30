"""Tests for the Gemini fallback chain, title cleanup, and key loading.

These cover the logic that keeps a chat replying when a model is retired or a
key runs out of quota. The Gemini SDK is never contacted: fake clients raise
the same error types the real one does.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.genai.errors import ClientError, ServerError

import ai_service
from ai_service import AIService, _clean_title, _is_model_unavailable, _is_quota_error
from config import load_gemini_api_keys


def quota_error() -> ClientError:
    return ClientError(429, {"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})


def not_found_error() -> ClientError:
    return ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}})


def server_error() -> ServerError:
    return ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.behaviour(model, len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return mock.Mock(text=outcome)


class FakeClient:
    def __init__(self, behaviour):
        self.models = FakeModels(behaviour)


def build_service(*behaviours) -> AIService:
    return AIService(clients=tuple(FakeClient(b) for b in behaviours))


class ErrorClassificationTests(unittest.TestCase):
    def test_quota_error_detected(self):
        self.assertTrue(_is_quota_error(quota_error()))
        self.assertFalse(_is_quota_error(not_found_error()))

    def test_model_unavailable_detected(self):
        self.assertTrue(_is_model_unavailable(not_found_error()))
        self.assertFalse(_is_model_unavailable(quota_error()))


class TitleCleanupTests(unittest.TestCase):
    def test_strips_quotes_prefix_and_trailing_punctuation(self):
        self.assertEqual(_clean_title('"Broken Heat Complaint."'), "Broken Heat Complaint")
        self.assertEqual(_clean_title("Title: Illegal Eviction Notice"), "Illegal Eviction Notice")

    def test_caps_at_four_words(self):
        self.assertEqual(
            _clean_title("Landlord Refuses To Return My Security Deposit"),
            "Landlord Refuses To Return",
        )

    def test_collapses_whitespace(self):
        self.assertEqual(_clean_title("  Mold   In\nBathroom  "), "Mold In Bathroom")


class FallbackChainTests(unittest.TestCase):
    def setUp(self):
        # Keep the retry backoff from making the suite slow.
        patcher = mock.patch.object(ai_service, "BACKOFF_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_first_successful_model(self):
        service = build_service(lambda model, n: "hello")
        self.assertEqual(service.generate_reply([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(service.clients[0].models.calls, [ai_service.FALLBACK_MODELS[0]])

    def test_retired_model_falls_through_to_the_next_model(self):
        first, second = ai_service.FALLBACK_MODELS[0], ai_service.FALLBACK_MODELS[1]
        service = build_service(lambda model, n: not_found_error() if model == first else "recovered")

        self.assertEqual(service.generate_reply([{"role": "user", "content": "hi"}]), "recovered")
        self.assertEqual(service.clients[0].models.calls, [first, second])

    def test_exhausted_key_falls_through_to_the_next_key(self):
        exhausted = build_service(lambda model, n: quota_error()).clients[0]
        service = AIService(clients=(exhausted, FakeClient(lambda model, n: "second key answered")))

        self.assertEqual(
            service.generate_reply([{"role": "user", "content": "hi"}]),
            "second key answered",
        )
        # The healthy key answers on the first model, so no downgrade happened.
        self.assertEqual(service.clients[1].models.calls, [ai_service.FALLBACK_MODELS[0]])

    def test_retired_model_is_not_retried_against_other_keys(self):
        first = ai_service.FALLBACK_MODELS[0]
        service = build_service(
            lambda model, n: not_found_error() if model == first else "ok",
            lambda model, n: not_found_error() if model == first else "ok",
        )
        service.generate_reply([{"role": "user", "content": "hi"}])

        # Key #2 must never be asked for a model key #1 proved is gone.
        self.assertNotIn(first, service.clients[1].models.calls)

    def test_transient_server_error_is_retried_in_place(self):
        service = build_service(lambda model, n: server_error() if n == 1 else "recovered")
        self.assertEqual(service.generate_reply([{"role": "user", "content": "hi"}]), "recovered")
        self.assertEqual(service.clients[0].models.calls.count(ai_service.FALLBACK_MODELS[0]), 2)

    def test_raises_when_every_combination_fails(self):
        service = build_service(lambda model, n: quota_error(), lambda model, n: quota_error())
        with self.assertRaises(RuntimeError):
            service.generate_reply([{"role": "user", "content": "hi"}])

    def test_non_recoverable_client_error_propagates(self):
        bad_request = ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}})
        service = build_service(lambda model, n: bad_request)
        with self.assertRaises(ClientError):
            service.generate_reply([{"role": "user", "content": "hi"}])

    def test_generate_title_cleans_the_model_output(self):
        service = build_service(lambda model, n: '"Broken Heat Complaint."')
        self.assertEqual(service.generate_title("No heat since Tuesday", "Sorry to hear"), "Broken Heat Complaint")

    def test_generate_title_prefers_the_cheap_model(self):
        service = build_service(lambda model, n: "Mold In Bathroom")
        service.generate_title("There is mold", "")
        self.assertEqual(service.clients[0].models.calls[0], ai_service.TITLE_MODEL)


class KeyLoadingTests(unittest.TestCase):
    def test_comma_separated_and_numbered_slots_are_merged(self):
        env = {"GEMINI_API_KEY": "a, b", "GEMINI_API_KEY_2": "c"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_gemini_api_keys(), ("a", "b", "c"))

    def test_duplicates_are_dropped(self):
        env = {"GEMINI_API_KEY": "a", "GEMINI_API_KEY_2": "a", "GEMINI_API_KEY_3": "b"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_gemini_api_keys(), ("a", "b"))

    def test_missing_keys_produce_an_empty_tuple(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_gemini_api_keys(), ())


if __name__ == "__main__":
    unittest.main()
