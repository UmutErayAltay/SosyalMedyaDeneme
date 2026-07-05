-- ============================================================
-- KULLANICI ENGELLEME — Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- Engelleme İKİ YÖNLÜ etki yapar: taraflardan biri diğerini görmez/takip
-- edemez/mesaj atamaz (Instagram/Twitter'daki gibi karşılıklı görünmezlik).
-- ============================================================

create table if not exists public.blocks (
    blocker_id uuid not null references public.profiles(id) on delete cascade,
    blocked_id uuid not null references public.profiles(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (blocker_id, blocked_id),
    constraint blocks_no_self_block check (blocker_id <> blocked_id)
);

create index if not exists blocks_blocked_idx on public.blocks (blocked_id);

alter table public.blocks enable row level security;

drop policy if exists "blocks read"   on public.blocks;
drop policy if exists "blocks insert" on public.blocks;
drop policy if exists "blocks delete" on public.blocks;

-- Sadece kendi engelleme listeni görebilir/yönetebilirsin (kişisel/gizli liste,
-- bkz. bookmarks aynı desen) — engellendiğini bilmen gerekmiyor.
create policy "blocks read"   on public.blocks for select using (blocker_id = auth.uid());
create policy "blocks insert" on public.blocks for insert with check (blocker_id = auth.uid());
create policy "blocks delete" on public.blocks for delete using (blocker_id = auth.uid());

NOTIFY pgrst, 'reload schema';
