-- Daily usage counts: (day, event, one value from a fixed list) -> how many.
--
-- WHY (owner, 2026-10-11): to see what tenants use and where they get stuck
-- without recording anything about any one of them. usage_metrics.py and
-- docs/usage-counts.md describe the design; this file is the database half.
--
-- WHAT THE TABLE CAN HOLD: a date, two short names and a number. There is
-- no column for a user, a session, an IP address, a user agent, a URL or
-- free text, and the only way to write a row is record_usage() below,
-- which accepts a (event, dim) pair only if it is in usage_allowed (or, for
-- law_view, is shaped like a section number of public law).
--
-- WHO CAN DO WHAT
--   anon / authenticated: execute record_usage(). Nothing else: no select,
--     insert, update or delete on either table. The app has no service-role
--     key, so it cannot read the counts back either; they are read from the
--     SQL editor with supabase/checks/20261011_usage_counts_report.sql.
--   Anyone holding the public API key can therefore ADD to a counter from
--     the fixed list (at most 200 rows of at most 1000 per call). They
--     cannot write anything else or read anything. Counts are an
--     indication, not an audit record.
--
-- RETENTION: a daily pg_cron job deletes rows older than 12 months.
--
-- NOT YET APPLIED when this file was written. After applying it, rename the
-- file to the version Supabase records (tests/test_migration_names.py).

create table if not exists public.usage_daily (
  day date not null,
  event text not null check (event ~ '^[a-z0-9_]{1,40}$'),
  dim text not null check (char_length(dim) <= 40),
  n bigint not null default 0 check (n >= 0),
  primary key (day, event, dim)
);

create table if not exists public.usage_allowed (
  event text not null check (event ~ '^[a-z0-9_]{1,40}$'),
  dim text not null check (char_length(dim) <= 40),
  primary key (event, dim)
);

comment on table public.usage_daily is
  'Aggregate daily usage counts. No identifiers, no free text. Written only by record_usage(); purged after 12 months.';
comment on table public.usage_allowed is
  'The only (event, dim) pairs record_usage() will count. Mirrors usage_metrics.EVENTS.';

alter table public.usage_daily enable row level security;
alter table public.usage_allowed enable row level security;

-- RLS with no policies already denies everything to these roles; the
-- revokes make that true at the privilege level too.
revoke all on table public.usage_daily from public, anon, authenticated;
revoke all on table public.usage_allowed from public, anon, authenticated;

-- The allowlist. Replaced as a whole so a value removed from the app stops
-- being accepted.
delete from public.usage_allowed;
insert into public.usage_allowed (event, dim) values
  ('account_complete', 'saved'),
  ('account_complete', 'skipped'),
  ('ai_error', 'failed'),
  ('ai_error', 'unavailable'),
  ('area_borough', 'Bronx'),
  ('area_borough', 'Brooklyn'),
  ('area_borough', 'Manhattan'),
  ('area_borough', 'Queens'),
  ('area_borough', 'Staten Island'),
  ('area_cross_used', 'no'),
  ('area_cross_used', 'yes'),
  ('area_search', 'error'),
  ('area_search', 'found'),
  ('area_search', 'invalid'),
  ('area_search', 'no_match'),
  ('area_zip_used', 'no'),
  ('area_zip_used', 'yes'),
  ('building_lookup', 'error'),
  ('building_lookup', 'found'),
  ('building_lookup', 'invalid'),
  ('building_lookup', 'not_found'),
  ('chat', 'conversation_archived'),
  ('chat', 'conversation_created'),
  ('chat', 'conversation_deleted'),
  ('chat', 'message_sent'),
  ('chat', 'reply_with_citations'),
  ('chip_click', 'continue_building'),
  ('chip_click', 'deposit'),
  ('chip_click', 'eviction'),
  ('chip_click', 'free_help'),
  ('chip_click', 'look_up_street'),
  ('chip_click', 'repairs'),
  ('error_route', 'account_complete'),
  ('error_route', 'area'),
  ('error_route', 'building'),
  ('error_route', 'chat'),
  ('error_route', 'forgot_password'),
  ('error_route', 'form_review'),
  ('error_route', 'home'),
  ('error_route', 'law'),
  ('error_route', 'law_index'),
  ('error_route', 'learn_more'),
  ('error_route', 'login'),
  ('error_route', 'other'),
  ('error_route', 'privacy'),
  ('error_route', 'reset_password'),
  ('error_route', 'resources'),
  ('error_route', 'settings'),
  ('error_route', 'signup'),
  ('error_status', '400'),
  ('error_status', '401'),
  ('error_status', '403'),
  ('error_status', '404'),
  ('error_status', '405'),
  ('error_status', '413'),
  ('error_status', '429'),
  ('error_status', '500'),
  ('error_status', '502'),
  ('error_status', '503'),
  ('error_status', 'other'),
  ('guard_violation', 'citation_without_quote'),
  ('guard_violation', 'number_not_in_source'),
  ('guard_violation', 'quote_not_in_source'),
  ('guard_violation', 'ungrounded_statute'),
  ('guard_violation', 'unknown_marker'),
  ('login', 'failure'),
  ('page_view', 'account_complete'),
  ('page_view', 'area'),
  ('page_view', 'building'),
  ('page_view', 'chat'),
  ('page_view', 'forgot_password'),
  ('page_view', 'form_review'),
  ('page_view', 'home'),
  ('page_view', 'law'),
  ('page_view', 'law_index'),
  ('page_view', 'learn_more'),
  ('page_view', 'login'),
  ('page_view', 'privacy'),
  ('page_view', 'reset_password'),
  ('page_view', 'resources'),
  ('page_view', 'settings'),
  ('page_view', 'signup'),
  ('ra81', 'downloaded'),
  ('ra81', 'draft_started'),
  ('ra81', 'reviewed'),
  ('resource_click', 'broker_fee_you_shouldn_t_have_paid'),
  ('resource_click', 'bronx_housing_court'),
  ('resource_click', 'brooklyn_housing_court'),
  ('resource_click', 'housing_court_answers'),
  ('resource_click', 'hpd_complaints'),
  ('resource_click', 'manhattan_housing_court'),
  ('resource_click', 'met_council_on_housing_hotline'),
  ('resource_click', 'ny_state_rent_office_hcr'),
  ('resource_click', 'nyc_right_to_counsel'),
  ('resource_click', 'nyc_tenant_helpline'),
  ('resource_click', 'queens_housing_court'),
  ('resource_click', 'red_hook_community_justice_center'),
  ('resource_click', 'rent_stabilized_apartment_questions_ny_s'),
  ('resource_click', 'report_a_repair_or_heat_problem_311'),
  ('resource_click', 'staten_island_housing_court'),
  ('signup', 'success'),
  ('signup', 'view'),
  ('view_device', 'desktop'),
  ('view_device', 'mobile'),
  ('view_language', 'ar'),
  ('view_language', 'bn'),
  ('view_language', 'en'),
  ('view_language', 'es'),
  ('view_language', 'fr'),
  ('view_language', 'ht'),
  ('view_language', 'ko'),
  ('view_language', 'other'),
  ('view_language', 'ru'),
  ('view_language', 'zh'),
  ('view_referrer', 'direct'),
  ('view_referrer', 'internal'),
  ('view_referrer', 'other'),
  ('view_referrer', 'search'),
  ('view_referrer', 'social'),
  ('view_signed_in', 'no'),
  ('view_signed_in', 'yes');

create or replace function public.record_usage(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row jsonb;
  v_event text;
  v_dim text;
  v_amount numeric;
  v_written integer := 0;
  -- The day is the database's, in New York time. A caller cannot choose
  -- it, so nothing can be written into the past or the future.
  v_day date := (now() at time zone 'America/New_York')::date;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) > 200 then
    return 0;
  end if;

  for v_row in select value from jsonb_array_elements(p_rows) loop
    if jsonb_typeof(v_row) <> 'object'
       or jsonb_typeof(v_row -> 'event') <> 'string'
       or jsonb_typeof(v_row -> 'dim') <> 'string'
       or jsonb_typeof(v_row -> 'n') <> 'number' then
      continue;
    end if;

    v_event := v_row ->> 'event';
    v_dim := v_row ->> 'dim';
    v_amount := floor((v_row ->> 'n')::numeric);

    if char_length(v_event) > 40 or char_length(v_dim) > 40 or v_amount < 1 then
      continue;
    end if;

    if not (
      exists (select 1 from public.usage_allowed a where a.event = v_event and a.dim = v_dim)
      or (v_event = 'law_view'
          and v_dim ~ '^[0-9]{1,3}(-[0-9]{1,5}[a-z]?(\.[0-9]{1,3}){0,2}|-[a-z]{1,2})?$')
    ) then
      continue;
    end if;

    insert into public.usage_daily as u (day, event, dim, n)
    values (v_day, v_event, v_dim, least(v_amount, 1000)::bigint)
    on conflict (day, event, dim) do update set n = u.n + excluded.n;

    v_written := v_written + 1;
  end loop;

  return v_written;
end;
$$;

comment on function public.record_usage(jsonb) is
  'Adds to daily usage counters. Accepts only allowlisted (event, dim) pairs; ignores everything else. Returns the number of rows counted.';

revoke all on function public.record_usage(jsonb) from public;
grant execute on function public.record_usage(jsonb) to anon, authenticated;

-- Retention: 12 months, daily.
create extension if not exists pg_cron;

select cron.unschedule('purge-usage-daily')
where exists (select 1 from cron.job where jobname = 'purge-usage-daily');

select cron.schedule(
  'purge-usage-daily',
  '25 3 * * *',
  $cron$delete from public.usage_daily where day < (current_date - interval '12 months');$cron$
);
