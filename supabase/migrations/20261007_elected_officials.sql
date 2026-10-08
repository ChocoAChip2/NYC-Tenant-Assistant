-- Elected officials for the street lookup (area_service.py).
--
-- One row per office and district: City Council (from NYC Open Data),
-- State Assembly and State Senate (from the NY Senate Open Legislation
-- API). Loaded by tools/officials/refresh.py, run by the "Refresh Elected
-- Officials" workflow after elections (owner, 2026-10-07: about a month
-- after results are called, leaving time for certification and runoffs).
--
-- Public facts, readable by anyone, like the law library. Writes happen
-- ONLY through officials_replace(), which checks the same ingest token as
-- the corpus_* functions (corpus_check_token). The service-role key is
-- never used.

CREATE TABLE public.elected_officials (
    office       text        NOT NULL CHECK (office IN ('council', 'assembly', 'state_senate')),
    district     integer     NOT NULL CHECK (district BETWEEN 1 AND 200),
    name         text        NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
    source       text        NOT NULL CHECK (length(source) BETWEEN 2 AND 200),
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (office, district)
);

COMMENT ON TABLE public.elected_officials IS
    'Current Council Members, Assembly Members and State Senators by district. '
    'Written only by officials_replace() (token-checked); see tools/officials.';

ALTER TABLE public.elected_officials ENABLE ROW LEVEL SECURITY;
CREATE POLICY elected_officials_read ON public.elected_officials FOR SELECT USING (true);
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.elected_officials FROM anon, authenticated;
GRANT SELECT ON public.elected_officials TO anon, authenticated;

-- Replace one office's whole list. Districts missing from p_rows are
-- removed (a vacancy shows as "see who represents District N" rather
-- than a stale name). Returns what changed, for the run summary.
CREATE FUNCTION public.officials_replace(p_token text, p_office text, p_source text, p_rows jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    v_changes jsonb := '[]'::jsonb;
    r jsonb;
    v_district integer;
    v_name text;
    v_old text;
BEGIN
    PERFORM public.corpus_check_token(p_token);
    IF p_office NOT IN ('council', 'assembly', 'state_senate') THEN
        RAISE EXCEPTION 'unknown office %', p_office;
    END IF;
    IF jsonb_typeof(p_rows) <> 'array' OR jsonb_array_length(p_rows) = 0 THEN
        RAISE EXCEPTION 'no rows for %', p_office;
    END IF;

    FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
        v_district := (r->>'district')::integer;
        v_name := btrim(r->>'name');
        SELECT name INTO v_old FROM public.elected_officials WHERE office = p_office AND district = v_district;
        IF v_old IS NULL THEN
            v_changes := v_changes || jsonb_build_object('district', v_district, 'change', 'added', 'name', v_name);
        ELSIF v_old IS DISTINCT FROM v_name THEN
            v_changes := v_changes || jsonb_build_object('district', v_district, 'change', 'changed', 'old', v_old, 'name', v_name);
        END IF;
        INSERT INTO public.elected_officials (office, district, name, source, refreshed_at)
        VALUES (p_office, v_district, v_name, left(p_source, 200), now())
        ON CONFLICT (office, district) DO UPDATE
            SET name = EXCLUDED.name, source = EXCLUDED.source, refreshed_at = now();
    END LOOP;

    WITH gone AS (
        DELETE FROM public.elected_officials
        WHERE office = p_office
          AND district NOT IN (SELECT (e->>'district')::integer FROM jsonb_array_elements(p_rows) e)
        RETURNING district, name
    )
    SELECT v_changes || coalesce(jsonb_agg(jsonb_build_object('district', district, 'change', 'removed', 'old', name)), '[]'::jsonb)
    INTO v_changes FROM gone;

    RETURN v_changes;
END;
$$;

REVOKE ALL ON FUNCTION public.officials_replace(text, text, text, jsonb) FROM public;
-- Callable with the public key ONLY because the token check guards it,
-- exactly like corpus_upsert_sections.
GRANT EXECUTE ON FUNCTION public.officials_replace(text, text, text, jsonb) TO anon, authenticated;
