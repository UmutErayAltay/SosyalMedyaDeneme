-- ============================================================
-- MESAJLAŞMA GÖRSEL + REALTIME — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- 1) messages tablosuna image_url ekle (görsel mesajları)
alter table public.messages add column if not exists image_url text;

-- 2) Realtime: messages tablosunu realtime publication'a ekle
-- (Supabase Dashboard'da veya SQL ile yapılabilir)
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'messages'
    ) then
        alter publication supabase_realtime add table public.messages;
    end if;
end $$;

-- 3) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
