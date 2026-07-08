-- ============================================================
-- STICKER (ÇIKARTMA) ÖZELLİĞİ
-- Yeni özellik: Sticker oluşturma, favorileme ve mesaj/yorum içinde kullanım.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- ============================================================
-- 1) STICKER TABLOSU
-- ============================================================

-- Sticker tablosunu oluştur (creator_id, image_url)
create table if not exists public.stickers (
  id uuid not null default gen_random_uuid() primary key,
  creator_id uuid not null references public.profiles(id) on delete cascade,
  image_url text not null,
  created_at timestamp with time zone default now()
);

-- Creator sorguları için index
create index if not exists idx_stickers_creator_id
  on public.stickers(creator_id);

-- Stickers için RLS'i etkinleştir
alter table public.stickers enable row level security;

-- RLS Policy: Tüm kimlik doğrulanmış kullanıcılar sticker'ları okuyabilir
drop policy if exists "stickers_select" on public.stickers;
create policy "stickers_select"
  on public.stickers for select
  using (auth.role() = 'authenticated');

-- RLS Policy: Kullanıcı sadece kendi sticker'ını oluşturabilir
drop policy if exists "stickers_insert" on public.stickers;
create policy "stickers_insert"
  on public.stickers for insert
  with check (creator_id = auth.uid());

-- RLS Policy: Kullanıcı sadece kendi sticker'ını silebilir
drop policy if exists "stickers_delete" on public.stickers;
create policy "stickers_delete"
  on public.stickers for delete
  using (creator_id = auth.uid());

-- ============================================================
-- 2) KULLANICI STICKER LİSTESİ (FAVORİ)
-- ============================================================

-- user_stickers tablosunu oluştur (başkasının sticker'ını yıldızla ekleyebilir)
create table if not exists public.user_stickers (
  user_id uuid not null references public.profiles(id) on delete cascade,
  sticker_id uuid not null references public.stickers(id) on delete cascade,
  created_at timestamp with time zone default now(),
  primary key (user_id, sticker_id)
);

-- Sticker ID'lerine göre hızlı sorgu için index
create index if not exists idx_user_stickers_sticker_id
  on public.user_stickers(sticker_id);

-- user_stickers için RLS'i etkinleştir
alter table public.user_stickers enable row level security;

-- RLS Policy: Kullanıcı sadece kendi sticker listesini okuyabilir
drop policy if exists "user_stickers_select" on public.user_stickers;
create policy "user_stickers_select"
  on public.user_stickers for select
  using (user_id = auth.uid());

-- RLS Policy: Kullanıcı sadece kendi sticker listesine ekleyebilir
drop policy if exists "user_stickers_insert" on public.user_stickers;
create policy "user_stickers_insert"
  on public.user_stickers for insert
  with check (user_id = auth.uid());

-- RLS Policy: Kullanıcı sadece kendi sticker listesinden silebilir
drop policy if exists "user_stickers_delete" on public.user_stickers;
create policy "user_stickers_delete"
  on public.user_stickers for delete
  using (user_id = auth.uid());

-- ============================================================
-- 3) MESAJA STICKER REFERANSI
-- ============================================================

-- messages tablosuna sticker_id kolonu ekle
alter table public.messages
  add column if not exists sticker_id uuid references public.stickers(id) on delete set null;

-- ============================================================
-- 4) YORUMA STICKER VE GIF REFERANSI
-- ============================================================

-- comments tablosuna sticker_id kolonu ekle
alter table public.comments
  add column if not exists sticker_id uuid references public.stickers(id) on delete set null;

-- comments tablosuna gif_url kolonu ekle
alter table public.comments
  add column if not exists gif_url text;

-- ============================================================
-- PostgREST schema cache yenile
-- ============================================================

NOTIFY pgrst, 'reload schema';
