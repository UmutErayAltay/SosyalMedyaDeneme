-- ============================================================
-- TASLAK (DRAFT) POST — yayınlanmadan kaydetme.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.posts add column if not exists is_draft boolean not null default false;

create index if not exists posts_is_draft_idx on public.posts (is_draft);

NOTIFY pgrst, 'reload schema';
