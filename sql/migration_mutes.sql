-- Sessize alma (mute) özelliği: takip etmeye devam ederken feed'den gizle
create table if not exists public.muted_users (
    muter_id uuid not null references auth.users(id) on delete cascade,
    muted_id uuid not null references auth.users(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (muter_id, muted_id)
);

alter table public.muted_users enable row level security;

drop policy if exists "muted_users: kendi mute'larini gorur" on public.muted_users;
create policy "muted_users: kendi mute'larini gorur" on public.muted_users
    for select using (auth.uid() = muter_id);

drop policy if exists "muted_users: kendi mute'unu ekler" on public.muted_users;
create policy "muted_users: kendi mute'unu ekler" on public.muted_users
    for insert with check (auth.uid() = muter_id);

drop policy if exists "muted_users: kendi mute'unu siler" on public.muted_users;
create policy "muted_users: kendi mute'unu siler" on public.muted_users
    for delete using (auth.uid() = muter_id);

NOTIFY pgrst, 'reload schema';
