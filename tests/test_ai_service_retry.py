"""Tests for AIService.generate_reply's handling of transient Gemini errors.

Root-caused a real production symptom: the chat page would show "Sorry, I
couldn't get a response right now" on what turned out to be a single 503
from Gemini. The SDK raises a 503 as ServerError, not ClientError -- and
the retry loop only ever caught ClientError, so a ServerError skipped the
fallback-model loop entirely on the very first transient hiccup. These
tests drive AIService.generate_reply directly with a mocked genai.Client,
so no real Gemini call is made and no real time.sleep delay happens
(time.sleep is patched out).
"""

import unittest
from unittest import mock

from google.genai.errors import ClientError, ServerError

from ai_service import AIService, FALLBACK_MODELS


def _server_error(status="UNAVAILABLE"):
    return ServerError(503, {"error": {"message": "The model is overloaded.", "status": status}})


def _client_error(code=404, status="NOT_FOUND", message="model not found"):
    return ClientError(code, {"error": {"message": message, "status": status}})


def _rate_limit_error():
    return ClientError(
        429,
        {"error": {"message": "Quota exceeded for quota metric 'Generate Content API requests per minute'.", "status": "RESOURCE_EXHAUSTED"}},
    )


def _fake_response(text):
    return mock.MagicMock(text=text)


class GenerateReplySuccessAfterRetryTests(unittest.TestCase):
    @mock.patch("ai_service.time.sleep")
    def test_retries_same_model_after_a_single_503_then_succeeds(self, mock_sleep):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [
            _server_error(),
            _fake_response("Here is the housing law answer."),
        ]
        service = AIService(client=client)

        reply = service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertEqual(reply, "Here is the housing law answer.")
        self.assertEqual(client.models.generate_content.call_count, 2)
        # Both calls were for the same (first) fallback model -- a transient
        # error should not immediately burn through to the next model.
        self.assertEqual(
            client.models.generate_content.call_args_list[0].kwargs["model"],
            client.models.generate_content.call_args_list[1].kwargs["model"],
        )
        mock_sleep.assert_called_once()

    @mock.patch("ai_service.time.sleep")
    def test_falls_back_to_next_model_after_exhausting_retries_on_first(self, mock_sleep):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [
            _server_error(),  # first model, attempt 1
            _server_error(),  # first model, attempt 2 (retry) -- still failing
            _fake_response("Answer from the fallback model."),  # second model succeeds
        ]
        service = AIService(client=client)

        reply = service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertEqual(reply, "Answer from the fallback model.")
        self.assertEqual(client.models.generate_content.call_count, 3)
        models_tried = [call.kwargs["model"] for call in client.models.generate_content.call_args_list]
        self.assertEqual(models_tried, [FALLBACK_MODELS[0], FALLBACK_MODELS[0], FALLBACK_MODELS[1]])


class GenerateReplyExhaustedTests(unittest.TestCase):
    @mock.patch("ai_service.time.sleep")
    def test_raises_runtime_error_after_every_model_and_retry_fails(self, mock_sleep):
        client = mock.MagicMock()
        # every fallback model x (1 initial attempt + 1 retry), all 503.
        calls = 2 * len(FALLBACK_MODELS)
        client.models.generate_content.side_effect = [_server_error() for _ in range(calls)]
        service = AIService(client=client)

        with self.assertRaises(RuntimeError) as ctx:
            service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertIn("temporarily unavailable", str(ctx.exception))
        self.assertEqual(client.models.generate_content.call_count, calls)


class ModelNotFoundStillSkipsToNextModelTests(unittest.TestCase):
    def test_404_moves_to_next_model_without_retrying_the_same_one(self):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [
            _client_error(),  # first model doesn't exist for this key
            _fake_response("Answer from the model that does exist."),
        ]
        service = AIService(client=client)

        reply = service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertEqual(reply, "Answer from the model that does exist.")
        # No retry on the same (nonexistent) model -- straight to the next one.
        self.assertEqual(client.models.generate_content.call_count, 2)

    def test_non_404_client_error_is_raised_immediately_without_retry(self):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = _client_error(
            code=400, status="INVALID_ARGUMENT", message="Request contained an invalid argument."
        )
        service = AIService(client=client)

        with self.assertRaises(ClientError):
            service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertEqual(client.models.generate_content.call_count, 1)


class RateLimitedRequestsFallBackToNextModelTests(unittest.TestCase):
    """A 429/RESOURCE_EXHAUSTED means this model's quota is used up for the
    current window -- the other real cause (besides a 503) behind "Sorry, I
    couldn't get a response right now" showing up after a handful of
    messages, once the free tier's per-minute quota for one model runs out.
    """

    @mock.patch("ai_service.time.sleep")
    def test_429_moves_to_next_model_without_retrying_the_same_one(self, mock_sleep):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [
            _rate_limit_error(),  # first model's quota is exhausted
            _fake_response("Answer from the model with quota left."),
        ]
        service = AIService(client=client)

        reply = service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertEqual(reply, "Answer from the model with quota left.")
        models_tried = [call.kwargs["model"] for call in client.models.generate_content.call_args_list]
        self.assertEqual(models_tried, [FALLBACK_MODELS[0], FALLBACK_MODELS[1]])
        # No point retrying an exhausted quota within the same request -- it
        # won't have reset yet, so this should move on immediately, not sleep.
        mock_sleep.assert_not_called()

    def test_rate_limit_on_every_model_raises_runtime_error(self):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [_rate_limit_error() for _ in FALLBACK_MODELS]
        service = AIService(client=client)

        with self.assertRaises(RuntimeError) as ctx:
            service.generate_reply([{"role": "user", "content": "What are my rights?"}])

        self.assertIn("temporarily unavailable", str(ctx.exception))
        self.assertEqual(client.models.generate_content.call_count, len(FALLBACK_MODELS))


if __name__ == "__main__":
    unittest.main()


class CurrentModelsTests(unittest.TestCase):
    """2026-11-08: replies stopped on 2026-10-15 once the 2.5 models were no
    longer open to this key. These pin the fix."""

    def test_no_retired_model_is_first_in_line(self):
        import ai_service
        for models in (ai_service.FALLBACK_MODELS, ai_service.TITLE_MODELS):
            self.assertFalse(models[0].startswith("gemini-2."), models)
            self.assertGreaterEqual(len(models), 2)

    def test_the_client_asks_for_the_api_version_that_takes_system_instructions(self):
        import ai_service
        from config import Settings
        settings = mock.MagicMock(spec=Settings)
        settings.gemini_api_key = "test-key"
        with mock.patch.object(ai_service.genai, "Client") as client_class:
            ai_service.AIService.from_settings(settings)
        self.assertEqual(client_class.call_args.kwargs["http_options"], {"api_version": "v1beta"})

    def test_a_retired_model_moves_on_to_the_next(self):
        retired = _client_error(code=404, status="NOT_FOUND",
                                message="This model models/gemini-x is no longer available. Please update your code to use a newer model.")
        denied = _client_error(code=403, status="PERMISSION_DENIED", message="This model is not available for your project.")
        for error in (retired, denied):
            with self.subTest(code=error.code):
                client = mock.MagicMock()
                client.models.generate_content.side_effect = [error, _fake_response("ok")]
                self.assertEqual(AIService(client=client).generate_reply([{"role": "user", "content": "hi"}]), "ok")

    def test_a_model_that_refuses_the_thinking_setting_is_asked_again_without_it(self):
        client = mock.MagicMock()
        client.models.generate_content.side_effect = [
            _client_error(code=400, status="INVALID_ARGUMENT", message="thinking_budget is not supported by this model."),
            _fake_response("Heat complaint"),
        ]
        title = AIService(client=client).generate_title("no heat")
        self.assertEqual(title, "Heat complaint")
        first, second = client.models.generate_content.call_args_list
        self.assertIn("thinking_config", first.kwargs["config"])
        self.assertNotIn("thinking_config", second.kwargs["config"])
        self.assertEqual(first.kwargs["model"], second.kwargs["model"])
