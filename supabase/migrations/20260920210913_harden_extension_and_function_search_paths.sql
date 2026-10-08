-- Two findings from Supabase's security linter, both raised by applying the
-- two migrations above to the live project. Neither is theoretical:
--
--   1. `vector` was installed into `public` (by 20260920). Anything in
--      `public` is reachable through PostgREST and shows up in the
--      auto-generated GraphQL schema, and an extension's functions are not
--      something the app should be exposing at all. Supabase's own
--      convention is the `extensions` schema.
--
--   2. `bump_conversation_updated_at` -- the trigger that has been running
--      on `conversations` since the chat-persistence branch -- had a
--      mutable search_path. A function without a pinned search_path
--      resolves unqualified names against whatever the CALLER's search_path
--      says, so a caller who can create a schema can shadow a built-in and
--      have this function call their version instead. It is a small hole in
--      a trigger this simple, but it costs one line to close and the linter
--      is right to flag it.
--
-- Moving the extension means the search functions can no longer find the
-- `<=>` operator under `public` alone, so `extensions` joins their pinned
-- search_path. Verified against the live database inside a rolled-back
-- transaction before being applied: the move, the re-pinning and a hybrid
-- search over a seeded row all succeed together.
--
-- Doing this now is deliberate. The corpus is still empty, so an extension
-- move that would be delicate over a populated HNSW index is free today.

ALTER EXTENSION vector SET SCHEMA extensions;

ALTER FUNCTION public.bump_conversation_updated_at()
    SET search_path = public, pg_temp;

ALTER FUNCTION public.match_legal_documents(extensions.vector, float, int)
    SET search_path = public, extensions, pg_temp;

ALTER FUNCTION public.search_legal_documents(extensions.vector, text, int, int)
    SET search_path = public, extensions, pg_temp;
