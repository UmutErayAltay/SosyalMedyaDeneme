-- ============================================================
-- SOSYAL MEDYA — VERİTABANI ŞEMASI + RLS (idempotent)
-- Supabase Dashboard → SQL Editor'de çalıştır.
-- Güvenle tekrar tekrar çalıştırılabilir (hata vermez, veri silmez).
-- ============================================================

-- =========================================================
-- 1) TABLOLAR
-- =========================================================
create table if not exists public.profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    username    text unique not null,
    email       text not null,
    full_name   text,
    bio         text,
    avatar_url  text,
    created_at  timestamptz not null default now()
);

create table if not exists public.posts (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.profiles(id) on delete cascade,
    content     text not null,
    image_url   text,
    created_at  timestamptz not null default now()
);

create table if not exists public.likes (
    user_id     uuid not null references public.profiles(id) on delete cascade,
    post_id     uuid not null references public.posts(id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (user_id, post_id)
);

create table if not exists public.comments (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.profiles(id) on delete cascade,
    post_id     uuid not null references public.posts(id) on delete cascade,
    content     text not null,
    created_at  timestamptz not null default now()
);

create table if not exists public.follows (
    follower_id  uuid not null references public.profiles(id) on delete cascade,
    following_id uuid not null references public.profiles(id) on delete cascade,
    created_at   timestamptz not null default now(),
    primary key (follower_id, following_id),
    check (follower_id <> following_id)
);

create table if not exists public.conversations (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now()
);

create table if not exists public.conversation_participants (
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    user_id         uuid not null references public.profiles(id) on delete cascade,
    created_at      timestamptz not null default now(),
    primary key (conversation_id, user_id)
);

create table if not exists public.messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    sender_id       uuid not null references public.profiles(id) on delete cascade,
    content         text not null,
    created_at      timestamptz not null default now()
);


-- =========================================================
-- 2) ESKİ TABLO VARSA EKSİK KOLONLARI EKLE (idempotent)
--    CREATE TABLE IF NOT EXISTS, tablo zaten varsa atlanır;
--    bu blok eksik kolonların varlığını garanti eder.
-- =========================================================
alter table public.profiles add column if not exists username   text;
alter table public.profiles add column if not exists email      text;
alter table public.profiles add column if not exists full_name  text;
alter table public.profiles add column if not exists bio        text;
alter table public.profiles add column if not exists avatar_url text;
alter table public.profiles add column if not exists created_at timestamptz default now();

-- username için UNIQUE kısıtı (yoksa ekle)
do $$
begin
    if not exists (select 1 from information_schema.table_constraints
                   where table_schema = 'public'
                     and table_name = 'profiles'
                     and constraint_name = 'profiles_username_key') then
        alter table public.profiles add constraint profiles_username_key unique (username);
    end if;
end $$;


-- =========================================================
-- 3) INDEXLER
-- =========================================================
create index if not exists posts_created_at_idx on public.posts (created_at desc);
create index if not exists posts_user_id_idx    on public.posts (user_id);
create index if not exists comments_post_id_idx on public.comments (post_id);
create index if not exists messages_conv_idx    on public.messages (conversation_id, created_at);


-- =========================================================
-- 4) ROW LEVEL SECURITY
-- =========================================================
alter table public.profiles                  enable row level security;
alter table public.posts                     enable row level security;
alter table public.likes                     enable row level security;
alter table public.comments                  enable row level security;
alter table public.follows                   enable row level security;
alter table public.conversations             enable row level security;
alter table public.conversation_participants enable row level security;
alter table public.messages                  enable row level security;


-- =========================================================
-- 5) RLS POLİTİKALARI (idempotent: önce drop, sonra create)
-- =========================================================

-- PROFILES
drop policy if exists "profiles read"   on public.profiles;
drop policy if exists "profiles update" on public.profiles;
create policy "profiles read"   on public.profiles for select using (true);
create policy "profiles update" on public.profiles for update using (auth.uid() = id);

-- POSTS
drop policy if exists "posts read"   on public.posts;
drop policy if exists "posts insert" on public.posts;
drop policy if exists "posts delete" on public.posts;
drop policy if exists "posts update" on public.posts;
create policy "posts read"   on public.posts for select using (true);
create policy "posts insert" on public.posts for insert with check (auth.uid() = user_id);
create policy "posts delete" on public.posts for delete using (auth.uid() = user_id);
create policy "posts update" on public.posts for update using (auth.uid() = user_id);

-- LIKES
drop policy if exists "likes read"   on public.likes;
drop policy if exists "likes insert" on public.likes;
drop policy if exists "likes delete" on public.likes;
create policy "likes read"   on public.likes for select using (true);
create policy "likes insert" on public.likes for insert with check (auth.uid() = user_id);
create policy "likes delete" on public.likes for delete using (auth.uid() = user_id);

-- COMMENTS
drop policy if exists "comments read"   on public.comments;
drop policy if exists "comments insert" on public.comments;
drop policy if exists "comments delete" on public.comments;
create policy "comments read"   on public.comments for select using (true);
create policy "comments insert" on public.comments for insert with check (auth.uid() = user_id);
create policy "comments delete" on public.comments for delete using (auth.uid() = user_id);

-- FOLLOWS
drop policy if exists "follows read"   on public.follows;
drop policy if exists "follows insert" on public.follows;
drop policy if exists "follows delete" on public.follows;
create policy "follows read"   on public.follows for select using (true);
create policy "follows insert" on public.follows for insert with check (auth.uid() = follower_id);
create policy "follows delete" on public.follows for delete using (auth.uid() = follower_id);

-- CONVERSATIONS
drop policy if exists "conv read" on public.conversations;
drop policy if exists "conv ins"  on public.conversations;
create policy "conv read" on public.conversations for select
    using (exists (
        select 1 from public.conversation_participants cp
        where cp.conversation_id = id and cp.user_id = auth.uid()
    ));
create policy "conv ins" on public.conversations for insert with check (true);

-- CONVERSATION PARTICIPANTS
drop policy if exists "cp read" on public.conversation_participants;
drop policy if exists "cp ins"  on public.conversation_participants;
drop policy if exists "cp del"  on public.conversation_participants;
create policy "cp read" on public.conversation_participants for select using (true);
create policy "cp ins"  on public.conversation_participants for insert with check (true);
create policy "cp del"  on public.conversation_participants for delete using (auth.uid() = user_id);

-- MESSAGES
drop policy if exists "msg read"   on public.messages;
drop policy if exists "msg insert" on public.messages;
create policy "msg read" on public.messages for select
    using (exists (
        select 1 from public.conversation_participants cp
        where cp.conversation_id = messages.conversation_id and cp.user_id = auth.uid()
    ));
create policy "msg insert" on public.messages for insert
    with check (
        sender_id = auth.uid()
        and exists (
            select 1 from public.conversation_participants cp
            where cp.conversation_id = messages.conversation_id and cp.user_id = auth.uid()
        )
    );


-- =========================================================
-- 6) YENİ KULLANICI İÇİN PROFİL OLUŞTURMA TRIGGER'I
-- =========================================================
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
    insert into public.profiles (id, username, email)
    values (
        new.id,
        coalesce(new.raw_user_meta_data->>'username', split_part(new.email, '@', 1)),
        new.email
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();


-- =========================================================
-- 7) POSTGREST SCHEMA CACHE'İ YENİLE
--    (Hata mesajındaki "schema cache" problemini çözer)
-- =========================================================
NOTIFY pgrst, 'reload schema';
