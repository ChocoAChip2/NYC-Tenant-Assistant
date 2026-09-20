# Grounding replies in real law

**Status:** built, and off by default. `LEGAL_CORPUS_ENABLED` turns it on.
Nothing changes in production until it is set, because an empty corpus
would make every reply silently uncited while looking like the feature
works.

This supersedes `docs/grounded-sources.md` (the proposal) where the two
disagree; `docs/proposal-review.md` explains why.

## The two halves

Retrieval finds passages of real law. The guard checks the reply stayed
inside them. **Neither works without the other** — retrieval with no guard
is a model that cites whatever it likes, and a guard with no retrieval has
nothing to check against.

| File | Job |
| --- | --- |
| `supabase/migrations/20260920_setup_vector_db.sql` | pgvector, `legal_documents`, HNSW index, `match_legal_documents` (from ChocoAChip2's branch) |
| `supabase/migrations/20260921_legal_corpus_framework.sql` | Provenance, RLS, full-text column, hybrid `search_legal_documents` |
| `supabase/migrations/20260922_harden_search_paths.sql` | Moves pgvector out of `public`, pins every function's `search_path` |
| `retrieval_service.py` | Embed the question, call the hybrid search, apply the relevance floor |
| `citation_guard.py` | Check markers, statutes, quotes and numbers |
| `tools/ingest_corpus.py` | Offline loader (needs the service-role key) |

## Why the guard checks four things

The obvious design checks that every `[Sn]` marker points at a retrieved
passage. That is not enough, and the gap is where the harm lives:

> The assistant retrieves § 27-2029 correctly and replies *"Under
> § 27-2029 your landlord must keep it at 70°F overnight."*

Marker valid. Section real. Section retrieved. And the answer is wrong —
the overnight minimum is 62°F. A tenant acts on a fabricated number that
now carries a citation and a link, which makes it **more** convincing than
an uncited guess.

So:

1. **Markers** — every `[Sn]` was actually shown.
2. **Statutes** — every statute-shaped string appears in a retrieved
   passage, or in the tenant's own message (repeating back what someone
   asked about is not a hallucination).
3. **Quotes** — a sentence may carry a citation only if it contains a span
   copied verbatim from the cited passage.
4. **Numbers** — every number-with-a-unit in a cited sentence appears in
   that passage. **This is the one that catches 70°F.**

Checks 3 and 4 are why the guard is worth having. Deleting them leaves a
validator that proves only that a citation exists.

### What it still does not catch

Retrieval quality. If the wrong section comes back, a reply can quote it
perfectly and still mislead. No validator fixes that — only better
retrieval does. Saying otherwise would repeat the mistake this design was
written to correct.

## Report mode first

`LEGAL_GUARD_MODE` defaults to `report`: every violation is logged, nothing
is changed. A guard that rejects good replies is worse than no guard,
because the assistant quietly says less than it knows and the failure looks
like a dull model rather than a bug.

Run in `report` until the logs show the real false-rejection rate, then set
`enforce`. In `enforce`, a failing reply keeps its prose and loses its
citation markers — the answer may still be useful, the authority it claimed
is what it has not earned.

## Retrieval is hybrid, and degrades to nothing

Tenants type "no heat"; the statute is titled "Minimum temperature to be
maintained". They also type "what does 27-2029 say", which is a keyword
lookup embeddings are bad at. `search_legal_documents` fuses vector and
full-text rankings with reciprocal rank fusion — RRF rather than a weighted
sum, because the two scores are not on comparable scales and a hand-tuned
weight stops being right the moment the corpus grows.

Every failure path returns an empty list: no corpus, no Gemini key, the
migration not applied, a 429 on the embedding call. An empty list means an
honest uncited answer. **A tenant asking about heat should never see an
error because a vector index was not built.**

The relevance floor (`MIN_SIMILARITY = 0.55`) is a feature. Returning the
best of a bad set would have the model faithfully cite an irrelevant
section, and a wrong citation is more convincing than no citation.

## Which law, and not the penal code

Tenant questions are answered by the **NYC Housing Maintenance Code**
(Admin Code Title 27, Ch. 2), **RPL § 235-b** (warranty of habitability),
**RPAPL Article 7** (eviction procedure) and **Admin Code Title 26, Ch. 5**
(illegal lockouts). Criminal law touches tenancy at essentially one point,
and that hook is in the Admin Code. Ingesting the penal code would cost
real effort and answer almost nothing.

## Security of the corpus

`legal_documents` shipped without RLS. In Supabase every `public` table is
exposed through PostgREST, so a table with no RLS is writable by anyone
holding the anon key — which is published in the browser. For a store whose
entire job is to be *the thing the assistant trusts*, "anyone can INSERT"
turns the anti-hallucination corpus into a prompt-injection vector.

Now: read open (it is public law, and `/learn-more` is public), writes
closed to `anon` and `authenticated` both by RLS and by an explicit
`REVOKE`. Ingestion runs offline under the service role, which the web app
deliberately does not hold.

Verified against the live database: RLS on, one read policy each,
`anon_can_insert = false`, and Supabase's security linter clean of every
finding this work introduced.

## Running the ingest

```bash
export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... GEMINI_API_KEY=...
python -m tools.ingest_corpus --file corpus/hmc-title27-ch2.json --dry-run
python -m tools.ingest_corpus --file corpus/hmc-title27-ch2.json
```

Input is a JSON array of sections; `--dry-run` validates and chunks without
writing. Every section needs an `official_url` over https, because **a
citation a tenant cannot open is an assertion, not a source** — and the
validator rejects the file if one is missing.

Scraping is deliberately not in that script. Publishers change their markup,
and a fetch loop that breaks quietly would poison the corpus with the one
kind of error this whole subsystem exists to prevent.
