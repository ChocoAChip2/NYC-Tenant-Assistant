-- Recorded from the live migration history (applied 2026-09-06, before
-- this file existed in the repo).
alter table public.conversations
  add column if not exists archived_at timestamptz null;

comment on column public.conversations.archived_at is
  'Set when the user archives this conversation from the chat UI; null means active. Archiving is a soft hide, not a delete -- messages are untouched.';
