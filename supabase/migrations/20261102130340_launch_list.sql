-- The launch list: addresses of people who asked to hear when the site opens.
--
-- WHY (owner, 2026-11-01): the public domain shows a coming-soon page with
-- a box for an email address (coming_soon.py). This is the database half.
--
-- WHAT A ROW HOLDS: a keyed hash of the address (so the same address isn't
-- added twice), the address encrypted by the app (an "enc:v..." envelope;
-- plaintext is refused), the page language, and when it was added.
--
-- WHO CAN DO WHAT
--   anon / authenticated: execute join_launch_list(). Nothing else: no
--     select, insert, update or delete. The app has no service-role key,
--     so it can add an address but never read the list back.
--   The owner reads it from the SQL editor and decrypts it with
--     tools/export_launch_list.py when the launch email goes out, then
--     deletes the rows.
--
-- VERSION: applied 2026-11-02 as 20261102130340 (owner approved).

create table if not exists public.launch_list (
  email_key text primary key check (email_key ~ '^[0-9a-f]{64}$'),
  email text not null check (email like 'enc:v%' and char_length(email) <= 1000),
  language text not null default 'en' check (language ~ '^[A-Za-z_]{2,10}$'),
  created_at timestamptz not null default now()
);

comment on table public.launch_list is
  'People who asked to hear when the site launches. Address encrypted by the app; written only by join_launch_list().';

alter table public.launch_list enable row level security;
revoke all on table public.launch_list from public, anon, authenticated;

create or replace function public.join_launch_list(p_email_key text, p_email text, p_language text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_email_key is null or p_email_key !~ '^[0-9a-f]{64}$' then
    raise exception 'bad key' using errcode = '22023';
  end if;
  if p_email is null or p_email not like 'enc:v%' or char_length(p_email) > 1000 then
    raise exception 'bad address' using errcode = '22023';
  end if;
  if (select count(*) from public.launch_list) >= 50000 then
    raise exception 'list full' using errcode = '54000';
  end if;
  insert into public.launch_list (email_key, email, language)
  values (p_email_key, p_email, coalesce(nullif(substring(p_language from '^[A-Za-z_]{2,10}$'), ''), 'en'))
  on conflict (email_key) do nothing;
end;
$$;

revoke all on function public.join_launch_list(text, text, text) from public;
grant execute on function public.join_launch_list(text, text, text) to anon, authenticated;
