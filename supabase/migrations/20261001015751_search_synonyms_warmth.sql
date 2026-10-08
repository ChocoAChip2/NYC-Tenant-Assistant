-- 2026-10-01: tenant words for warmth that missed the heat section.
--
-- Found testing the live site: "How warm does my apartment have to be at
-- night in the winter?" retrieved 27-2033.1 and 27-2028 but not § 27-2029
-- (Minimum temperature to be maintained). With nothing stating the number,
-- the assistant fell back on memory and gave the pre-2017 rule (55°F when
-- it is below 40°F outside); the law since 2017 is 62°F overnight.
--
-- Data only: rows in legal_search_synonyms (20260930_tenant_phrased_search).
-- Checked in a rolled-back transaction first: with these rows, 27-2029 is
-- returned for the question above, for "what temperature must my landlord
-- keep the apartment at", and for "my apartment is chilly, how many degrees
-- is required".

INSERT INTO public.legal_search_synonyms (tenant_word, law_words) VALUES
    ('warm'      , 'heat, temperature'),
    ('warmth'    , 'heat, temperature'),
    ('degrees'   , 'temperature'),
    ('thermostat', 'heat, temperature'),
    ('chilly'    , 'heat, temperature')
ON CONFLICT (tenant_word) DO NOTHING;
