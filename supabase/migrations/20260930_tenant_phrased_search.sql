-- Let tenants find the law in their own words.
--
-- WHY: the library loaded on 2026-09-29, and full-text search then found
-- nothing for most everyday questions. "my landlord changed the locks",
-- "mold in my bathroom", "free lawyer for housing court" and "no heat at
-- night" all returned 0 sources. websearch_to_tsquery ANDs every word, and
-- tenants and the Admin Code use different words (landlord/owner,
-- lawyer/counsel, roaches/cockroaches, bed bugs/bedbug). On a 23-question
-- tenant test set, recall@6 was 6/23 (log/2026-09-30-tenant-phrased-search.txt).
--
-- WHAT CHANGES (search_legal_documents only; signature and return type
-- are unchanged, so the app needs no change):
--
--   1. legal_search_synonyms maps tenant words to the words the code uses.
--      Each query word becomes a GROUP: the word itself OR its law words.
--      The table is public-read and data, not code, so the list can grow
--      without a migration.
--   2. Filler words that carry no legal meaning ("apartment", "get",
--      "section", single digits...) are dropped, so they can't make a
--      match fail.
--   3. Instead of requiring every word, sections are scored by the groups
--      they match, each weighted by rarity (IDF: "owner" is in most
--      sections and counts for little, "bedbug" counts for a lot), plus a
--      bonus for groups in the section's TITLE ("Minimum temperature to be
--      maintained", "Unlawful eviction"). A section must match two groups
--      (or the only one), or one group that is in its title, so a lone
--      common word can't drag in noise. A quoted phrase still matches
--      exactly first.
--   4. One chunk per section. Otherwise a long section like § 26-511
--      filled three of the six slots with itself.
--
-- Vector search (when embeddings exist) and the RRF fusion are unchanged.

CREATE TABLE IF NOT EXISTS public.legal_search_synonyms (
    tenant_word text PRIMARY KEY CHECK (tenant_word = lower(btrim(tenant_word)) AND tenant_word <> ''),
    law_words   text NOT NULL CHECK (btrim(law_words) <> '')
);

COMMENT ON TABLE public.legal_search_synonyms IS
    'class=PUBLIC. Tenant words -> the words the Admin Code uses, for '
    'search_legal_documents. law_words is a comma-separated list; each item '
    'is a word or an exact phrase ("essential services"), stemmed at query '
    'time with the english config, same as legal_documents.fts.';

ALTER TABLE public.legal_search_synonyms ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_search_synonyms_read ON public.legal_search_synonyms;
CREATE POLICY legal_search_synonyms_read ON public.legal_search_synonyms FOR SELECT USING (true);
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.legal_search_synonyms FROM anon, authenticated;

INSERT INTO public.legal_search_synonyms (tenant_word, law_words) VALUES
    ('landlord'       , 'owner'),
    ('landlords'      , 'owner'),
    ('management'     , 'owner, managing agent'),
    ('super'          , 'janitor'),
    ('superintendent' , 'janitor'),
    ('lock'           , 'lock, evict'),
    ('locks'          , 'lock, evict'),
    ('locked'         , 'lock, evict'),
    ('lockout'        , 'lock, evict, unlawful eviction'),
    ('kick'           , 'evict'),
    ('kicked'         , 'evict'),
    ('throw'          , 'evict'),
    ('counsel'        , 'legal services'),
    ('lawyer'         , 'counsel, legal services, attorney'),
    ('lawyers'        , 'counsel, legal services, attorney'),
    ('attorney'       , 'counsel, legal services'),
    ('heat'           , 'heat, temperature'),
    ('heating'        , 'heat, temperature'),
    ('cold'           , 'heat, temperature'),
    ('freezing'       , 'heat, temperature'),
    ('radiator'       , 'heat'),
    ('boiler'         , 'heat, boiler'),
    ('winter'         , 'heat'),
    ('mould'          , 'mold'),
    ('mildew'         , 'mold'),
    ('roach'          , 'cockroach, pest'),
    ('roaches'        , 'cockroaches, pest'),
    ('mouse'          , 'mice, pest'),
    ('mice'           , 'mice, pest'),
    ('rat'            , 'rats, pest'),
    ('rats'           , 'rats, pest'),
    ('rodent'         , 'rats, mice, pest'),
    ('rodents'        , 'rats, mice, pest'),
    ('bug'            , 'bedbug, pest'),
    ('bugs'           , 'bedbug, pest'),
    ('bed'            , 'bedbug'),
    ('vermin'         , 'pest'),
    ('kid'            , 'child'),
    ('kids'           , 'child'),
    ('children'       , 'child'),
    ('baby'           , 'child'),
    ('toddler'        , 'child'),
    ('son'            , 'child'),
    ('daughter'       , 'child'),
    ('detector'       , 'detecting'),
    ('alarm'          , 'detecting'),
    ('fix'            , 'repair'),
    ('broken'         , 'repair'),
    ('leak'           , 'leak, water'),
    ('leaking'        , 'leak, water'),
    ('electricity'    , 'electricity, essential services, utility'),
    ('electric'       , 'electricity, essential services, utility'),
    ('power'          , 'electricity, essential services'),
    ('lights'         , 'electricity, essential services'),
    ('gas'            , 'gas, essential services'),
    ('shut'           , 'essential services, discontinuance'),
    ('cut'            , 'essential services, discontinuance'),
    ('leave'          , 'vacate'),
    ('harassing'      , 'harassment'),
    ('bullying'       , 'harassment'),
    ('threatening'    , 'harassment'),
    ('voucher'        , 'lawful source of income'),
    ('vouchers'       , 'lawful source of income'),
    ('cityfheps'      , 'lawful source of income'),
    ('discriminate'   , 'discriminatory'),
    ('discrimination' , 'discriminatory'),
    ('wheelchair'     , 'disability'),
    ('controlled'     , 'control'),
    ('stabilized'     , 'stabilization'),
    ('renewal'        , 'renew, lease'),
    ('bathroom'       , 'room, bath'),
    ('bedroom'        , 'room'),
    ('kitchen'        , 'room')
ON CONFLICT (tenant_word) DO UPDATE SET law_words = EXCLUDED.law_words;

-- The query's word groups: one row per meaningful query lexeme, holding
-- that lexeme plus the stems of its law words. STABLE, runs as the caller.
CREATE OR REPLACE FUNCTION public.legal_query_groups(p_query text)
RETURNS TABLE (grp int, lexemes text[])  -- lexemes: tsquery fragments
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    WITH words AS (
        SELECT DISTINCT lower(w) AS word
        FROM regexp_split_to_table(coalesce(p_query, ''), '[^[:alnum:]]+') AS w
        WHERE w <> ''
    ),
    stems AS (
        SELECT w.word, lx
        FROM words w,
             unnest(tsvector_to_array(to_tsvector('english', w.word))) AS lx
        -- No legal meaning in a tenant question; requiring them would only
        -- make matches fail. Stems, as to_tsvector('english') produces them.
        WHERE lx <> ALL (ARRAY['apart', 'get', 'make', 'go', 'want', 'need', 'help',
                               'pleas', 'know', 'tell', 'can', 'would', 'could',
                               'someth', 'thing', 'happen', 'hi', 'hello', 'thank',
                               'section', 'said', 'say', 'realli', 'also', 'way',
                               'one', 'still', 'now', 'turn', 'work', 'doesn',
                               'ignor', 'use', 'like'])
          AND lx !~ '^[0-9]$'
          AND length(lx) > 1  -- stray letters ("a", "b") match every lettered subdivision
    ),
    expanded AS (
        -- Each element is a tsquery fragment: a quoted lexeme, or a phrase
        -- such as 'essenti' <-> 'servic' so that "essential services" does
        -- not match every section that merely says "services".
        SELECT s.lx AS key, quote_literal(s.lx) AS lexeme FROM stems s
        UNION
        SELECT s.lx, phraseto_tsquery('english', item)::text
        FROM stems s
        JOIN public.legal_search_synonyms syn ON syn.tenant_word = s.word
        CROSS JOIN LATERAL unnest(string_to_array(syn.law_words, ',')) AS item
        WHERE numnode(phraseto_tsquery('english', item)) > 0
    )
    SELECT (dense_rank() OVER (ORDER BY key))::int, array_agg(DISTINCT lexeme ORDER BY lexeme)
    FROM expanded
    GROUP BY key;
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
    groups_raw AS (
        SELECT grp,
               to_tsquery('simple', array_to_string(
                   ARRAY(SELECT '(' || l || ')' FROM unnest(lexemes) AS l), ' | ')) AS q
        FROM legal_query_groups(query_text)
    ),
    doc_count AS (
        SELECT greatest(count(*), 1)::float AS n FROM active_docs
    ),
    groups AS (
        -- Each group is weighted by how rare it is in the library (IDF), so
        -- "owner", which is in most sections, counts for little and
        -- "bedbug" counts for a lot.
        SELECT g.grp, g.q,
               ln(dc.n / (1 + (SELECT count(*) FROM active_docs d WHERE d.fts @@ g.q))) + 1 AS weight
        FROM groups_raw g, doc_count dc
    ),
    any_q AS (
        SELECT count(*) AS n_groups,
               CASE WHEN count(*) = 0 THEN NULL
                    ELSE to_tsquery('simple', string_agg('(' || q::text || ')', ' | ')) END AS q
        FROM groups
    ),
    strict_q AS (
        -- A quoted phrase is honoured exactly, as before.
        SELECT CASE WHEN position('"' IN coalesce(query_text, '')) = 0 THEN NULL
                    ELSE websearch_to_tsquery('english', query_text) END AS q
    ),
    text_scored AS (
        SELECT d.id,
               d.source_id,
               (sq.q IS NOT NULL AND numnode(sq.q) > 0 AND d.fts @@ sq.q) AS strict_hit,
               (SELECT count(*) FROM groups g WHERE d.fts @@ g.q) AS covered,
               (SELECT count(*) FROM groups g WHERE to_tsvector('english', s.title) @@ g.q) AS title_covered,
               -- Rare words matched in the text, counted again when they are in
               -- the section's title, then a pinch of ts_rank_cd to break ties.
               coalesce((SELECT sum(g.weight) FROM groups g WHERE d.fts @@ g.q), 0)
             + coalesce((SELECT sum(g.weight) FROM groups g
                                WHERE to_tsvector('english', s.title) @@ g.q), 0)
             + ts_rank_cd(d.fts, a.q) AS score
        FROM active_docs d
        JOIN legal_sources s ON s.id = d.source_id,
        any_q a, strict_q sq
        WHERE a.q IS NOT NULL AND d.fts @@ a.q
    ),
    text_best AS (
        -- One chunk per section: the best-scoring one. A section qualifies
        -- by matching two groups (or the only one), or one group that is
        -- also in its title ("my radiator doesn't work" -> "Central heat
        -- ... when required").
        SELECT DISTINCT ON (t.source_id) t.*
        FROM text_scored t, any_q a
        WHERE t.strict_hit OR t.covered >= least(a.n_groups, 2) OR t.title_covered >= 1
        ORDER BY t.source_id, t.strict_hit DESC, t.score DESC
    ),
    text_hits AS (
        SELECT t.id,
               row_number() OVER (ORDER BY t.strict_hit DESC, t.score DESC) AS rank
        FROM text_best t
        ORDER BY t.strict_hit DESC, t.score DESC
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

GRANT EXECUTE ON FUNCTION public.legal_query_groups(text) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_legal_documents(extensions.vector, text, int, int) TO anon, authenticated;
