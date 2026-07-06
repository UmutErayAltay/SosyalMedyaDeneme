-- Yakın arkadaşlar özelliği: tablo, indeks, RLS politikaları ve posts.visibility kısıtı güncelle
create table if not exists public.close_friends (
  owner_id   uuid not null references public.profiles(id) on delete cascade,
  friend_id  uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (owner_id, friend_id)
);

create index if not exists close_friends_friend_idx on public.close_friends (friend_id);

-- posts.visibility kısıtını 3 değerli versiyona güncelle
alter table public.posts drop constraint if exists posts_visibility_check;
alter table public.posts add constraint posts_visibility_check
    check (visibility in ('public', 'followers', 'close_friends'));

-- RLS politikaları
alter table public.close_friends enable row level security;
drop policy if exists "close_friends read" on public.close_friends;
drop policy if exists "close_friends insert" on public.close_friends;
drop policy if exists "close_friends delete" on public.close_friends;

create policy "close_friends read" on public.close_friends for select using (auth.uid() = owner_id);
create policy "close_friends insert" on public.close_friends for insert with check (auth.uid() = owner_id);
create policy "close_friends delete" on public.close_friends for delete using (auth.uid() = owner_id);
