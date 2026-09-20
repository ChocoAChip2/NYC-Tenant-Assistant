"""Finds passages of real law to show the assistant before it answers.

Grounding has two halves and this is the first one. This module fetches
candidate passages; citation_guard.py checks that the reply stayed inside
them. Neither half works without the other -- retrieval with no guard is
a model that cites whatever it likes, and a guard with no retrieval has
nothing to check against.

DEGRADES TO NOTHING, ON PURPOSE
Every failure here returns an empty list rather than raising: no corpus
ingested yet, no Gemini key, Supabase unreachable, the RPC missing
because the migration has not been applied. An empty list means the
assistant answers from general knowledge and the reply is labelled
uncited, which is a correct and honest outcome. A tenant asking about
heat should never see an error page because a vector index was not built.

WHY HYBRID AND NOT JUST VECTORS
Tenants type "no heat"; the statute is titled "Minimum temperature to be
maintained". Tenants also type "what does 27-2029 say", which is a
keyword lookup that embeddings are bad at. search_legal_documents() fuses
both rankings in Postgres (see the migration); this module just calls it.

THE RELEVANCE FLOOR IS A FEATURE
If nothing clears MIN_SIMILARITY, retrieval returns nothing and the reply
goes out uncited. Returning the best of a bad set would be worse: the
model would faithfully cite an irrelevant section, and a wrong citation
is more convincing than no citation.
"""

from __future__ import annotations

import logging
import os

from citation_guard import Passage

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

DEFAULT_MATCH_COUNT = 6

# Cosine similarity below this is noise. Tuned conservatively: the cost of
# retrieving nothing is an honest uncited answer; the cost of retrieving
# the wrong section is a confident wrong answer with a link on it.
MIN_SIMILARITY = 0.55


def is_enabled() -> bool:
    """Grounding stays off until someone turns it on.

    The corpus has to be ingested before retrieval means anything, and an
    empty corpus would silently make every reply uncited while looking
    like the feature works.
    """
    return os.environ.get("LEGAL_CORPUS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class RetrievalService:
    def __init__(self, supabase_client=None, gemini_client=None):
        self._supabase = supabase_client
        self._gemini = gemini_client

    def embed_query(self, text: str) -> list[float] | None:
        if not self._gemini or not text.strip():
            return None
        try:
            response = self._gemini.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config={"output_dimensionality": EMBEDDING_DIMENSIONS},
            )
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                return None
            values = getattr(embeddings[0], "values", None)
            return list(values) if values else None
        except Exception:
            # An embedding call is one more thing competing for the same
            # Gemini quota that already causes 429 fallbacks in
            # ai_service. Losing it costs a citation, not the reply.
            logger.exception("Could not embed the query; answering without retrieval.")
            return None

    def search(self, query: str, match_count: int = DEFAULT_MATCH_COUNT) -> list[Passage]:
        if not is_enabled() or not self._supabase or not query.strip():
            return []

        embedding = self.embed_query(query)

        try:
            response = self._supabase.rpc(
                "search_legal_documents",
                {
                    "query_embedding": embedding,
                    "query_text": query,
                    "match_count": match_count,
                },
            ).execute()
            rows = response.data or []
        except Exception:
            logger.exception("Legal corpus search failed; answering without retrieval.")
            return []

        return self._to_passages(rows)

    @staticmethod
    def _to_passages(rows: list[dict]) -> list[Passage]:
        passages: list[Passage] = []
        for row in rows:
            similarity = row.get("similarity")
            # A row that arrived only through full-text search has no
            # similarity; keyword agreement is evidence in its own right,
            # so it is kept. A vector hit below the floor is dropped.
            if similarity is not None and similarity < MIN_SIMILARITY:
                continue
            text = (row.get("text_content") or "").strip()
            if not text:
                continue
            passages.append(
                Passage(
                    marker=f"S{len(passages) + 1}",
                    text=text,
                    citation=row.get("citation"),
                    authority=row.get("authority"),
                    official_url=row.get("official_url"),
                )
            )
        return passages


def format_for_prompt(passages: list[Passage]) -> str:
    """Render passages as the closed set the assistant may cite.

    The instruction is repeated here, next to the passages, rather than
    living only in the system prompt. Long conversations push a system
    prompt far from the text it governs, and a rule the model has to
    remember from 4,000 tokens ago is a rule it will drop.
    """
    if not passages:
        return ""

    blocks = []
    for passage in passages:
        heading = " ".join(part for part in (passage.authority, passage.citation) if part)
        blocks.append(f"[{passage.marker}] {heading}\n{passage.text}")

    return (
        "SOURCES. These are the only sources you have. There are no others.\n\n"
        + "\n\n".join(blocks)
        + "\n\nRules for using them:\n"
        "- Cite with the markers above, like [S1]. Cite nothing else.\n"
        "- A sentence may carry a citation only if it contains a phrase "
        "copied word for word from that source, in quotation marks.\n"
        "- Every number, temperature, deadline and dollar amount you state "
        "with a citation must appear inside that quoted phrase. If the "
        "source does not give a number, do not give one.\n"
        "- Do not name any statute, section, rule or local law that does not "
        "appear in the sources above.\n"
        "- If these sources do not answer the question, say so plainly and "
        "answer from general knowledge WITHOUT citing anything.\n"
    )
