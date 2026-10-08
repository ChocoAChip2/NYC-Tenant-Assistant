-- Apply before deploying the case-context/review UI. Existing rows stay valid.
alter table public.conversations add column if not exists case_context text;
alter table public.conversations add column if not exists case_revision integer not null default 0;

create table if not exists public.form_drafts (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  payload text not null,
  created_at timestamptz not null default now()
);
create index if not exists form_drafts_conversation_idx on public.form_drafts(conversation_id, created_at);
alter table public.form_drafts enable row level security;
revoke all on public.form_drafts from anon;
grant select, insert, delete on public.form_drafts to authenticated;
create policy form_drafts_select_own on public.form_drafts for select to authenticated
  using (user_id = (select auth.uid()) and exists (
    select 1 from public.conversations c where c.id = conversation_id and c.user_id = (select auth.uid())
  ));
create policy form_drafts_insert_own on public.form_drafts for insert to authenticated
  with check (user_id = (select auth.uid()) and exists (
    select 1 from public.conversations c where c.id = conversation_id and c.user_id = (select auth.uid())
  ));
create policy form_drafts_delete_own on public.form_drafts for delete to authenticated
  using (user_id = (select auth.uid()));
