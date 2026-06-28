-- ============================================================
-- TAM SIFIRLAMA: tüm tabloları sil + şemayı sıfırdan kur
-- Supabase Dashboard → SQL Editor → BİR KEZ çalıştır.
--
-- UYARI: TÜM VERİ SİLİNİR. (Kayıt bile yapamadığınız için kayıp yok.)
-- Bu betik çalıştıktan sonra .env'nizdeki anahtarlar AYNI kalır,
-- yani projenizi yeniden kurmanıza GEREK YOK.
-- ============================================================

-- ---------- 1) TRIGGER VE FONKSİYONU KALDIR ----------
drop trigger if exists on_auth_user_created on auth.users;
drop function if exists public.handle_new_user() cascade;


-- ---------- 2) TABLOLARI CASCADE İLE KALDIR ----------
-- (bağımlılıklar dahil hepsi gider; RLS politikaları da otomatik düşer)
drop table if exists public.messages                  cascade;
drop table if exists public.conversation_participants cascade;
drop table if exists public.conversations             cascade;
drop table if exists public.follows                   cascade;
drop table if exists public.comments                  cascade;
drop table if exists public.likes                     cascade;
drop table if exists public.posts                     cascade;
drop table if exists public.profiles                  cascade;


-- ---------- 3) ŞEMAYI SIFIRDAN OLUŞTUR ----------
create table public.profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    username    text unique not null,
    email       text not null,
    full_name   text,
    bio         text,
    avatar_url  text,
    created_at  timestamptz not null default now()
);

create table public.posts (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.profiles(id) on delete cascade,
    content     text not null,
    image_url   text,
    created_at  timestamptz not null default now()
);

create table public.likes (
    user_id     uuid not null references public.profiles(id) on delete cascade,
    post_id     uuid not null references public.posts(id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (user_id, post_id)
);

create table public.comments (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.profiles(id) on delete cascade,
    post_id     uuid not null references public.posts(id) on delete cascade,
    content     text not null,
    created_at  timestamptz not null default now()
);

create table public.follows (
    follower_id  uuid not null references public.profiles(id) on delete cascade,
    following_id uuid not null references public.profiles(id) on delete cascade,
    created_at   timestamptz not null default now(),
    primary key (follower_id, following_id),
    check (follower_id <> following_id)
);

create table public.conversations (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now()
);

create table public.conversation_participants (
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    user_id         uuid not null references public.profiles(id) on delete cascade,
    created_at      timestamptz not null default now(),
    primary key (conversation_id, user_id)
);

create table public.messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    sender_id       uuid not null references public.profiles(id) on delete cascade,
    content         text not null,
    created_at      timestamptz not null default now()
);


-- ---------- 4) INDEXLER ----------
create index posts_created_at_idx on public.posts (created_at desc);
create index posts_user_id_idx    on public.posts (user_id);
create index comments_post_id_idx on public.comments (post_id);
create index messages_conv_idx    on public.messages (conversation_id, created_at);


-- ---------- 5) ROW LEVEL SECURITY ----------
alter table public.profiles                  enable row level security;
alter table public.posts                     enable row level security;
alter table public.likes                     enable row level security;
alter table public.comments                  enable row level security;
alter table public.follows                   enable row level security;
alter table public.conversations             enable row level security;
alter table public.conversation_participants enable row level security;
alter table public.messages                  enable row level security;


-- ---------- 6) RLS POLİTİKALARI ----------
-- PROFILES
create policy "profiles read"   on public.profiles for select using (true);
create policy "profiles update" on public.profiles for update using (auth.uid() = id);

-- POSTS
create policy "posts read"   on public.posts for select using (true);
create policy "posts insert" on public.posts for insert with check (auth.uid() = user_id);
create policy "posts delete" on public.posts for delete using (auth.uid() = user_id);
create policy "posts update" on public.posts for update using (auth.uid() = user_id);

-- LIKES
create policy "likes read"   on public.likes for select using (true);
create policy "likes insert" on public.likes for insert with check (auth.uid() = user_id);
create policy "likes delete" on public.likes for delete using (auth.uid() = user_id);

-- COMMENTS
create policy "comments read"   on public.comments for select using (true);
create policy "comments insert" on public.comments for insert with check (auth.uid() = user_id);
create policy "comments delete" on public.comments for delete using (auth.uid() = user_id);

-- FOLLOWS
create policy "follows read"   on public.follows for select using (true);
create policy "follows insert" on public.follows for insert with check (auth.uid() = follower_id);
create policy "follows delete" on public.follows for delete using (auth.uid() = follower_id);

-- CONVERSATIONS
create policy "conv read" on public.conversations for select
    using (exists (
        select 1 from public.conversation_participants cp
        where cp.conversation_id = id and cp.user_id = auth.uid()
    ));
create policy "conv ins" on public.conversations for insert with check (true);

-- CONVERSATION PARTICIPANTS
create policy "cp read" on public.conversation_participants for select using (true);
create policy "cp ins"  on public.conversation_participants for insert with check (true);
create policy "cp del"  on public.conversation_participants for delete using (auth.uid() = user_id);

-- MESSAGES
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


-- ---------- 7) YENİ KULLANICI İÇİN PROFİL OLUŞTURMA TRIGGER'I ----------
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

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();


-- ---------- 8) POSTGREST SCHEMA CACHE'İ YENİLE ----------
NOTIFY pgrst, 'reload schema';
