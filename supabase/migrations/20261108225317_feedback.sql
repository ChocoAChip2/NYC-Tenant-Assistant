-- Feedback from the chat's "Was this chat helpful?" question (feedback.py).
--
-- WHY (owner, 2026-11-08): collect feedback at a good moment, when a longer
-- chat is coming to an end. This is the database half.
--
-- WHAT A ROW HOLDS: the day (no time), a rating 1-3, an optional comment
-- encrypted by the app (an "enc:v..." envelope; plaintext is refused), what
-- prompted the question and the page language. No account, conversation,
-- IP address or time of day.
--
-- WHO CAN DO WHAT
--   authenticated: execute submit_feedback(). Nothing else.
--   anon: nothing.
--   The owner reads rows from the SQL editor; comments are decrypted with
--   the app's data key, as with the launch list.
--
-- RETENTION: a daily pg_cron job deletes rows older than 12 months.
--
-- VERSION: applied 2026-11-08 as 20261108225317 (owner approved).

create table if not exists public.feedback (
  id bigint generated always as identity primary key,
  day date not null default current_date,
  rating smallint not null check (rating between 1 and 3),
  comment text check (comment is null or (comment like 'enc:v%' and char_length(comment) <= 4000)),
  trigger text not null check (trigger in ('closing', 'idle')),
  language text not null default 'en' check (language ~ '^[A-Za-z_]{2,10}$')
);

comment on table public.feedback is
  'Answers to the chat feedback question. No identifiers; comment encrypted by the app; written only by submit_feedback(); purged after 12 months.';

alter table public.feedback enable row level security;
revoke all on table public.feedback from public, anon, authenticated;

create or replace function public.submit_feedback(p_rating integer, p_comment text, p_trigger text, p_language text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_rating is null or p_rating not between 1 and 3 then
    raise exception 'bad rating' using errcode = '22023';
  end if;
  if p_trigger is null or p_trigger not in ('closing', 'idle') then
    raise exception 'bad trigger' using errcode = '22023';
  end if;
  if p_comment is not null and (p_comment not like 'enc:v%' or char_length(p_comment) > 4000) then
    raise exception 'bad comment' using errcode = '22023';
  end if;
  if (select count(*) from public.feedback where day = current_date) >= 5000 then
    raise exception 'too many today' using errcode = '54000';
  end if;
  insert into public.feedback (rating, comment, trigger, language)
  values (p_rating, p_comment, p_trigger,
          coalesce(nullif(substring(p_language from '^[A-Za-z_]{2,10}$'), ''), 'en'));
end;
$$;

revoke all on function public.submit_feedback(integer, text, text, text) from public, anon;
grant execute on function public.submit_feedback(integer, text, text, text) to authenticated;

create extension if not exists pg_cron;

select cron.unschedule('purge-feedback')
where exists (select 1 from cron.job where jobname = 'purge-feedback');

select cron.schedule(
  'purge-feedback',
  '17 4 * * *',
  $cron$delete from public.feedback where day < (current_date - interval '12 months');$cron$
);
