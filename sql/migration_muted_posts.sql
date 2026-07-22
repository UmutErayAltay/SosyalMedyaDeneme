create table if not exists public.muted_posts (
    user_id uuid not null references auth.users(id) on delete cascade,
    post_id uuid not null references public.posts(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, post_id)
);

alter table public.muted_posts enable row level security;

drop policy if exists "muted_posts: kendi mute'larini gorur" on public.muted_posts;
create policy "muted_posts: kendi mute'larini gorur" on public.muted_posts
    for select using (auth.uid() = user_id);

drop policy if exists "muted_posts: kendi mute'unu ekler" on public.muted_posts;
create policy "muted_posts: kendi mute'unu ekler" on public.muted_posts
    for insert with check (auth.uid() = user_id);

drop policy if exists "muted_posts: kendi mute'unu siler" on public.muted_posts;
create policy "muted_posts: kendi mute'unu siler" on public.muted_posts
    for delete using (auth.uid() = user_id);

NOTIFY pgrst, 'reload schema';
