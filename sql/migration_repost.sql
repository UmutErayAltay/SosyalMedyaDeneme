-- Repost/alıntılı paylaşım: posts.repost_of_id + bildirim tipine 'repost'
-- on delete cascade: orijinal post silinirse repost'ları da silinir
-- (yetim "içerik yok" placeholder'ları yerine bilinçli tercih).

alter table public.posts
add column if not exists repost_of_id uuid references public.posts(id) on delete cascade;

-- notifications.type CHECK'ine 'repost' ekle (drop+add, idempotent)
alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check check (
    type = any (array['like'::text, 'comment'::text, 'reply'::text,
                      'comment_like'::text, 'follow'::text, 'message'::text,
                      'mention'::text, 'hashtag_post'::text,
                      'comment_reaction'::text, 'follow_request'::text,
                      'follow_accept'::text, 'story_reaction'::text,
                      'repost'::text])
);
