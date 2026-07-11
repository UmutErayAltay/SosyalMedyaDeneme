-- Yorum emoji tepkisi (comment_reaction) icin eksik DB parcalari.
-- Kod (notifications.py NOTIFICATION_TYPES + social.py react_comment) bu
-- turu bekliyordu ama ne tercih kolonu ne de type CHECK kisiti icermiyordu:
--   1) notification_preferences.notify_comment_reaction kolonu yoktu
--      -> tercih kaydetme upsert'i PGRST204 ile patliyordu
--   2) notifications_type_check 'comment_reaction' degerine izin vermiyordu
--      -> notify() insert'i 23514 ile patliyor, emoji tepkisi 500 donuyordu
--      (yalnizca INSERT yolunda: geri al + yeni emoji senaryosu)
-- Idempotent: tekrar calistirilabilir.

alter table if exists notification_preferences
  add column if not exists notify_comment_reaction boolean not null default true;

-- RLS'i tekrar etkinleştir (idempotent)
alter table notification_preferences enable row level security;

-- type CHECK kisitina 'comment_reaction' ekle (drop+add = idempotent)
alter table notifications drop constraint if exists notifications_type_check;
alter table notifications add constraint notifications_type_check
  check (type = any (array[
    'like', 'comment', 'reply', 'comment_like', 'follow',
    'message', 'mention', 'hashtag_post', 'comment_reaction'
  ]::text[]));
