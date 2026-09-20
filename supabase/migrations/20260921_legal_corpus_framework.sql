-- Builds on 20260920_setup_vector_db.sql rather than replacing it. That
-- migration created `legal_documents`, the HNSW index and the
-- `match_legal_documents` RPC; all three survive here with their names
-- and signatures intact. What this adds is everything needed to cite a
-- retrieved passage honestly, plus the security the first migration left
-- open.
--
-- WHY EACH PIECE EXISTS (see docs/grounded-sources.md and
-- docs/proposal-review.md for the full reasoning):
--
--   * RLS. `legal_documents` shipped without it. In Supabase every table
--     in `public` is exposed through PostgREST, so a table with no RLS is
--     world-writable by anyone holding the anon key -- which is published
--     in the browser. For a corpus whose entire job is to be the thing
--     the assistant trusts, "anyone can INSERT into it" is the worst
--     possible property: it turns the anti-hallucination store into a
--     prompt-injection vector. Read is open (this is public law); writes
--     are closed to everyone but the service role.
--
--   * Provenance. A citation the tenant cannot check is not a citation.
--     `legal_sources` carries the authority, the section number, the
--     official URL and the date the text was fetched, so every quote in a
--     reply can be rendered as a link to the real statute.
--
--   * Full-text search. Tenants type "no heat", the statute is titled
--     "Minimum temperature to be maintained". Vector search alone misses
--     keyword lookups ("what does 27-2029 say?"); FTS alone misses
--     paraphrase. Hybrid beats either, so `fts` is generated here and
--     `search_legal_documents` fuses both rankings.
--
--   * search_path pinning. A SECURITY DEFINER function with a mutable
--     search_path can be hijacked by a caller-controlled schema. Both
--     functions pin it.

-- ---------------------------------------------------------------------
-- 1. Provenance: one row per citable section of law
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS legal_sources (
    id             BIGSERIAL PRIMARY KEY,
    authority      TEXT NOT NULL,
    citation       TEXT NOT NULL,
    title          TEXT NOT NULL,
    jurisdiction   TEXT NOT NULL DEFAULT 'NYC',
    official_url   TEXT NOT NULL,
    effective_date DATE,
    retrieved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash   TEXT NOT NULL,
    UNIQUE (authority, citation)
);

COMMENT ON TABLE legal_sources IS
    'class=PUBLIC. Public law. Deliberately not encrypted: it is not user '
    'content, and encrypting it would destroy both indexes below.';
COMMENT ON COLUMN legal_sources.retrieved_at IS
    'When this text was fetched. NOT when the law last changed -- the '
    'publishers do not reliably expose that, so the UI says "retrieved on", '
    'which is a claim we can actually stand behind.';

-- ---------------------------------------------------------------------
-- 2. Extend legal_documents into citable chunks
-- ---------------------------------------------------------------------

ALTER TABLE legal_documents
    ADD COLUMN IF NOT EXISTS source_id    BIGINT REFERENCES legal_sources(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS heading_path TEXT,
    ADD COLUMN IF NOT EXISTS ordinal      INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- A chunk is a slice of a section, but the SECTION is the citable unit.
-- Long sections have to be split (gemini-embedding-001 caps input at 2048
-- tokens) and a quote from paragraph (c) must still cite the section, not
-- "chunk 3 of the section".
ALTER TABLE legal_documents
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text_content)) STORED;

CREATE INDEX IF NOT EXISTS legal_documents_fts_idx
    ON legal_documents USING gin (fts);
CREATE INDEX IF NOT EXISTS legal_documents_source_id_idx
    ON legal_documents (source_id);

COMMENT ON TABLE legal_documents IS
    'class=PUBLIC. Retrieval chunks. embedding is gemini-embedding-001 at '
    '768 dimensions (the 20260920 migration comment says text-embedding-004; '
    'that model name is superseded, the dimension count is unchanged).';

-- ---------------------------------------------------------------------
-- 3. Row level security
-- ---------------------------------------------------------------------

ALTER TABLE legal_sources   ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS legal_sources_read   ON legal_sources;
DROP POLICY IF EXISTS legal_documents_read ON legal_documents;

-- Public law, readable by anyone including logged-out visitors, because
-- /learn-more is public and citations should resolve without an account.
CREATE POLICY legal_sources_read   ON legal_sources   FOR SELECT USING (true);
CREATE POLICY legal_documents_read ON legal_documents FOR SELECT USING (true);

-- No INSERT/UPDATE/DELETE policy exists for anon or authenticated, so with
-- RLS on, writes are refused for both. Ingestion runs offline under the
-- service role, which bypasses RLS by design.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON legal_sources   FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON legal_documents FROM anon, authenticated;

-- ---------------------------------------------------------------------
-- 4. Retrieval
-- ---------------------------------------------------------------------

-- Kept from 20260920 so anything already written against it still works,
-- with provenance joined on and search_path pinned.
--
-- DROP first, not CREATE OR REPLACE: the 20260920 version returns four
-- columns and this one returns eight, and Postgres refuses to replace a
-- function whose OUT parameters change ("cannot change return type of
-- existing function"). Nothing calls it yet, so dropping is safe; if that
-- ever stops being true, this needs a rename instead.
DROP FUNCTION IF EXISTS match_legal_documents(vector, float, int);
DROP FUNCTION IF EXISTS search_legal_documents(vector, text, int, int);

CREATE FUNCTION match_legal_documents(
    query_embedding vector(768),
    match_threshold float,
    match_count int
)
RETURNS TABLE (
    id           bigint,
    source_name  text,
    text_content text,
    similarity   float,
    citation     text,
    authority    text,
    official_url text,
    retrieved_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    SELECT
        d.id,
        d.source_name,
        d.text_content,
        1 - (d.embedding <=> query_embedding) AS similarity,
        s.citation,
        s.authority,
        s.official_url,
        s.retrieved_at
    FROM legal_documents d
    LEFT JOIN legal_sources s ON s.id = d.source_id
    WHERE d.embedding IS NOT NULL
      AND 1 - (d.embedding <=> query_embedding) > match_threshold
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Hybrid: vector similarity and full-text rank fused by reciprocal rank
-- fusion. RRF is used rather than a weighted score sum because the two
-- scores are not on comparable scales and any hand-tuned weight silently
-- stops being right the moment the corpus grows.
CREATE FUNCTION search_legal_documents(
    query_embedding vector(768),
    query_text      text,
    match_count     int DEFAULT 6,
    rrf_k           int DEFAULT 60
)
RETURNS TABLE (
    id           bigint,
    source_name  text,
    text_content text,
    citation     text,
    authority    text,
    official_url text,
    retrieved_at timestamptz,
    similarity   float,
    fused_score  float
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    WITH vector_hits AS (
        SELECT d.id,
               row_number() OVER (ORDER BY d.embedding <=> query_embedding) AS rank,
               1 - (d.embedding <=> query_embedding) AS similarity
        FROM legal_documents d
        WHERE query_embedding IS NOT NULL AND d.embedding IS NOT NULL
        ORDER BY d.embedding <=> query_embedding
        LIMIT match_count * 4
    ),
    text_hits AS (
        SELECT d.id,
               row_number() OVER (
                   ORDER BY ts_rank_cd(d.fts, websearch_to_tsquery('english', query_text)) DESC
               ) AS rank
        FROM legal_documents d
        WHERE query_text IS NOT NULL
          AND query_text <> ''
          AND d.fts @@ websearch_to_tsquery('english', query_text)
        ORDER BY ts_rank_cd(d.fts, websearch_to_tsquery('english', query_text)) DESC
        LIMIT match_count * 4
    ),
    fused AS (
        SELECT COALESCE(v.id, t.id) AS id,
               COALESCE(1.0 / (rrf_k + v.rank), 0)
             + COALESCE(1.0 / (rrf_k + t.rank), 0) AS fused_score,
               v.similarity
        FROM vector_hits v
        FULL OUTER JOIN text_hits t ON t.id = v.id
    )
    SELECT d.id,
           d.source_name,
           d.text_content,
           s.citation,
           s.authority,
           s.official_url,
           s.retrieved_at,
           f.similarity,
           f.fused_score
    FROM fused f
    JOIN legal_documents d ON d.id = f.id
    LEFT JOIN legal_sources s ON s.id = d.source_id
    ORDER BY f.fused_score DESC
    LIMIT match_count;
$$;

GRANT EXECUTE ON FUNCTION match_legal_documents(vector, float, int)  TO anon, authenticated;
GRANT EXECUTE ON FUNCTION search_legal_documents(vector, text, int, int) TO anon, authenticated;
