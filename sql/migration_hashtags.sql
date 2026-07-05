-- ============================================================
-- HASHTAG SİSTEMİ — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.hashtags (
    id         uuid primary key default gen_random_uuid(),
    tag        text not null unique,  -- küçük harf, # işareti olmadan
    created_at timestamptz not null default now()
);

create table if not exists public.post_hashtags (
    post_id    uuid not null references public.posts(id) on delete cascade,
    hashtag_id uuid not null references public.hashtags(id) on delete cascade,
    primary key (post_id, hashtag_id)
);

create index if not exists post_hashtags_hashtag_idx on public.post_hashtags (hashtag_id);

alter table public.hashtags enable row level security;
alter table public.post_hashtags enable row level security;

drop policy if exists "hashtags read"   on public.hashtags;
drop policy if exists "hashtags insert" on public.hashtags;
create policy "hashtags read"   on public.hashtags for select using (true);
create policy "hashtags insert" on public.hashtags for insert with check (true);

drop policy if exists "post_hashtags read"   on public.post_hashtags;
drop policy if exists "post_hashtags insert" on public.post_hashtags;
drop policy if exists "post_hashtags delete" on public.post_hashtags;
create policy "post_hashtags read"   on public.post_hashtags for select using (true);
create policy "post_hashtags insert" on public.post_hashtags for insert with check (true);
create policy "post_hashtags delete" on public.post_hashtags for delete using (
    exists (
        select 1 from public.posts
        where posts.id = post_hashtags.post_id and posts.user_id = auth.uid()
    )
);

NOTIFY pgrst, 'reload schema';
