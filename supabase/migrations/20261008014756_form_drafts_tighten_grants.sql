-- form_drafts is immutable (no UPDATE policy) and per-tenant. The 20261006
-- migration granted select/insert/delete, but Supabase's default
-- privileges had already given `authenticated` everything on new public
-- tables. TRUNCATE is not covered by row level security, so drop the
-- privileges the app never uses. Applied live 2026-10-08.
revoke update, truncate, references, trigger on public.form_drafts from authenticated;
