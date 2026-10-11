-- Reading the usage counts (run in the SQL editor; the app cannot read them).
--
-- Every query hides any cell with a total under 5: a count of 1 or 2 on a
-- rare value is the kind of number that can point at a person. Keep the
-- "having sum(n) >= 5" when adapting these. Never join or cross-tabulate two
-- dimensions; the table deliberately holds one dimension per counter.

-- 1. Last 30 days, one row per event and value.
select event, dim, sum(n) as total
from public.usage_daily
where day >= current_date - 30
group by event, dim
having sum(n) >= 5
order by event, total desc;

-- 2. One event by week, to compare before and after an update.
--    Change 'area_search' to the event you want.
select date_trunc('week', day)::date as week, dim, sum(n) as total
from public.usage_daily
where event = 'area_search' and day >= current_date - 120
group by 1, dim
having sum(n) >= 5
order by week, dim;

-- 3. Where people stop: daily totals for the steps of one path. Read as
--    "how many reached each step that day", never as one person's path.
with steps as (
  select day,
         sum(n) filter (where event = 'area_search' and dim = 'found') as street_found,
         sum(n) filter (where event = 'page_view' and dim = 'building') as building_pages,
         sum(n) filter (where event = 'signup' and dim = 'view') as signup_views,
         sum(n) filter (where event = 'signup' and dim = 'success') as signups,
         sum(n) filter (where event = 'chat' and dim = 'message_sent') as chat_messages
  from public.usage_daily
  where day >= current_date - 30
  group by day
)
select day,
       case when street_found >= 5 then street_found end as street_found,
       case when building_pages >= 5 then building_pages end as building_pages,
       case when signup_views >= 5 then signup_views end as signup_views,
       case when signups >= 5 then signups end as signups,
       case when chat_messages >= 5 then chat_messages end as chat_messages
from steps
order by day;
