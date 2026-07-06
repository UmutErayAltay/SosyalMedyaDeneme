-- ============================================================
-- HİKAYE (Stories) — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- expires_at: default now()+24 saat. Süresi dolan satırlar gerçek bir
-- cron/scheduler OLMADAN, opportunistic olarak silinir (bkz. app/stories.py
-- _cleanup_expired_stories — notifications.py'nin RETENTION_DAYS deseniyle
-- aynı: feed her ziyaret edildiğinde fırsatçı temizlik yeterli, bu ölçekte).
create table if not exists public.stories (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references public.profiles(id) on delete cascade,
    image_url  text,
    video_url  text,
    caption    text,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null default (now() + interval '24 hours')
);

create index if not exists stories_user_idx on public.stories (user_id, created_at desc);
create index if not exists stories_expires_idx on public.stories (expires_at);

-- Kimin hangi hikayeyi gördüğü — "görüldü/görülmedi" halka rengi için.
-- Kasıtlı olarak dar kapsam: "hikayeni kim gördü" listesi YOK, sadece
-- kendi görme durumunu okuyabilir (RLS: auth.uid() = user_id).
create table if not exists public.story_views (
    story_id  uuid not null references public.stories(id) on delete cascade,
    user_id   uuid not null references public.profiles(id) on delete cascade,
    viewed_at timestamptz not null default now(),
    primary key (story_id, user_id)
);

alter table public.stories enable row level security;
alter table public.story_views enable row level security;

drop policy if exists "stories read"   on public.stories;
drop policy if exists "stories insert" on public.stories;
drop policy if exists "stories delete" on public.stories;
create policy "stories read"   on public.stories for select using (true);
create policy "stories insert" on public.stories for insert with check (auth.uid() = user_id);
create policy "stories delete" on public.stories for delete using (auth.uid() = user_id);

drop policy if exists "story_views read"   on public.story_views;
drop policy if exists "story_views insert" on public.story_views;
create policy "story_views read"   on public.story_views for select using (auth.uid() = user_id);
create policy "story_views insert" on public.story_views for insert with check (auth.uid() = user_id);

NOTIFY pgrst, 'reload schema';
