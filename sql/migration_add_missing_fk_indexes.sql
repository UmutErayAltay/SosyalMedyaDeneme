-- ============================================================
-- INDEX'SİZ FOREIGN KEY TEMİZLİĞİ
-- get_advisors(type="performance") "unindexed_foreign_keys" bulgusunda
-- raporlanan 20 FK için kapsayan index ekliyor. Index eklemek JOIN/FK
-- kontrolü performansını artırır ve davranışı değiştirmez — güvenli.
-- Idempotent.
-- ============================================================

create index if not exists idx_bookmarks_collection_id
  on public.bookmarks (collection_id);

create index if not exists idx_bookmarks_post_id
  on public.bookmarks (post_id);

create index if not exists idx_comments_sticker_id
  on public.comments (sticker_id);

create index if not exists idx_conversations_created_by
  on public.conversations (created_by);

create index if not exists idx_messages_reply_to_id
  on public.messages (reply_to_id);

create index if not exists idx_messages_sender_id
  on public.messages (sender_id);

create index if not exists idx_messages_sticker_id
  on public.messages (sticker_id);

create index if not exists idx_muted_posts_post_id
  on public.muted_posts (post_id);

create index if not exists idx_muted_users_muted_id
  on public.muted_users (muted_id);

create index if not exists idx_notifications_actor_id
  on public.notifications (actor_id);

create index if not exists idx_notifications_comment_id
  on public.notifications (comment_id);

create index if not exists idx_notifications_conversation_id
  on public.notifications (conversation_id);

create index if not exists idx_notifications_hashtag_id
  on public.notifications (hashtag_id);

create index if not exists idx_notifications_post_id
  on public.notifications (post_id);

create index if not exists idx_poll_votes_option_id
  on public.poll_votes (option_id);

create index if not exists idx_polls_post_id
  on public.polls (post_id);

create index if not exists idx_post_views_user_id
  on public.post_views (user_id);

create index if not exists idx_posts_repost_of_id
  on public.posts (repost_of_id);

create index if not exists idx_profiles_pinned_post_id
  on public.profiles (pinned_post_id);

create index if not exists idx_reports_resolved_by
  on public.reports (resolved_by);
