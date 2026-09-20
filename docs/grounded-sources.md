# Grounding replies in real law (proposal)

**Status:** proposal. Nothing here is built yet. It exists so the decisions
get made before the code, because the shape of the retrieval layer is hard
to change once conversations depend on it.

The goal, in the words it was asked in: give information *without
confidence in uncertainty*. Cite only sources that exist, that are
relevant, and that a tenant can open and check for themselves.

## The short answer

Yes. Supabase can hold the corpus and the app can retrieve from it before
every reply. Postgres 17 on this project already offers the `vector`
extension (pgvector 0.8.0) and `pg_trgm`; neither is installed yet, and
installing `vector` is a one-line migration.

But the retrieval layer is the easy half. **A prompt asking the model not
to invent citations does not stop it inventing citations.** What stops it
is that the app checks, in code, that every citation in the reply points
at a chunk that was actually retrieved, and drops the reply if it does
not. That validator is the feature. Everything else is plumbing for it.

## First, a correction worth making early

The penal code is the wrong corpus for almost every question this site
will get. Tenant law in New York City lives in:

- **NYC Housing Maintenance Code** — Admin Code Title 27, Ch. 2. Heat,
  hot water, repairs, HPD violations. (Minimum indoor temperature is
  [§ 27-2029](https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-47505).)
- **Real Property Law** — especially § 235-b, the warranty of
  habitability, and the HSTPA 2019 amendments on deposits and fees.
- **Real Property Actions and Proceedings Law (RPAPL)** — Article 7,
  the actual eviction procedure and its deadlines.
- **NYC Admin Code Title 26, Ch. 5** — unlawful eviction (§ 26-521),
  with the penalties in § 26-523.
- **Rent Stabilization Code** (9 NYCRR 2520–2530) for regulated units.
- **Multiple Dwelling Law** where it applies.

Criminal law touches this at exactly one useful point — illegal lockouts
are a crime, not just a civil wrong — and that hook lives in the Admin
Code section above, not in a general penal-code dump. Ingesting the penal
code would cost effort and return almost nothing; ingesting the Housing
Maintenance Code would answer a large share of real questions on day one.

**Recommended first corpus:** Housing Maintenance Code (Title 27 Ch. 2)
plus RPL § 220–238-a. That is small, high-value, and enough to prove the
whole pipeline.

## Where the text comes from

- **NY State consolidated laws** (RPL, RPAPL, MDL): the NY Senate's
  [Open Legislation API](https://legislation.nysenate.gov/static/docs/html/laws.html)
  serves them structured, section by section, with a free API key. This
  is the good path — it gives clean section boundaries rather than
  scraped HTML.
- **NYC Administrative Code**: published by American Legal Publishing;
  HPD also distributes the Housing Maintenance Code as a PDF. Scraping
  is acceptable here provided the ingest records exactly what it fetched
  and when.

Both are public-domain government edicts. Store, alongside the text,
enough to prove what was served: source URL, retrieval timestamp,
effective date, and a SHA-256 of the raw section.

## Schema sketch

```sql
create extension if not exists vector;

create table legal_sources (
  id            uuid primary key default gen_random_uuid(),
  authority     text not null,      -- 'NYC Administrative Code'
  citation      text not null,      -- '27-2029'
  title         text not null,      -- 'Minimum temperature to be maintained'
  jurisdiction  text not null,      -- 'NYC' | 'NY'
  official_url  text not null,
  effective_date date,
  retrieved_at  timestamptz not null default now(),
  content_hash  text not null,
  unique (authority, citation)
);

create table legal_chunks (
  id          uuid primary key default gen_random_uuid(),
  source_id   uuid not null references legal_sources(id) on delete cascade,
  heading_path text not null,       -- 'Title 27 > Ch 2 > Art 8 > 27-2029'
  ordinal     int  not null,
  content     text not null,
  embedding   vector(768),
  fts         tsvector generated always as (to_tsvector('english', content)) stored
);

create index on legal_chunks using hnsw (embedding vector_cosine_ops);
create index on legal_chunks using gin (fts);
```

Two things to notice:

- **This table is not encrypted.** It is public law. Encrypting it would
  destroy both indexes and protect nothing. The instinct to encrypt
  everything after the last round of work is the wrong instinct here.
- **Chunk by section, not by token count.** The section is the citable
  unit. A chunk that straddles § 27-2029 and § 27-2030 cannot be cited
  honestly. Prepend the heading path to each chunk's embedded text so a
  short subsection still carries its context.

RLS: `select` for `authenticated` (or even `anon`, since it is public),
no insert/update/delete from the app at all — the corpus is written only
by the ingest script running with elevated credentials, offline.

## Retrieval

Hybrid, because pure vector search is bad at exactly the queries tenants
type. "What's § 27-2029?" and "HPD violation class C" are keyword
lookups; "my landlord won't fix the mold" is semantic.

1. Vector search over `embedding` (Gemini `text-embedding-004`, 768 dims
   — same API key the app already holds).
2. Full-text search via `websearch_to_tsquery` over `fts`.
3. Fuse with reciprocal rank fusion, take the top 5–8 chunks.
4. **Apply a relevance floor.** If nothing clears it, retrieval returned
   nothing, and that is a legitimate and important outcome.

Run it as a `SECURITY DEFINER` SQL function so the app makes one round
trip and the ranking logic is versioned in a migration.

## The citation contract — the part that actually prevents hallucination

**Prompt side.** Retrieved chunks are injected with opaque markers:

```
[S1] NYC Administrative Code § 27-2029 — Minimum temperature to be maintained
<verbatim chunk text>

[S2] ...
```

The directive is closed-book and absolute:

- You may cite only `[S1]`…`[Sn]`. There are no other sources.
- Never name a statute, section number, dollar figure or deadline that
  does not appear in the text above.
- Any direct quote must be copied verbatim from one of those chunks.
- If the sources above do not answer the question, say so and answer from
  general knowledge **without citing anything**.

**Code side — the enforcement.** After the model replies, before the user
sees a single character:

1. Extract every `[Sn]` marker. Any marker outside the retrieved set →
   the reply is rejected.
2. Extract every quoted span attributed to a source. If it is not a
   substring of that chunk (after whitespace normalisation) → rejected.
3. Scan for statute-shaped strings (`§ 27-2029`, `RPL 235-b`, `9 NYCRR
   2523.5`) that appear in the reply but in none of the retrieved chunks
   → rejected.
4. On rejection: retry once with the failure named in the prompt, then
   fall back to an uncited answer. Log every rejection — the rate is a
   real quality metric and belongs on a dashboard eventually.

Rule 3 is the one that does the heavy lifting, and it works whether or
not the model cooperates with the marker format.

**Render side.** A validated `[S1]` becomes a link to `official_url` with
the citation and the "as of" date. The user can click through to the
statute. That is what makes it *verifiable* rather than merely *asserted*.

## Uncertainty, honestly

Do not ask the model for a confidence score; they are not calibrated and
a number invites false precision. Bind confidence to something real:
whether the answer survived validation with a citation attached.

- **Cited** — "Based on NYC Admin Code § 27-2029 (as of 2026-01-01)",
  with the link.
- **Uncited** — "General information. I could not find this in the laws I
  have on file, so please confirm it before relying on it."

Two states, both truthful, and the difference is mechanical rather than
vibes. This is also where the existing escalation triggers in
`branding.ESCALATION_TRIGGERS` should hook in: an uncited answer about a
court date deserves a louder pointer to a real attorney than a cited
answer about heat does.

Lived experience from tenants stays welcome in the chat — nothing here
restricts what a user can say or what the assistant can discuss. The
constraint is only on what gets presented as *law*.

## Freshness

Law changes; a stale citation is worse than none, because it looks
checkable. Store `effective_date`, show it in every citation, and re-run
the ingest on a schedule that compares `content_hash`. A changed hash is
a signal to re-embed that section and, eventually, to flag replies that
cited the old text.

## Build order

1. Migration: `create extension vector`, the two tables, RLS, the search
   function.
2. `tools/ingest_corpus.py` — fetch, split by section, embed, upsert.
   Run offline, not from the web app.
3. `retrieval_service.py` — hybrid query, relevance floor, returns chunks
   with their source rows.
4. `citation_guard.py` — the validator, unit-tested against deliberately
   hallucinated replies. **Write this before wiring retrieval into the
   chat**, so the guarantee exists before anything depends on it.
5. Prompt changes in `ai_service.py`, behind a flag.
6. UI: citation chips under cited replies, the uncited-answer label.

Steps 1–4 are independently testable and ship without touching the chat
path at all.
