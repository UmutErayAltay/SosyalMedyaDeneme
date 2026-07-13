-- notifications.type CHECK kısıtına follow_request + follow_accept ekler
-- (gizli profil takip istekleri özelliği) — aynı desen: migration_reactions.sql,
-- migration_mentions.sql, migration_notification_preferences_comment_reaction.sql
-- vb. her yeni bildirim türünde bu kısıt genişletiliyor. Idempotent.

alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
  check (type = any (array[
    'like', 'comment', 'reply', 'comment_like', 'follow', 'message', 'mention',
    'hashtag_post', 'comment_reaction', 'follow_request', 'follow_accept'
  ]));
