-- ============================================================
-- BİLDİRİMLER (beğeni, yorum, yanıt, takip, mesaj) — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- 1) notifications tablosu
create table if not exists public.notifications (
    id              uuid primary key default gen_random_uuid(),
    recipient_id    uuid not null references public.profiles(id) on delete cascade,
    actor_id        uuid not null references public.profiles(id) on delete cascade,
    type            text not null check (type in ('like','comment','reply','comment_like','follow','message')),
    post_id         uuid references public.posts(id) on delete cascade,
    comment_id      uuid references public.comments(id) on delete cascade,
    conversation_id uuid references public.conversations(id) on delete cascade,
    is_read         boolean not null default false,
    created_at      timestamptz not null default now()
);

-- 2) Index (liste sıralaması + okunmamış sayaç sorgusu için)
create index if not exists notifications_recipient_created_idx
    on public.notifications (recipient_id, created_at desc);
create index if not exists notifications_recipient_unread_idx
    on public.notifications (recipient_id, is_read);

-- 3) RLS
alter table public.notifications enable row level security;

drop policy if exists "notifications read"   on public.notifications;
drop policy if exists "notifications insert" on public.notifications;
drop policy if exists "notifications update" on public.notifications;
drop policy if exists "notifications delete" on public.notifications;

create policy "notifications read"   on public.notifications for select
    using (recipient_id = auth.uid());
create policy "notifications insert" on public.notifications for insert
    with check (actor_id = auth.uid());
create policy "notifications update" on public.notifications for update
    using (recipient_id = auth.uid());
create policy "notifications delete" on public.notifications for delete
    using (recipient_id = auth.uid());

-- 4) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
