-- The legal library: official, verbatim NYC law, kept current by a
-- quarterly refresh (tools/corpus/refresh.py, run from GitHub Actions).
--
-- NOT YET APPLIED. Written in a container that cannot reach Supabase.
-- Before `apply_migration`, run this whole file inside BEGIN ... ROLLBACK
-- against the live project together with the checks in
-- supabase/checks/20260929_legal_library_checks.sql, then check
-- get_advisors after.
--
-- WHAT CHANGES AND WHY (docs/HANDOFF.md, decisions 3 and 4):
--
--   * legal_sources gets one row per section with the full text, history
--     and editor's notes, keyed by `section_key` (`<source_key>:<citation>`).
--     The old UNIQUE(authority, citation) goes: NYC Admin Code Title 26
--     has two § 26-1301s, so a citation alone is not an identity.
--
--   * Writes happen ONLY through SECURITY DEFINER functions that check an
--     ingest token, never with the service-role key. The app holds no
--     service-role key by design, and the refresh job should not either:
--     a leaked ingest token can touch the library and nothing else. Only
--     the token's sha256 is stored, in a table no role can read.
--
--   * Sections are never deleted. One that disappears from the source is
--     marked `missing_from_source`, because old citations still point at
--     it. A broken parse must not empty the library, so a source is
--     refused if its parse covers fewer than 80% of the sections that were
--     active BEFORE the run: once up front (corpus_preflight_source, before
--     any upsert touches the library) and again at finalize, where rows
--     this run added or restored are left out of the count so they cannot
--     pad it. (Fixed 2026-09-29 before first apply: finalize used to count
--     after the upserts, so 5 real sections + 20 junk ones passed as 25/30.)
--
--   * The content hash is recomputed here, not trusted from the client,
--     and a mismatch rejects the batch. That catches text mangled in
--     transit and any drift between this normalization and model.py's.
--
--   * Search only returns sections that are still active.
--
-- The tables are empty today (the corpus has never been loaded), which is
-- what makes dropping the old constraint and adding NOT NULL columns
-- safe. The guard below turns that assumption into a check.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.legal_sources) OR EXISTS (SELECT 1 FROM public.legal_documents) THEN
        RAISE EXCEPTION 'legal_sources/legal_documents are not empty; this migration assumes they are';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 1. legal_sources: one row per section
-- ---------------------------------------------------------------------

-- Drop UNIQUE(authority, citation) by what it is, not by a guessed name.
DO $$
DECLARE
    con text;
BEGIN
    FOR con IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'public.legal_sources'::regclass
          AND c.contype = 'u'
          AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
               FROM unnest(c.conkey) AS k(attnum)
               JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
              = ARRAY['authority', 'citation']
    LOOP
        EXECUTE format('ALTER TABLE public.legal_sources DROP CONSTRAINT %I', con);
    END LOOP;
END $$;

ALTER TABLE public.legal_sources
    ADD COLUMN section_key     text NOT NULL,
    ADD COLUMN source_key      text NOT NULL,
    ADD COLUMN heading_path    text NOT NULL DEFAULT '',
    ADD COLUMN full_text       text NOT NULL DEFAULT '',
    ADD COLUMN history         jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN notes           jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN repealed        boolean NOT NULL DEFAULT false,
    ADD COLUMN last_amended    date,
    ADD COLUMN last_checked_at timestamptz,
    ADD COLUMN status          text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'missing_from_source')),
    ADD CONSTRAINT legal_sources_section_key_key UNIQUE (section_key),
    ADD CONSTRAINT legal_sources_section_key_shape
        CHECK (section_key = source_key || ':' || citation);

CREATE INDEX legal_sources_source_key_idx ON public.legal_sources (source_key, status);
CREATE INDEX legal_sources_citation_idx   ON public.legal_sources (citation);

COMMENT ON COLUMN public.legal_sources.section_key IS
    '<source_key>:<citation>. Citations are not unique across a code '
    '(Admin Code Title 26 has two § 26-1301s).';
COMMENT ON COLUMN public.legal_sources.content_hash IS
    'sha256 of the law text only (history and editor''s notes excluded), '
    'ASCII whitespace collapsed. Recomputed server-side on every write.';
COMMENT ON COLUMN public.legal_sources.last_amended IS
    'Latest effective date named in the publisher''s history line. NULL '
    'when the history names none; never guessed.';
COMMENT ON COLUMN public.legal_sources.last_checked_at IS
    'When a refresh last confirmed this text against the official source. '
    'This is the "verified current as of" date shown to tenants.';

-- ---------------------------------------------------------------------
-- 2. Change log (public) and refresh runs (private)
-- ---------------------------------------------------------------------

CREATE TABLE public.legal_refresh_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    trigger     text NOT NULL DEFAULT 'manual',
    summary     jsonb
);

CREATE TABLE public.legal_source_changes (
    id          bigserial PRIMARY KEY,
    section_key text NOT NULL,
    change_type text NOT NULL
        CHECK (change_type IN ('added', 'amended', 'missing_from_source', 'restored')),
    old_hash    text,
    new_hash    text,
    detected_at timestamptz NOT NULL DEFAULT now(),
    run_id      uuid NOT NULL REFERENCES public.legal_refresh_runs(id)
);

CREATE INDEX legal_source_changes_section_idx ON public.legal_source_changes (section_key, detected_at DESC);
CREATE INDEX legal_source_changes_run_idx     ON public.legal_source_changes (run_id);

COMMENT ON TABLE public.legal_source_changes IS
    'class=PUBLIC. What changed in the law and when we noticed. Public so '
    'the /law page can show a section''s amendment trail.';
COMMENT ON TABLE public.legal_refresh_runs IS
    'class=PRIVATE. Operational log of refresh runs. No read policy.';

ALTER TABLE public.legal_refresh_runs   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.legal_source_changes ENABLE ROW LEVEL SECURITY;

CREATE POLICY legal_source_changes_read ON public.legal_source_changes FOR SELECT USING (true);

REVOKE ALL ON public.legal_refresh_runs FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.legal_source_changes FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.legal_source_changes_id_seq FROM anon, authenticated;

-- ---------------------------------------------------------------------
-- 3. Ingest tokens: sha256 only, readable by nobody but the functions
-- ---------------------------------------------------------------------

CREATE TABLE public.corpus_ingest_tokens (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    label      text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

COMMENT ON TABLE public.corpus_ingest_tokens IS
    'class=SECRET-DERIVED. sha256 of ingest tokens. RLS on with no policies: '
    'only the SECURITY DEFINER corpus_* functions read it. Generate a token '
    'with `python -m tools.corpus.refresh --new-token` on the owner''s machine.';

ALTER TABLE public.corpus_ingest_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.corpus_ingest_tokens FROM anon, authenticated;

-- ---------------------------------------------------------------------
-- 4. Functions
-- ---------------------------------------------------------------------

-- The hash every write is checked against. Must match
-- tools/corpus/model.py Section.content_hash: collapse runs of ASCII
-- whitespace to one space, trim, sha256 the UTF-8 bytes, lowercase hex.
-- Spelled with chr() so no regex-escape dialect question arises.
CREATE FUNCTION public.corpus_law_hash(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, extensions, pg_temp
AS $$
    SELECT encode(
        extensions.digest(
            convert_to(
                btrim(
                    regexp_replace(
                        p_text,
                        '[' || chr(32) || chr(9) || chr(10) || chr(11) || chr(12) || chr(13) || ']+',
                        ' ',
                        'g'
                    ),
                    ' '
                ),
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

CREATE FUNCTION public.corpus_check_token(p_token text)
RETURNS void
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
BEGIN
    IF p_token IS NULL OR length(p_token) < 32 OR NOT EXISTS (
        SELECT 1 FROM public.corpus_ingest_tokens
        WHERE token_hash = encode(extensions.digest(convert_to(p_token, 'UTF8'), 'sha256'), 'hex')
          AND revoked_at IS NULL
    ) THEN
        RAISE EXCEPTION 'invalid ingest token' USING ERRCODE = '28000';
    END IF;
END;
$$;

CREATE FUNCTION public.corpus_begin_run(p_token text, p_trigger text DEFAULT 'manual')
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    PERFORM public.corpus_check_token(p_token);
    INSERT INTO public.legal_refresh_runs (trigger)
    VALUES (left(coalesce(p_trigger, 'manual'), 40))
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- One batch of parsed sections. Returns [{section_key, change}] where
-- change is added | amended | restored | unchanged.
CREATE FUNCTION public.corpus_upsert_sections(p_token text, p_run_id uuid, p_sections jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    s           jsonb;
    c           jsonb;
    v_key       text;
    v_source    text;
    v_citation  text;
    v_text      text;
    v_hash      text;
    v_existing  public.legal_sources%ROWTYPE;
    v_id        bigint;
    v_change    text;
    v_label     text;
    v_results   jsonb := '[]'::jsonb;
BEGIN
    PERFORM public.corpus_check_token(p_token);

    IF NOT EXISTS (SELECT 1 FROM public.legal_refresh_runs WHERE id = p_run_id AND status = 'running') THEN
        RAISE EXCEPTION 'run % is not running', p_run_id;
    END IF;
    IF jsonb_typeof(p_sections) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'p_sections must be a JSON array';
    END IF;
    IF jsonb_array_length(p_sections) > 50 THEN
        RAISE EXCEPTION 'batch too large: % sections (max 50)', jsonb_array_length(p_sections);
    END IF;

    FOR s IN SELECT value FROM jsonb_array_elements(p_sections) LOOP
        v_key      := s->>'section_key';
        v_source   := s->>'source_key';
        v_citation := s->>'citation';
        v_text     := coalesce(s->>'full_text', '');

        IF v_key IS NULL OR v_source IS NULL OR v_citation IS NULL
           OR v_key <> v_source || ':' || v_citation
           OR v_source !~ '^[a-z0-9-]{2,40}$'
           OR length(v_citation) > 40 THEN
            RAISE EXCEPTION 'bad section identity: %', left(coalesce(v_key, '<null>'), 80);
        END IF;
        IF coalesce(s->>'official_url', '') !~ '^https://' THEN
            RAISE EXCEPTION '%: official_url must be https', v_key;
        END IF;
        IF length(v_text) > 1000000 THEN
            RAISE EXCEPTION '%: text too long (% chars)', v_key, length(v_text);
        END IF;
        IF jsonb_typeof(s->'chunks') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'chunks') > 500 THEN
            RAISE EXCEPTION '%: chunks must be an array of at most 500', v_key;
        END IF;
        IF NOT coalesce((s->>'repealed')::boolean, false) AND btrim(v_text) = '' THEN
            RAISE EXCEPTION '%: empty text on a section not marked repealed', v_key;
        END IF;

        v_hash := public.corpus_law_hash(v_text);
        IF v_hash IS DISTINCT FROM s->>'content_hash' THEN
            RAISE EXCEPTION '%: content_hash mismatch (server %, client %)', v_key, v_hash, s->>'content_hash';
        END IF;

        v_label := coalesce(s->>'authority', '') || ' ' || v_citation;

        SELECT * INTO v_existing FROM public.legal_sources WHERE section_key = v_key FOR UPDATE;

        IF NOT FOUND THEN
            v_change := 'added';
            INSERT INTO public.legal_sources (
                section_key, source_key, authority, citation, title, jurisdiction,
                official_url, heading_path, full_text, history, notes, repealed,
                last_amended, content_hash, retrieved_at, last_checked_at, status
            ) VALUES (
                v_key, v_source, s->>'authority', v_citation, coalesce(s->>'title', ''),
                coalesce(s->>'jurisdiction', 'NYC'), s->>'official_url',
                coalesce(s->>'heading_path', ''), v_text,
                coalesce(s->'history', '[]'::jsonb), coalesce(s->'notes', '[]'::jsonb),
                coalesce((s->>'repealed')::boolean, false),
                (s->>'last_amended')::date, v_hash, now(), now(), 'active'
            )
            RETURNING id INTO v_id;
        ELSE
            v_id := v_existing.id;
            IF v_existing.content_hash IS DISTINCT FROM v_hash THEN
                v_change := 'amended';
            ELSIF v_existing.status <> 'active' THEN
                v_change := 'restored';
            ELSE
                v_change := 'unchanged';
            END IF;
            -- Metadata (title, history, notes, URL) is refreshed every run;
            -- only a change to the law text itself counts as an amendment.
            UPDATE public.legal_sources SET
                authority       = s->>'authority',
                title           = coalesce(s->>'title', ''),
                jurisdiction    = coalesce(s->>'jurisdiction', 'NYC'),
                official_url    = s->>'official_url',
                heading_path    = coalesce(s->>'heading_path', ''),
                full_text       = v_text,
                history         = coalesce(s->'history', '[]'::jsonb),
                notes           = coalesce(s->'notes', '[]'::jsonb),
                repealed        = coalesce((s->>'repealed')::boolean, false),
                last_amended    = (s->>'last_amended')::date,
                content_hash    = v_hash,
                retrieved_at    = CASE WHEN v_change = 'amended' THEN now() ELSE retrieved_at END,
                last_checked_at = now(),
                status          = 'active'
            WHERE id = v_id;
        END IF;

        IF v_change IN ('added', 'amended') THEN
            -- New text means new chunks; any old embeddings described old law.
            DELETE FROM public.legal_documents WHERE source_id = v_id;
            FOR c IN SELECT value FROM jsonb_array_elements(s->'chunks') LOOP
                IF length(coalesce(c->>'text', '')) = 0 OR length(c->>'text') > 8000 THEN
                    RAISE EXCEPTION '%: chunk % empty or too long', v_key, c->>'ordinal';
                END IF;
                INSERT INTO public.legal_documents (source_id, source_name, text_content, heading_path, ordinal, content_hash)
                VALUES (
                    v_id, v_label, c->>'text', coalesce(s->>'heading_path', ''),
                    (c->>'ordinal')::int,
                    encode(extensions.digest(convert_to(c->>'text', 'UTF8'), 'sha256'), 'hex')
                );
            END LOOP;
        END IF;

        IF v_change <> 'unchanged' THEN
            INSERT INTO public.legal_source_changes (section_key, change_type, old_hash, new_hash, run_id)
            VALUES (v_key, v_change, v_existing.content_hash, v_hash, p_run_id);
        END IF;

        v_results := v_results || jsonb_build_object('section_key', v_key, 'change', v_change);
        v_existing := NULL;
    END LOOP;

    RETURN v_results;
END;
$$;

-- Before any section of a source is upserted: refuse a parse that covers
-- fewer than 80% of the sections active right now. Read-only.
CREATE FUNCTION public.corpus_preflight_source(p_token text, p_run_id uuid, p_source_key text, p_seen_keys text[])
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    v_active  int;
    v_covered int;
BEGIN
    PERFORM public.corpus_check_token(p_token);
    IF NOT EXISTS (SELECT 1 FROM public.legal_refresh_runs WHERE id = p_run_id AND status = 'running') THEN
        RAISE EXCEPTION 'run % is not running', p_run_id;
    END IF;
    SELECT count(*), count(*) FILTER (WHERE section_key = ANY (coalesce(p_seen_keys, ARRAY[]::text[])))
    INTO v_active, v_covered
    FROM public.legal_sources
    WHERE source_key = p_source_key AND status = 'active';
    IF v_active > 0 AND v_covered < ceil(0.8 * v_active) THEN
        RAISE EXCEPTION '%: parse covers only % of % active sections (< 80%%); refusing to write',
            p_source_key, v_covered, v_active;
    END IF;
    RETURN jsonb_build_object('source_key', p_source_key, 'active', v_active, 'covered', v_covered);
END;
$$;

-- After every section of a source has been upserted: mark the active
-- sections that were NOT seen as missing_from_source. Never deletes.
CREATE FUNCTION public.corpus_finalize_source(p_token text, p_run_id uuid, p_source_key text, p_seen_keys text[])
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    v_active   int;
    v_seen     int;
    v_unlisted int;
    v_missing  text[];
BEGIN
    PERFORM public.corpus_check_token(p_token);

    IF NOT EXISTS (SELECT 1 FROM public.legal_refresh_runs WHERE id = p_run_id AND status = 'running') THEN
        RAISE EXCEPTION 'run % is not running', p_run_id;
    END IF;

    -- Count only sections that were active BEFORE this run: rows this run
    -- added or restored are excluded, so new keys cannot pad the count.
    WITH prior AS (
        SELECT ls.section_key
        FROM public.legal_sources ls
        WHERE ls.source_key = p_source_key
          AND ls.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM public.legal_source_changes c
              WHERE c.run_id = p_run_id AND c.section_key = ls.section_key
                AND c.change_type IN ('added', 'restored'))
    )
    SELECT count(*), count(*) FILTER (WHERE section_key = ANY (coalesce(p_seen_keys, ARRAY[]::text[])))
    INTO v_active, v_seen
    FROM prior;

    -- Every section this run wrote must be in the seen list; otherwise the
    -- client's key list is inconsistent and "missing" would be wrong.
    SELECT count(*) INTO v_unlisted
    FROM public.legal_source_changes c
    JOIN public.legal_sources ls ON ls.section_key = c.section_key AND ls.source_key = p_source_key
    WHERE c.run_id = p_run_id AND c.change_type IN ('added', 'amended', 'restored')
      AND NOT (c.section_key = ANY (coalesce(p_seen_keys, ARRAY[]::text[])));
    IF v_unlisted > 0 THEN
        RAISE EXCEPTION '%: % sections written in this run are not in the seen list; refusing to finalize',
            p_source_key, v_unlisted;
    END IF;

    -- A broken parse must not empty the library.
    IF v_active > 0 AND v_seen < ceil(0.8 * v_active) THEN
        RAISE EXCEPTION '%: only % of % active sections seen (< 80%%); refusing to finalize',
            p_source_key, v_seen, v_active;
    END IF;

    WITH gone AS (
        UPDATE public.legal_sources
        SET status = 'missing_from_source'
        WHERE source_key = p_source_key
          AND status = 'active'
          AND NOT (section_key = ANY (coalesce(p_seen_keys, ARRAY[]::text[])))
        RETURNING section_key, content_hash
    ), logged AS (
        INSERT INTO public.legal_source_changes (section_key, change_type, old_hash, new_hash, run_id)
        SELECT section_key, 'missing_from_source', content_hash, NULL, p_run_id FROM gone
        RETURNING section_key
    )
    SELECT coalesce(array_agg(section_key ORDER BY section_key), ARRAY[]::text[]) INTO v_missing FROM logged;

    RETURN jsonb_build_object('source_key', p_source_key, 'active_before', v_active,
                              'seen', v_seen, 'missing', to_jsonb(v_missing));
END;
$$;

CREATE FUNCTION public.corpus_finish_run(p_token text, p_run_id uuid, p_status text, p_summary jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
BEGIN
    PERFORM public.corpus_check_token(p_token);
    IF p_status NOT IN ('succeeded', 'failed') THEN
        RAISE EXCEPTION 'bad status %', p_status;
    END IF;
    UPDATE public.legal_refresh_runs
    SET status = p_status, finished_at = now(), summary = p_summary
    WHERE id = p_run_id AND status = 'running';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'run % is not running', p_run_id;
    END IF;
END;
$$;

-- New functions are executable by PUBLIC by default. Lock everything
-- down, then open exactly the token-checked entry points to anon (the
-- refresh job calls them with the anon key plus the ingest token).
REVOKE ALL ON FUNCTION public.corpus_law_hash(text)                               FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_check_token(text)                            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_begin_run(text, text)                        FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_upsert_sections(text, uuid, jsonb)           FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_preflight_source(text, uuid, text, text[])   FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_finalize_source(text, uuid, text, text[])    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.corpus_finish_run(text, uuid, text, jsonb)          FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.corpus_begin_run(text, text)                     TO anon;
GRANT EXECUTE ON FUNCTION public.corpus_upsert_sections(text, uuid, jsonb)        TO anon;
GRANT EXECUTE ON FUNCTION public.corpus_preflight_source(text, uuid, text, text[]) TO anon;
GRANT EXECUTE ON FUNCTION public.corpus_finalize_source(text, uuid, text, text[]) TO anon;
GRANT EXECUTE ON FUNCTION public.corpus_finish_run(text, uuid, text, jsonb)       TO anon;

-- ---------------------------------------------------------------------
-- 5. Search returns active sections only
-- ---------------------------------------------------------------------
-- Same signatures and return types as 20260921/20260922, so CREATE OR
-- REPLACE is allowed. The one change: rows must join an active source.
-- Chunks with no source (source_id NULL) are no longer returned; nothing
-- has ever been loaded without one.

CREATE OR REPLACE FUNCTION public.match_legal_documents(
    query_embedding extensions.vector(768),
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
SET search_path = public, extensions, pg_temp
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
    JOIN legal_sources s ON s.id = d.source_id AND s.status = 'active'
    WHERE d.embedding IS NOT NULL
      AND 1 - (d.embedding <=> query_embedding) > match_threshold
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION public.search_legal_documents(
    query_embedding extensions.vector(768),
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
SET search_path = public, extensions, pg_temp
AS $$
    WITH active_docs AS (
        SELECT d.*
        FROM legal_documents d
        JOIN legal_sources s ON s.id = d.source_id AND s.status = 'active'
    ),
    vector_hits AS (
        SELECT d.id,
               row_number() OVER (ORDER BY d.embedding <=> query_embedding) AS rank,
               1 - (d.embedding <=> query_embedding) AS similarity
        FROM active_docs d
        WHERE query_embedding IS NOT NULL AND d.embedding IS NOT NULL
        ORDER BY d.embedding <=> query_embedding
        LIMIT match_count * 4
    ),
    text_hits AS (
        SELECT d.id,
               row_number() OVER (
                   ORDER BY ts_rank_cd(d.fts, websearch_to_tsquery('english', query_text)) DESC
               ) AS rank
        FROM active_docs d
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
    JOIN legal_sources s ON s.id = d.source_id
    ORDER BY f.fused_score DESC
    LIMIT match_count;
$$;
