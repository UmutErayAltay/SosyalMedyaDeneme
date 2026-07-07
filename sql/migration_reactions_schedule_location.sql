-- ============================================================
-- MESAJ TEPKİLERİ (EMOJI), POST PLANLAMA VE KONUM
-- Üç yeni özellik: mesaj emoji tepkileri, scheduled post ve konum bilgisi.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- ============================================================
-- 1) MESAJ EMOJİ TEPKİLERİ
-- ============================================================

-- message_reactions tablosunu oluştur
create table if not exists public.message_reactions (
  message_id uuid not null references public.messages(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  reaction text not null,
  created_at timestamp with time zone default now(),
  primary key (message_id, user_id)
);

-- Mesaj ID'lerine göre hızlı sorgu için index
create index if not exists idx_message_reactions_message_id
  on public.message_reactions(message_id);

-- Kullanıcı tarafındaki sorgular için index
create index if not exists idx_message_reactions_user_id
  on public.message_reactions(user_id);

-- message_reactions için RLS'i etkinleştir
alter table public.message_reactions enable row level security;

-- RLS Policy: Konuşma katılımcıları tepkileri okuyabilir
drop policy if exists "message_reactions_select_via_conversation" on public.message_reactions;
create policy "message_reactions_select_via_conversation"
  on public.message_reactions for select
  using (
    exists (
      select 1
      from public.messages m
      join public.conversation_participants cp on m.conversation_id = cp.conversation_id
      where m.id = message_id and cp.user_id = auth.uid()
    )
  );

-- RLS Policy: Kullanıcı sadece kendi tepkisini ekleyebilir/düzenleyebilir
drop policy if exists "message_reactions_insert" on public.message_reactions;
create policy "message_reactions_insert"
  on public.message_reactions for insert
  with check (
    user_id = auth.uid() and
    exists (
      select 1
      from public.messages m
      join public.conversation_participants cp on m.conversation_id = cp.conversation_id
      where m.id = message_id and cp.user_id = auth.uid()
    )
  );

-- RLS Policy: Kullanıcı sadece kendi tepkisini silebilir
drop policy if exists "message_reactions_delete" on public.message_reactions;
create policy "message_reactions_delete"
  on public.message_reactions for delete
  using (user_id = auth.uid());

-- ============================================================
-- 2) POST PLANLAMA
-- ============================================================

-- posts tablosuna scheduled_at kolonu ekle
-- NULL = normal post, dolu = planlı post (is_draft=true ile birlikte)
alter table public.posts
  add column if not exists scheduled_at timestamp with time zone;

-- Planlı postlar için index (zamanlanmış görevler için sorgu optimizasyonu)
create index if not exists idx_posts_scheduled_at_draft
  on public.posts(scheduled_at)
  where scheduled_at is not null and is_draft = true;

-- ============================================================
-- 3) POST KONUM BİLGİSİ
-- ============================================================

-- posts tablosuna konum kolonları ekle
alter table public.posts
  add column if not exists location_name text;

alter table public.posts
  add column if not exists location_lat double precision;

alter table public.posts
  add column if not exists location_lng double precision;

-- Konum sorgularının hızlanması için composite index
create index if not exists idx_posts_location_point
  on public.posts(location_lat, location_lng)
  where location_lat is not null and location_lng is not null;

-- ============================================================
-- PostgREST schema cache yenile
-- ============================================================

NOTIFY pgrst, 'reload schema';
