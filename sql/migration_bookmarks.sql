-- ============================================================
-- KAYDEDİLENLER (bookmarks) — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.bookmarks (
    user_id    uuid not null references public.profiles(id) on delete cascade,
    post_id    uuid not null references public.posts(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, post_id)
);

create index if not exists bookmarks_user_id_idx on public.bookmarks (user_id, created_at desc);

alter table public.bookmarks enable row level security;

-- Kaydedilenler kişiseldir: sadece sahibi görebilir/ekleyebilir/silebilir
-- (likes/follows gibi herkese açık DEĞİL — "okuma listesi" mahremiyeti).
drop policy if exists "bookmarks read"   on public.bookmarks;
drop policy if exists "bookmarks insert" on public.bookmarks;
drop policy if exists "bookmarks delete" on public.bookmarks;
create policy "bookmarks read"   on public.bookmarks for select using (auth.uid() = user_id);
create policy "bookmarks insert" on public.bookmarks for insert with check (auth.uid() = user_id);
create policy "bookmarks delete" on public.bookmarks for delete using (auth.uid() = user_id);

NOTIFY pgrst, 'reload schema';
