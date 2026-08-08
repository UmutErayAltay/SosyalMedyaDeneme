-- ============================================================
-- MESAJLAŞMA VİDEO — DM'e video gönderme desteği.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.messages add column if not exists video_url text;

NOTIFY pgrst, 'reload schema';
