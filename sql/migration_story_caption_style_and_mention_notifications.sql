-- Hikaye altyazı stili (caption_style) + hikaye @mention bildirim tipi
-- (story_mention) + bunun için notification_preferences opt-out kolonu.
--
-- Bağlam: app/api_v1/stories.py (caption_style form alanı, satır ~137),
-- app/mentions.py (notify_story_mention()), app/notifications.py
-- (NOTIFICATION_TYPES listesine 'story_mention' zaten eklenmişti — bu
-- migration DB tarafını Python tarafıyla senkronize eder).

-- 1) stories.caption_style — hap-şekilli (pill) altyazı arka planı, NULL
-- (render edilmez) veya iki sabit değerden biri.
alter table public.stories
    add column if not exists caption_style text;

alter table public.stories
    drop constraint if exists stories_caption_style_check;

alter table public.stories
    add constraint stories_caption_style_check
    check (caption_style is null or caption_style in ('pill_light', 'pill_dark'));

-- 2) notifications.type CHECK kısıtına 'story_mention' eklenir. Mevcut
-- liste pg_get_constraintdef ile DOĞRULANDI (varsayılmadı):
--   like, comment, reply, comment_like, follow, message, mention,
--   hashtag_post, comment_reaction, follow_request, follow_accept,
--   story_reaction, repost
-- Aşağıdaki liste TAMAMEN bu gerçek listenin korunmasıyla + story_mention
-- eklenmesiyle oluşturuldu.
alter table public.notifications
    drop constraint if exists notifications_type_check;

alter table public.notifications
    add constraint notifications_type_check
    check (type = any (array[
        'like', 'comment', 'reply', 'comment_like', 'follow', 'message',
        'mention', 'hashtag_post', 'comment_reaction', 'follow_request',
        'follow_accept', 'story_reaction', 'repost', 'story_mention'
    ]));

-- 3) notification_preferences.notify_story_mention — tablo VE
-- notify_<type> deseni doğrulandı (notify_story_reaction, notify_repost
-- vb. zaten aynı desende), bu yüzden aynı desenle eklenir.
alter table public.notification_preferences
    add column if not exists notify_story_mention boolean not null default true;

-- ROLLBACK:
-- alter table public.notification_preferences drop column if exists notify_story_mention;
-- alter table public.notifications drop constraint if exists notifications_type_check;
-- alter table public.notifications add constraint notifications_type_check
--     check (type = any (array['like','comment','reply','comment_like','follow','message',
--         'mention','hashtag_post','comment_reaction','follow_request','follow_accept',
--         'story_reaction','repost']));
-- alter table public.stories drop constraint if exists stories_caption_style_check;
-- alter table public.stories drop column if exists caption_style;
