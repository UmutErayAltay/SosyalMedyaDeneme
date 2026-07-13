-- notifications.type CHECK kısıtına story_reaction ekler (hikaye emoji tepkisi özelliği)
-- Aynı desen: migration_notification_types_follow_request.sql. Idempotent.

alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
  check (type = any (array[
    'like', 'comment', 'reply', 'comment_like', 'follow', 'message', 'mention',
    'hashtag_post', 'comment_reaction', 'follow_request', 'follow_accept', 'story_reaction'
  ]));
