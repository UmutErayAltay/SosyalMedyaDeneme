-- ============================================================
-- HİKAYE ANKETLERİ: polls tablosunu post-specific'ten genel kıl,
-- story_id desteği ekle. post_id NULLABLE olur, ama CHECK constraint ile
-- post_id ve story_id'den TAM OLARAK BİRİ dolu olmalı.
-- Idempotent. Supabase Dashboard → SQL Editor'de çalıştır.
-- ============================================================

-- Step 1: post_id UNIQUE constraint'ini kaldır (çünkü NULL story_id'lerle çakışacak)
alter table if exists public.polls drop constraint if exists polls_post_id_key;

-- Step 2: post_id'yi NULLABLE yap (eğer zaten değilse)
alter table if exists public.polls alter column post_id drop not null;

-- Step 3: story_id kolonu ekle (nullable, hikaye silinince anket de silinir)
alter table if exists public.polls add column if not exists story_id uuid unique references public.stories(id) on delete cascade;

-- Step 4: CHECK constraint ekle: post_id ve story_id'den TAM OLARAK BİRİ dolu olmalı
alter table if exists public.polls drop constraint if exists polls_one_of_post_or_story;
alter table public.polls add constraint polls_one_of_post_or_story
  check ((post_id is not null and story_id is null) or (post_id is null and story_id is not null));

-- Step 5: Index'ler
create index if not exists polls_story_id_idx on public.polls (story_id);

-- Mevcut RLS politikaları zaten read/insert için yeterli (post/story veri herkese açık,
-- oy alma zaten auth'a bağlı). Değişiklik gerekmez.

NOTIFY pgrst, 'reload schema';
