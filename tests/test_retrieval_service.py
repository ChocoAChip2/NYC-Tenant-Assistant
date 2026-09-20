"""Tests for retrieval and the ingest tool.

No Supabase and no Gemini: the Supabase client and the Gemini client are
both faked, because retrieval has to be testable without either, and
because the behaviour most worth locking in is what happens when they are
BROKEN -- retrieval must degrade to "no sources" rather than to an error
page. A tenant asking about heat should never see a stack trace because a
vector index was not built.
"""

import os
import unittest
from unittest import mock

import retrieval_service
from citation_guard import Passage
from tools import ingest_corpus


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, rows, explode=False):
        self._rows = rows
        self._explode = explode
        self.calls = []

    def __call__(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        if self._explode:
            raise RuntimeError("relation \"legal_documents\" does not exist")
        return FakeResponse(self._rows)


class FakeSupabase:
    def __init__(self, rows=None, explode=False):
        self.rpc = FakeRpc(rows or [], explode=explode)


ROW = {
    "text_content": "an indoor temperature of at least 62 degrees Fahrenheit",
    "citation": "27-2029",
    "authority": "NYC Administrative Code",
    "official_url": "https://example.gov/27-2029",
    "similarity": 0.81,
}


def _enabled():
    return mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"})


class EnablementTests(unittest.TestCase):
    def test_grounding_is_off_unless_explicitly_enabled(self):
        """An empty corpus would make every reply silently uncited while
        looking like the feature works, so it stays off until someone has
        actually ingested law and turned it on."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(retrieval_service.is_enabled())

    def test_search_returns_nothing_while_disabled(self):
        service = retrieval_service.RetrievalService(FakeSupabase([ROW]))

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(service.search("no heat"), [])


class DegradationTests(unittest.TestCase):
    def test_a_missing_table_or_rpc_yields_no_sources_not_an_exception(self):
        service = retrieval_service.RetrievalService(FakeSupabase(explode=True))

        with _enabled():
            self.assertEqual(service.search("no heat"), [])

    def test_a_broken_embedding_call_still_allows_keyword_retrieval(self):
        """Losing the embedding costs a better ranking, not the reply --
        which matters because that call competes for the same Gemini quota
        that already causes fallbacks in ai_service."""
        gemini = mock.Mock()
        gemini.models.embed_content.side_effect = RuntimeError("429")
        supabase = FakeSupabase([ROW])
        service = retrieval_service.RetrievalService(supabase, gemini)

        with _enabled():
            passages = service.search("no heat")

        self.assertEqual(len(passages), 1)
        self.assertIsNone(supabase.rpc.calls[0][1]["query_embedding"])

    def test_no_supabase_client_yields_no_sources(self):
        service = retrieval_service.RetrievalService(None)

        with _enabled():
            self.assertEqual(service.search("no heat"), [])


class RelevanceFloorTests(unittest.TestCase):
    def test_a_weak_vector_match_is_dropped(self):
        """Returning the best of a bad set is worse than returning
        nothing: the model would faithfully cite an irrelevant section,
        and a wrong citation is more convincing than no citation."""
        weak = dict(ROW, similarity=0.2)
        service = retrieval_service.RetrievalService(FakeSupabase([weak]))

        with _enabled():
            self.assertEqual(service.search("no heat"), [])

    def test_a_keyword_only_hit_with_no_similarity_is_kept(self):
        keyword_only = dict(ROW, similarity=None)
        service = retrieval_service.RetrievalService(FakeSupabase([keyword_only]))

        with _enabled():
            self.assertEqual(len(service.search("27-2029")), 1)

    def test_markers_are_numbered_from_one_in_result_order(self):
        rows = [dict(ROW), dict(ROW, citation="27-2030"), dict(ROW, citation="27-2031")]
        service = retrieval_service.RetrievalService(FakeSupabase(rows))

        with _enabled():
            passages = service.search("heat")

        self.assertEqual([p.marker for p in passages], ["S1", "S2", "S3"])

    def test_markers_stay_contiguous_when_a_row_is_filtered_out(self):
        """A gap in the markers would tell the model that [S2] exists when
        it was never shown, which is an invitation to cite it."""
        rows = [dict(ROW), dict(ROW, similarity=0.1), dict(ROW, citation="27-2031")]
        service = retrieval_service.RetrievalService(FakeSupabase(rows))

        with _enabled():
            passages = service.search("heat")

        self.assertEqual([p.marker for p in passages], ["S1", "S2"])


class PromptFormattingTests(unittest.TestCase):
    def test_no_passages_means_no_prompt_block_at_all(self):
        self.assertEqual(retrieval_service.format_for_prompt([]), "")

    def test_the_block_states_the_closed_set_and_the_quote_rule(self):
        block = retrieval_service.format_for_prompt(
            [Passage(marker="S1", text="Heat text.", citation="27-2029", authority="NYC Admin Code")]
        )

        self.assertIn("[S1] NYC Admin Code 27-2029", block)
        self.assertIn("There are no others", block)
        self.assertIn("copied word for word", block)
        self.assertIn("WITHOUT citing anything", block)

    def test_the_rules_travel_with_the_passages_not_only_in_the_system_prompt(self):
        """A rule the model has to remember from 4,000 tokens ago is a rule
        it will drop, so the instruction sits next to the text it governs."""
        block = retrieval_service.format_for_prompt([Passage(marker="S1", text="x" * 50)])

        self.assertIn("Rules for using them:", block)


class ChunkingTests(unittest.TestCase):
    def test_a_short_section_stays_one_chunk(self):
        self.assertEqual(ingest_corpus.chunk_section("Short section text."), ["Short section text."])

    def test_a_long_section_is_split_under_the_embedding_input_cap(self):
        text = ("This is a sentence about heat and hot water in dwellings. " * 200).strip()

        chunks = ingest_corpus.chunk_section(text)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), ingest_corpus.MAX_CHARS_PER_CHUNK)

    def test_splitting_loses_no_content(self):
        text = ". ".join(f"Sentence number {n} about repairs" for n in range(400)) + "."

        joined = " ".join(ingest_corpus.chunk_section(text))

        self.assertIn("Sentence number 0 about repairs", joined)
        self.assertIn("Sentence number 399 about repairs", joined)


class IngestValidationTests(unittest.TestCase):
    GOOD = {
        "authority": "NYC Administrative Code",
        "citation": "27-2029",
        "title": "Minimum temperature to be maintained",
        "official_url": "https://codelibrary.amlegal.com/example",
        "text": "Between the hours of 10:00 PM and 6:00 AM...",
    }

    def test_a_complete_section_validates(self):
        self.assertEqual(ingest_corpus.validate([self.GOOD]), [])

    def test_a_section_without_an_official_url_is_rejected(self):
        """Provenance is not optional: a citation the tenant cannot open is
        an assertion, not a source."""
        problems = ingest_corpus.validate([dict(self.GOOD, official_url="")])

        self.assertTrue(any("official_url" in problem for problem in problems))

    def test_a_non_https_source_url_is_rejected(self):
        problems = ingest_corpus.validate([dict(self.GOOD, official_url="http://example.gov/x")])

        self.assertTrue(any("https" in problem for problem in problems))

    def test_a_duplicate_citation_is_rejected(self):
        """Two rows for one section means two passages that can disagree,
        both citable."""
        problems = ingest_corpus.validate([self.GOOD, dict(self.GOOD, text="different text")])

        self.assertTrue(any("duplicate" in problem for problem in problems))

    def test_dry_run_writes_nothing_and_still_reports_chunk_counts(self):
        stats = ingest_corpus.ingest([self.GOOD], supabase=None, gemini=None, dry_run=True)

        self.assertEqual(stats["sections"], 1)
        self.assertEqual(stats["chunks"], 1)


if __name__ == "__main__":
    unittest.main()


class ChatRouteGroundingTests(unittest.TestCase):
    """The wiring in routes._ground_reply.

    The property that matters most is the first one: with the feature off,
    the chat path must behave exactly as it did before this branch. Every
    other test here is about what happens once it is on.
    """

    def _app(self, passages=None, reply="Plain answer."):
        import flask

        from routes import main_bp
        from tests.app_test_support import configure_test_app

        templates = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
        )

        class FakeSupabaseService:
            def build_user_scoped_client(self, token):
                return mock.Mock()

            def ensure_conversation_for_user(self, *a, **k):
                return None

            def insert_message(self, *a, **k):
                return None

            def fetch_messages_for_conversation(self, *a, **k):
                return [{"role": "user", "content": "my landlord will not fix the heat"}]

        class FakeAI:
            client = object()

            def is_ready(self):
                return True

            def generate_reply(self, messages):
                self.last_messages = messages
                return reply

        app = flask.Flask(__name__, template_folder=templates)
        app.secret_key = "t"
        app.config["SUPABASE_SERVICE"] = FakeSupabaseService()
        ai = FakeAI()
        app.config["AI_SERVICE"] = ai
        app.register_blueprint(main_bp)
        configure_test_app(app)

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = "u1"
            sess["user_email"] = "t@example.com"
            sess["access_token"] = "tok"
        return app, client, ai

    def _post(self, client):
        return client.post(
            "/chat/message",
            json={"conversation_id": "c1", "content": "my landlord will not fix the heat"},
        )

    def test_with_grounding_off_the_reply_path_is_unchanged(self):
        app, client, ai = self._app()

        with mock.patch.dict(os.environ, {}, clear=True):
            response = self._post(client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Plain answer.")
        self.assertEqual(response.get_json()["sources"], [])
        # No SOURCES block was prepended to the history.
        self.assertNotIn("SOURCES", ai.last_messages[0]["content"])

    def test_with_grounding_on_but_no_hits_the_reply_is_still_uncited(self):
        app, client, ai = self._app()

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[]
        ):
            response = self._post(client)

        self.assertEqual(response.get_json()["sources"], [])
        self.assertNotIn("SOURCES", ai.last_messages[0]["content"])

    def test_passages_are_prepended_and_chips_come_back(self):
        passage = Passage(
            marker="S1",
            text="an indoor temperature of at least 62 degrees Fahrenheit shall be maintained",
            citation="27-2029",
            authority="NYC Administrative Code",
            official_url="https://example.gov/27-2029",
        )
        app, client, ai = self._app(
            reply='The rule is "an indoor temperature of at least 62 degrees Fahrenheit shall be maintained" [S1].'
        )

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[passage]
        ):
            payload = self._post(client).get_json()

        self.assertIn("SOURCES", ai.last_messages[0]["content"])
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(payload["sources"][0]["label"], "NYC Administrative Code 27-2029")

    def test_report_mode_lets_a_failing_reply_through_unchanged(self):
        """The default. A guard that blocks before anyone has measured its
        false-rejection rate makes the assistant quietly say less than it
        knows, and the failure looks like a dull model rather than a bug."""
        passage = Passage(marker="S1", text="at least 62 degrees Fahrenheit", citation="27-2029")
        app, client, ai = self._app(reply="Under [S1] it must be 70 degrees overnight.")

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[passage]
        ):
            payload = self._post(client).get_json()

        self.assertIn("[S1]", payload["reply"])

    def test_enforce_mode_strips_the_citation_from_a_failing_reply(self):
        passage = Passage(marker="S1", text="at least 62 degrees Fahrenheit", citation="27-2029")
        app, client, ai = self._app(reply="Under [S1] it must be 70 degrees overnight.")

        with mock.patch.dict(
            os.environ, {"LEGAL_CORPUS_ENABLED": "1", "LEGAL_GUARD_MODE": "enforce"}
        ), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[passage]
        ):
            payload = self._post(client).get_json()

        self.assertNotIn("[S1]", payload["reply"])
        self.assertEqual(payload["sources"], [])


class GroundingFailureModeTests(ChatRouteGroundingTests):
    """Every optional part of grounding must fail into an ordinary reply.

    This subsystem is a bonus. A tenant asking about heat must never get an
    error page because a vector index was not built, a row came back in an
    odd shape, or the guard itself has a bug.
    """

    PASSAGE = Passage(marker="S1", text="at least 62 degrees Fahrenheit", citation="27-2029")

    def test_retrieval_blowing_up_still_produces_a_reply(self):
        app, client, ai = self._app(reply="Plain answer.")

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", side_effect=RuntimeError("boom")
        ):
            payload = self._post(client).get_json()

        self.assertEqual(payload["reply"], "Plain answer.")
        self.assertEqual(payload["sources"], [])

    def test_prompt_formatting_blowing_up_still_produces_a_reply(self):
        app, client, ai = self._app(reply="Plain answer.")

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[self.PASSAGE]
        ), mock.patch.object(
            retrieval_service, "format_for_prompt", side_effect=RuntimeError("boom")
        ):
            payload = self._post(client).get_json()

        self.assertEqual(payload["reply"], "Plain answer.")

    def test_a_guard_crash_strips_citations_rather_than_trusting_them(self):
        """A crash is not a violation -- it means nothing was verified, so
        nothing has earned a citation. That holds in report mode too."""
        import citation_guard

        app, client, ai = self._app(reply="Under [S1] it is 62 degrees Fahrenheit.")

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[self.PASSAGE]
        ), mock.patch.object(citation_guard, "check", side_effect=RuntimeError("boom")):
            payload = self._post(client).get_json()

        self.assertNotIn("[S1]", payload["reply"])
        self.assertEqual(payload["sources"], [])

    def test_a_chip_rendering_crash_still_sends_the_reply(self):
        import citation_guard

        app, client, ai = self._app(reply='It is "at least 62 degrees Fahrenheit" [S1].')

        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}), mock.patch.object(
            retrieval_service.RetrievalService, "search", return_value=[self.PASSAGE]
        ), mock.patch.object(citation_guard, "render_sources", side_effect=RuntimeError("boom")):
            payload = self._post(client).get_json()

        self.assertIn("[S1]", payload["reply"])
        self.assertEqual(payload["sources"], [])

    def test_a_model_error_is_still_reported_as_a_model_error(self):
        """The model call is deliberately NOT swallowed: chat_message maps
        these to real status codes, and retrying would just spend a second
        API call to fail again."""
        app, client, ai = self._app()
        ai.generate_reply = mock.Mock(side_effect=RuntimeError("Gemini is not configured yet."))

        with mock.patch.dict(os.environ, {}, clear=True):
            response = self._post(client)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(ai.generate_reply.call_count, 1)


class MalformedCorpusRowTests(unittest.TestCase):
    """Rows come from Postgres through PostgREST, and the shapes below have
    all been seen or are one client-version change away."""

    def _search(self, rows):
        service = retrieval_service.RetrievalService(FakeSupabase(rows))
        with mock.patch.dict(os.environ, {"LEGAL_CORPUS_ENABLED": "1"}):
            return service.search("q")

    def test_similarity_arriving_as_a_string_does_not_raise(self):
        """A numeric column can serialise as a string. Comparing that to a
        float raises, which would turn a slightly odd corpus into a 500."""
        self.assertEqual(len(self._search([dict(ROW, similarity="0.81")])), 1)

    def test_an_uncoercible_similarity_is_treated_as_no_score(self):
        self.assertEqual(len(self._search([dict(ROW, similarity="not a number")])), 1)

    def test_a_row_that_is_not_an_object_is_skipped(self):
        self.assertEqual(self._search(["surprise"]), [])

    def test_rows_missing_text_are_skipped(self):
        self.assertEqual(self._search([{"similarity": 0.9}, dict(ROW, text_content=None)]), [])

    def test_unknown_extra_columns_are_ignored(self):
        self.assertEqual(len(self._search([dict(ROW, some_new_column=object())])), 1)
