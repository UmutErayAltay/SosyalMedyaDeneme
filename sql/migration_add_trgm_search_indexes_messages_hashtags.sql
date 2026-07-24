-- ============================================================
-- ARAMA PERFORMANS INDEX'LERİ (devam) — mesaj ve hashtag araması
-- migration_search_indexes.sql zaten posts.content, profiles.username,
-- profiles.full_name için pg_trgm GIN index'i eklemişti. Bu migration
-- eksik kalan iki kolonu tamamlıyor:
--   - messages.content  → app/messaging/views.py mesaj içi arama (ILIKE '%q%')
--   - hashtags.tag       → app/routes/discovery.py hashtag araması (ILIKE '%q%')
-- '%q%' deseni B-tree index'ten yararlanamaz, pg_trgm GIN gerekir.
-- Idempotent.
-- ============================================================

create extension if not exists pg_trgm;

create index if not exists idx_messages_content_trgm
  on public.messages using gin (content gin_trgm_ops);

create index if not exists idx_hashtags_tag_trgm
  on public.hashtags using gin (tag gin_trgm_ops);
