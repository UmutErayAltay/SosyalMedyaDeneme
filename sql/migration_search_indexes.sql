-- ============================================================
-- ARAMA PERFORMANS INDEX'LERİ
-- /search route'undaki ILIKE '%q%' sorguları (routes/discovery.py) tam
-- tablo taraması yapıyordu. pg_trgm trigram GIN index'leri bunu hızlandırır.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create extension if not exists pg_trgm;

create index if not exists idx_posts_content_trgm
  on public.posts using gin (content gin_trgm_ops);

create index if not exists idx_profiles_username_trgm
  on public.profiles using gin (username gin_trgm_ops);

create index if not exists idx_profiles_full_name_trgm
  on public.profiles using gin (full_name gin_trgm_ops);
