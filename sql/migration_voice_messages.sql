-- ============================================================
-- SESLİ MESAJ — DM'e ses kaydı ekleme.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.messages add column if not exists audio_url text;

NOTIFY pgrst, 'reload schema';
