-- ============================================================
-- VİDEO DESTEĞİ — postlara tek video ekleme (görsellerle birlikte değil).
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.posts add column if not exists video_url text;

NOTIFY pgrst, 'reload schema';
