-- ============================================================
-- HASHTAG TAKİP — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.hashtag_follows (
    user_id    uuid not null references public.profiles(id) on delete cascade,
    hashtag_id uuid not null references public.hashtags(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (user_id, hashtag_id)
);

create index if not exists hashtag_follows_hashtag_idx on public.hashtag_follows (hashtag_id);

alter table public.hashtag_follows enable row level security;

-- follows tablosuyla aynı görünürlük: kim neyi takip ediyor herkese açık
-- (bookmarks gibi mahrem değil), sadece kendi takibini ekleyip/çıkarabilir.
drop policy if exists "hashtag_follows read"   on public.hashtag_follows;
drop policy if exists "hashtag_follows insert" on public.hashtag_follows;
drop policy if exists "hashtag_follows delete" on public.hashtag_follows;
create policy "hashtag_follows read"   on public.hashtag_follows for select using (true);
create policy "hashtag_follows insert" on public.hashtag_follows for insert with check (auth.uid() = user_id);
create policy "hashtag_follows delete" on public.hashtag_follows for delete using (auth.uid() = user_id);

-- notifications: yeni bildirim türü "hashtag_post" + hangi etiket olduğunu
-- taşımak için hashtag_id kolonu (mevcut post_id/comment_id/conversation_id
-- ile aynı desen — nullable, sadece ilgili türde doldurulur).
alter table public.notifications add column if not exists hashtag_id uuid references public.hashtags(id) on delete cascade;

alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
    check (type = any (array['like','comment','reply','comment_like','follow','message','mention','hashtag_post']));

NOTIFY pgrst, 'reload schema';
