-- Recorded from the live migration history (applied 2026-07-26, before
-- migrations were kept in this repo): removes the starter-template tables.
DROP TABLE IF EXISTS public.items;
DROP TABLE IF EXISTS public.profiles;
DROP TABLE IF EXISTS public.test_connection;
