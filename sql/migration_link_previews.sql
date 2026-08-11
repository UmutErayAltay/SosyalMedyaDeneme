-- ============================================================
-- LINK PREVIEW CACHE — Şema migration
-- Mesaj/gönderi linklerinde OG unfurling (başlık/açıklama/görsel/site adı)
-- için cache tablosu. Aynı URL tekrar paylaşılırsa dış siteye tekrar
-- istek atılmasın diye normalized_url üzerinden tekilleştirilir.
-- Supabase MCP apply_migration ile uygulanır. Idempotent.
-- ============================================================

create table if not exists public.link_previews (
    id uuid primary key default gen_random_uuid(),
    normalized_url text not null unique,
    url text not null,
    title text,
    description text,
    image_url text,
    site_name text,
    fetch_failed boolean not null default false,
    fetched_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create index if not exists link_previews_normalized_url_idx on public.link_previews (normalized_url);

alter table public.link_previews enable row level security;

-- Herkese açık URL metadata'sı — hassas/kullanıcıya özel veri yok, okuma
-- serbest. Yazma (insert/update/delete) için BİLİNÇLİ olarak hiç politika
-- YOK: client (anon/authenticated) hiçbir şekilde yazamaz, sadece backend
-- service-role (RLS'yi bypass eder) cache'i doldurur/günceller.
drop policy if exists "link_previews read" on public.link_previews;
create policy "link_previews read" on public.link_previews for select using (true);

NOTIFY pgrst, 'reload schema';
