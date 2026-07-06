-- ============================================================
-- KAYDEDİLENLER KLASÖRLERİ (bookmark_collections) — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.bookmark_collections (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references public.profiles(id) on delete cascade,
    name       text not null,
    created_at timestamptz not null default now()
);

create index if not exists bookmark_collections_user_idx on public.bookmark_collections (user_id);

-- bookmarks.collection_id NULL = "Genel" (klasörsüz). Klasör silinirse
-- ON DELETE SET NULL ile içindeki kayıtlar sessizce Genel'e döner.
alter table public.bookmarks add column if not exists collection_id uuid references public.bookmark_collections(id) on delete set null;

alter table public.bookmark_collections enable row level security;

-- Klasörler de bookmarks gibi mahrem: sadece sahibi görebilir/oluşturabilir/silebilir.
drop policy if exists "bookmark_collections read"   on public.bookmark_collections;
drop policy if exists "bookmark_collections insert" on public.bookmark_collections;
drop policy if exists "bookmark_collections delete" on public.bookmark_collections;
create policy "bookmark_collections read"   on public.bookmark_collections for select using (auth.uid() = user_id);
create policy "bookmark_collections insert" on public.bookmark_collections for insert with check (auth.uid() = user_id);
create policy "bookmark_collections delete" on public.bookmark_collections for delete using (auth.uid() = user_id);

NOTIFY pgrst, 'reload schema';
