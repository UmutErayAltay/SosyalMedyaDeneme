-- Post görüntülenme sayacı tablosu (Instagram tarzı "views")
-- Story_views deseniyle aynı: post_id + user_id primary key,
-- tek satır per kişi (tekrar görüntüleme upsert ile günceller, duplicate sayılmaz)

create table if not exists public.post_views (
  post_id   uuid not null references public.posts(id) on delete cascade,
  user_id   uuid not null references public.profiles(id) on delete cascade,
  viewed_at timestamptz not null default now(),
  primary key (post_id, user_id)
);

create index if not exists post_views_post_idx on public.post_views (post_id);

alter table public.post_views enable row level security;

drop policy if exists "post_views read" on public.post_views;
drop policy if exists "post_views insert" on public.post_views;

create policy "post_views read"   on public.post_views for select using (auth.uid() = user_id);
create policy "post_views insert" on public.post_views for insert with check (auth.uid() = user_id);

notify pgrst, 'reload schema';
