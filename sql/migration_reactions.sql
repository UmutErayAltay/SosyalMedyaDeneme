-- ============================================================
-- EMOJI REAKSİYONLAR — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- likes tablosuna reaction_type ekle (mevcut satırlar 'like' = 👍 olur)
alter table public.likes add column if not exists reaction_type text not null default 'like';

do $$
begin
    if not exists (
        select 1 from information_schema.constraint_column_usage
        where table_name = 'likes' and constraint_name = 'likes_reaction_type_check'
    ) then
        alter table public.likes add constraint likes_reaction_type_check
            check (reaction_type in ('like', 'love', 'haha', 'wow', 'sad'));
    end if;
end $$;

NOTIFY pgrst, 'reload schema';
