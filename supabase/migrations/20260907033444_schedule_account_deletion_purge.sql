-- Split out of 20260907033439_account_deletion_requests.sql so each file
-- matches one entry in the live migration history (applied separately).

-- ---------------------------------------------------------------------
-- 3. The daily schedule
-- ---------------------------------------------------------------------

create extension if not exists pg_cron;

-- Unschedule first so re-running this migration doesn't create a second
-- copy of the same job.
select cron.unschedule('purge-expired-account-deletions')
where exists (
  select 1 from cron.job where jobname = 'purge-expired-account-deletions'
);

-- 03:15 UTC daily. The exact time doesn't matter much (a request is already
-- 30 days old by the time it qualifies, so being up to a day late is
-- immaterial), but off-peak keeps it out of the way of real traffic.
select cron.schedule(
  'purge-expired-account-deletions',
  '15 3 * * *',
  $$select public.purge_expired_account_deletions();$$
);
