-- ============================================================
-- MESAJ DÜZENLEME — "düzenlendi" etiketi için edited_at kolonu.
-- migration_post_edit.sql ile aynı desen. Idempotent.
-- ============================================================

alter table public.messages add column if not exists edited_at timestamptz;

NOTIFY pgrst, 'reload schema';
