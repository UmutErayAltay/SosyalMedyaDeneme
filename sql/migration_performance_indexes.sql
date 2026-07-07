-- Performans optimizasyonu: sıcak sorgu desenleri için eksik index'ler
-- Sıcak desenlerdeki eksikler
CREATE INDEX IF NOT EXISTS idx_poll_votes_user_id ON public.poll_votes USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_id ON public.poll_votes USING btree (poll_id);
CREATE INDEX IF NOT EXISTS idx_comments_user_id ON public.comments USING btree (user_id);

-- Advisors'ta önerilen, uygulamada sık kullanılan FK index'leri
CREATE INDEX IF NOT EXISTS idx_likes_post_id ON public.likes USING btree (post_id);
CREATE INDEX IF NOT EXISTS idx_conversation_participants_user_id ON public.conversation_participants USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_follows_following_id ON public.follows USING btree (following_id);
CREATE INDEX IF NOT EXISTS idx_story_views_user_id ON public.story_views USING btree (user_id);
