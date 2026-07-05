-- ============================================================
-- ANKET (POLL) POSTU — bir post EN FAZLA bir anketle ilişkilendirilir,
-- postun content'i anketin sorusu olarak kullanılır (ayrı bir "question"
-- kolonu gerekmez). Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.polls (
    id         uuid primary key default gen_random_uuid(),
    post_id    uuid not null unique references public.posts(id) on delete cascade,
    created_at timestamptz not null default now()
);

create table if not exists public.poll_options (
    id          uuid primary key default gen_random_uuid(),
    poll_id     uuid not null references public.polls(id) on delete cascade,
    option_text text not null,
    position    int not null default 0
);

create table if not exists public.poll_votes (
    poll_id    uuid not null references public.polls(id) on delete cascade,
    option_id  uuid not null references public.poll_options(id) on delete cascade,
    user_id    uuid not null references public.profiles(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (poll_id, user_id)  -- kullanıcı başına ankette TEK oy
);

create index if not exists poll_options_poll_idx on public.poll_options (poll_id);
create index if not exists poll_votes_poll_idx    on public.poll_votes (poll_id);

alter table public.polls        enable row level security;
alter table public.poll_options enable row level security;
alter table public.poll_votes   enable row level security;

drop policy if exists "polls read"   on public.polls;
drop policy if exists "polls insert" on public.polls;
create policy "polls read"   on public.polls for select using (true);
create policy "polls insert" on public.polls for insert with check (true);

drop policy if exists "poll_options read"   on public.poll_options;
drop policy if exists "poll_options insert" on public.poll_options;
create policy "poll_options read"   on public.poll_options for select using (true);
create policy "poll_options insert" on public.poll_options for insert with check (true);

drop policy if exists "poll_votes read"   on public.poll_votes;
drop policy if exists "poll_votes insert" on public.poll_votes;
drop policy if exists "poll_votes update" on public.poll_votes;
drop policy if exists "poll_votes delete" on public.poll_votes;
create policy "poll_votes read"   on public.poll_votes for select using (true);
create policy "poll_votes insert" on public.poll_votes for insert with check (auth.uid() = user_id);
create policy "poll_votes update" on public.poll_votes for update using (auth.uid() = user_id);
create policy "poll_votes delete" on public.poll_votes for delete using (auth.uid() = user_id);

NOTIFY pgrst, 'reload schema';
