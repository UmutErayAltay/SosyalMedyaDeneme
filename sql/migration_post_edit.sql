-- ============================================================
-- POST DÜZENLEME — "düzenlendi" etiketi için edited_at kolonu.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.posts add column if not exists edited_at timestamptz;

NOTIFY pgrst, 'reload schema';
