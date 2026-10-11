-- Checks for the usage counts (migration *_usage_daily_counts.sql).
--
-- Run in the SQL editor after applying the migration. Every statement is
-- wrapped in one transaction that ROLLS BACK, so nothing here changes a
-- count. Each check raises an exception with a message if it fails; "all
-- usage checks passed" at the end means none did.
--
-- Note: a rollback does not undo sequence increments. These tables have no
-- sequences, so this run leaves nothing behind.

begin;

do $checks$
declare
  v_before bigint;
  v_after bigint;
  v_written integer;
  v_bad text;
begin
  -- 1. The table cannot hold an identifier, an address, a URL or free text:
  --    exactly these four columns, with these types.
  select string_agg(column_name || ':' || data_type, ',' order by ordinal_position) into v_bad
  from information_schema.columns where table_schema = 'public' and table_name = 'usage_daily';
  if v_bad is distinct from 'day:date,event:text,dim:text,n:bigint' then
    raise exception 'usage_daily has unexpected columns: %', v_bad;
  end if;

  -- 2. anon and authenticated can read or change neither table.
  select string_agg(grantee || ':' || privilege_type || ':' || table_name, ',') into v_bad
  from information_schema.role_table_grants
  where table_schema = 'public' and table_name in ('usage_daily', 'usage_allowed')
    and grantee in ('anon', 'authenticated', 'PUBLIC');
  if v_bad is not null then
    raise exception 'usage tables are readable or writable by app roles: %', v_bad;
  end if;
  if exists (select 1 from pg_class where relnamespace = 'public'::regnamespace
             and relname in ('usage_daily', 'usage_allowed') and not relrowsecurity) then
    raise exception 'row level security is off on a usage table';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename in ('usage_daily', 'usage_allowed')) then
    raise exception 'a usage table has a policy; it should have none';
  end if;

  -- 3. The function is SECURITY DEFINER with a pinned, empty search_path,
  --    and only anon/authenticated (not PUBLIC) may run it.
  if not exists (
    select 1 from pg_proc p where p.oid = 'public.record_usage(jsonb)'::regprocedure
      and p.prosecdef and p.proconfig @> array['search_path=""']
  ) then
    raise exception 'record_usage is not SECURITY DEFINER with an empty search_path';
  end if;
  if has_function_privilege('public', 'public.record_usage(jsonb)', 'execute') then
    raise exception 'record_usage is executable by PUBLIC';
  end if;
  if not has_function_privilege('anon', 'public.record_usage(jsonb)', 'execute')
     or not has_function_privilege('authenticated', 'public.record_usage(jsonb)', 'execute') then
    raise exception 'record_usage is not executable by anon and authenticated';
  end if;

  -- 4. Allowlisted rows are counted; everything else is ignored.
  select coalesce(sum(n), 0) into v_before from public.usage_daily;
  v_written := public.record_usage('[
    {"event": "page_view", "dim": "home", "n": 2},
    {"event": "law_view", "dim": "27-2029", "n": 1},
    {"event": "area_borough", "dim": "Bronx", "n": 1}
  ]'::jsonb);
  if v_written <> 3 then
    raise exception 'expected 3 allowlisted rows to be counted, got %', v_written;
  end if;

  v_written := public.record_usage('[
    {"event": "page_view", "dim": "231 Echo Place, Bronx", "n": 1},
    {"event": "page_view", "dim": "tenant@example.com", "n": 1},
    {"event": "area_search", "dim": "Echo Place", "n": 1},
    {"event": "building_lookup", "dim": "10457", "n": 1},
    {"event": "law_view", "dim": "27-2029; drop table x", "n": 1},
    {"event": "law_view", "dim": "apt 4B", "n": 1},
    {"event": "made_up_event", "dim": "home", "n": 1},
    {"event": "page_view", "dim": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "n": 1},
    {"event": "page_view", "dim": "home", "n": 0},
    {"event": "page_view", "dim": "home", "n": -5},
    {"event": "page_view", "dim": "home", "n": "3"},
    {"event": "page_view", "dim": 7, "n": 1},
    {"event": "page_view"},
    "not an object",
    null
  ]'::jsonb);
  if v_written <> 0 then
    raise exception 'record_usage counted % row(s) that are not on the allowlist', v_written;
  end if;

  if public.record_usage('{"event": "page_view", "dim": "home", "n": 1}'::jsonb) <> 0
     or public.record_usage(null) <> 0 then
    raise exception 'record_usage accepted something that is not an array';
  end if;

  -- 5. One call cannot add more than 1000 to a counter.
  select coalesce(sum(n), 0) into v_before from public.usage_daily;
  perform public.record_usage('[{"event": "page_view", "dim": "home", "n": 99999999999}]'::jsonb);
  select coalesce(sum(n), 0) into v_after from public.usage_daily;
  if v_after - v_before <> 1000 then
    raise exception 'one row added % to a counter; the cap is 1000', v_after - v_before;
  end if;

  -- 6. The day is the database's own.
  if exists (select 1 from public.usage_daily
             where day > (now() at time zone 'America/New_York')::date) then
    raise exception 'a usage row is dated in the future';
  end if;

  -- 7. Retention: the purge job exists, and its statement removes a row
  --    older than 12 months while keeping a recent one.
  if not exists (select 1 from cron.job where jobname = 'purge-usage-daily' and active) then
    raise exception 'the purge-usage-daily cron job is missing or inactive';
  end if;
  insert into public.usage_daily (day, event, dim, n)
  values (current_date - interval '13 months', 'page_view', 'home', 1),
         (current_date - interval '11 months', 'page_view', 'home', 1);
  execute (select command from cron.job where jobname = 'purge-usage-daily');
  if exists (select 1 from public.usage_daily where day < current_date - interval '12 months') then
    raise exception 'the purge statement left a row older than 12 months';
  end if;
  if not exists (select 1 from public.usage_daily where day = (current_date - interval '11 months')::date) then
    raise exception 'the purge statement removed a row inside the 12 months';
  end if;

  raise notice 'all usage checks passed';
end;
$checks$;

rollback;
