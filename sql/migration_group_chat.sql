-- ============================================================
-- GRUP SOHBETİ — conversation_participants zaten çok-katılımcılı (many-to-many)
-- bir join tablosu, şema değişikliği gerekmiyor. Sadece conversations tablosuna
-- grup meta bilgisi (isim + grup mu/1:1 mi) ekleniyor.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.conversations add column if not exists is_group boolean not null default false;
alter table public.conversations add column if not exists name text;
alter table public.conversations add column if not exists created_by uuid references public.profiles(id) on delete set null;

NOTIFY pgrst, 'reload schema';
