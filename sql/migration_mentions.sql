-- ============================================================
-- @MENTION BİLDİRİMİ — notifications.type CHECK kısıtına 'mention' ekler.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- Ayrı bir tablo gerekmez: mention'lar post/comment içeriğinden anlık
-- çıkarılıp doğrudan profiles.username'e karşı doğrulanır (bkz. app/mentions.py).
-- ============================================================

alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
    check (type in ('like','comment','reply','comment_like','follow','message','mention'));

-- PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
