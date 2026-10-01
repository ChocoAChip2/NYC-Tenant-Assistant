-- Row-level security: evaluate auth.uid() once per query, not once per row.
--
-- Supabase's performance advisor (lint 0003 auth_rls_initplan) flagged all
-- twelve per-user policies: written as `auth.uid() = user_id`, Postgres
-- re-runs auth.uid() for every row it checks. Wrapping it as
-- `(select auth.uid())` lets the planner compute it once. Who can see or
-- change what is exactly the same -- only the plan changes. See
-- https://supabase.com/docs/guides/database/postgres/row-level-security#call-functions-with-select
--
-- The conversations/messages policies were created in the Supabase
-- dashboard before migrations were kept in this repo; this file is now
-- their record. Applied live 2026-10-01.

alter policy "Users can cancel their own deletion request" on public.account_deletion_requests
  using ((select auth.uid()) = user_id);
alter policy "Users can request their own deletion" on public.account_deletion_requests
  with check ((select auth.uid()) = user_id);
alter policy "Users can update their own deletion request" on public.account_deletion_requests
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
alter policy "Users can view their own deletion request" on public.account_deletion_requests
  using ((select auth.uid()) = user_id);

alter policy conversations_delete_own on public.conversations
  using (user_id = (select auth.uid()));
alter policy conversations_insert_own on public.conversations
  with check (user_id = (select auth.uid()));
alter policy conversations_select_own on public.conversations
  using (user_id = (select auth.uid()));
alter policy conversations_update_own on public.conversations
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

alter policy messages_delete_own on public.messages
  using (user_id = (select auth.uid()));
alter policy messages_insert_own on public.messages
  with check (
    user_id = (select auth.uid())
    and exists (
      select 1 from public.conversations c
      where c.id = messages.conversation_id and c.user_id = (select auth.uid())
    )
  );
alter policy messages_select_own on public.messages
  using (user_id = (select auth.uid()));
alter policy messages_update_own on public.messages
  using (user_id = (select auth.uid()))
  with check (
    user_id = (select auth.uid())
    and exists (
      select 1 from public.conversations c
      where c.id = messages.conversation_id and c.user_id = (select auth.uid())
    )
  );
