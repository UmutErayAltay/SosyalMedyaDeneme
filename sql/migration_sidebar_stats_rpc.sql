-- ============================================================
-- Kenar çubuğu istatistikleri: post/takipçi/takip sayısı + bio TEK sorguda.
-- Önceden routes/_common.py fetch_sidebar_context() bunları 4 ayrı Supabase
-- sorgusuyla (ThreadPoolExecutor'da paralel) çekiyordu — RPC'ye indirmek
-- round-trip/bağlantı sayısını azaltır (bkz. Sprint 53'teki feed_page_posts
-- gerekçesi: her sorgu ~400ms PostgREST overhead taşır).
-- ============================================================

create or replace function public.sidebar_stats(p_me uuid)
returns jsonb
language sql
stable
set search_path = public
as $$
  select jsonb_build_object(
    'posts_count', (select count(*) from posts where user_id = p_me and is_draft = false),
    'followers_count', (select count(*) from follows where following_id = p_me),
    'following_count', (select count(*) from follows where follower_id = p_me),
    'bio', (select bio from profiles where id = p_me)
  );
$$;
