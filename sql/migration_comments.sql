-- ============================================================
-- YORUAN YANITLAMA + BEĞENME — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır.
-- Idempotent (tekrar çalıştırılabilir).
-- ============================================================

-- 1) comments tablosuna parent_comment_id ekle (yanıt hiyerarşisi)
alter table public.comments add column if not exists parent_comment_id uuid
    references public.comments(id) on delete cascade;

-- 2) comment_likes tablosu oluştur (yorum beğenme)
create table if not exists public.comment_likes (
    user_id    uuid not null references public.profiles(id) on delete cascade,
    comment_id uuid not null references public.comments(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, comment_id)
);

-- 3) Index
create index if not exists comment_likes_comment_id_idx on public.comment_likes (comment_id);
create index if not exists comments_parent_idx on public.comments (parent_comment_id);

-- 4) RLS
alter table public.comment_likes enable row level security;

drop policy if exists "comment_likes read"   on public.comment_likes;
drop policy if exists "comment_likes insert" on public.comment_likes;
drop policy if exists "comment_likes delete" on public.comment_likes;
create policy "comment_likes read"   on public.comment_likes for select using (true);
create policy "comment_likes insert" on public.comment_likes for insert with check (auth.uid() = user_id);
create policy "comment_likes delete" on public.comment_likes for delete using (auth.uid() = user_id);

-- 5) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
