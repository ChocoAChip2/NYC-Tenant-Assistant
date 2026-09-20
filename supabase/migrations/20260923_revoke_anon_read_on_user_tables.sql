-- Supabase's security linter flags conversations, messages and
-- account_deletion_requests as discoverable in the auto-generated GraphQL
-- schema, because the `anon` role can SELECT them. RLS still protects every
-- ROW -- this is about the public key being able to learn that these tables
-- and columns exist at all.
--
-- Nothing in the app needs it. A Supabase client authenticates with the
-- publishable anon API KEY plus the user's JWT, and PostgREST then runs the
-- query as `authenticated`, not as `anon`. The three tables here all have
-- policies keyed on auth.uid(), so a request with no JWT could never see a
-- row anyway; the SELECT grant was pure surface.
--
-- One thing DID depend on it: .github/workflows/keepalive.yml pinged
-- `conversations` with the bare anon key every six days, relying on "the
-- role may select, RLS returns nothing" to produce a 200. That was a quiet
-- coupling between an uptime ping and tenants' private data being publicly
-- readable in principle. The workflow now pings `legal_sources` instead --
-- public law, anon-readable on purpose, and incapable of leaking user data
-- into a workflow log. That change ships in the same commit as this file.
--
-- NOT FIXED, and not fixable: the linter raises the same finding for the
-- `authenticated` role. The app genuinely needs signed-in users to be able
-- to select these tables; RLS is what makes that safe, and it is verified
-- by a cross-tenant isolation test (user A sees zero of user B's rows).

REVOKE SELECT ON conversations              FROM anon;
REVOKE SELECT ON messages                   FROM anon;
REVOKE SELECT ON account_deletion_requests  FROM anon;
