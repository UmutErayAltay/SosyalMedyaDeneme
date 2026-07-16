-- GERİ KAZANILDI (2026-07-17): apply_migration ile uygulanmış (version
-- 20260712222604) ama dosya olarak repoya hiç eklenmemiş — kurtarıldı.
-- TEKRAR UYGULANMASINA GEREK YOK, zaten canlıda.
create or replace function public.profile_page_data(p_viewer uuid, p_owner uuid, p_include_bookmarks boolean default false)
returns jsonb
language sql
stable
set search_path = public
as $$
with visible as (
  select p.id
  from posts p
  where (
      p.visibility = 'public'
      or (p.visibility = 'close_friends' and (p.user_id = p_viewer or exists (
            select 1 from close_friends cf
            where cf.owner_id = p.user_id and cf.friend_id = p_viewer)))
      or (coalesce(p.visibility, 'followers') not in ('public', 'close_friends')
          and (p.user_id = p_viewer or exists (
            select 1 from follows f
            where f.follower_id = p_viewer and f.following_id = p.user_id and f.status = 'accepted')))
    )
    and not exists (
      select 1 from blocks b
      where (b.blocker_id = p_viewer and b.blocked_id = p.user_id)
         or (b.blocker_id = p.user_id and b.blocked_id = p_viewer)
    )
)
select jsonb_build_object(
  'is_private', coalesce((select pr.is_private from profiles pr where pr.id = p_owner), false),
  'is_following', exists (select 1 from follows
                          where follower_id = p_viewer and following_id = p_owner and status = 'accepted'),
  'is_pending_request', p_viewer <> p_owner and exists (select 1 from follows
                                                        where follower_id = p_viewer and following_id = p_owner and status = 'pending'),
  'posts', (
    select coalesce(jsonb_agg(
             enrich_post_json(p.id, p_viewer)
             order by (p.id = (select pinned_post_id from profiles where id = p_owner)) desc,
                      p.created_at desc), '[]'::jsonb)
    from posts p
    where p.user_id = p_owner
      and coalesce(p.is_draft, false) = false
      and (p_viewer = p_owner or not coalesce((select pr.is_private from profiles pr where pr.id = p_owner), false)
           or exists (select 1 from follows f where f.follower_id = p_viewer and f.following_id = p_owner and f.status = 'accepted'))
      and p.id in (select id from visible)
  ),
  -- DÜZELTME (bu migration'ın asıl amacı): liked_posts artık YAZARIN
  -- gizlilik durumunu da kontrol ediyor — önceki sürüm sadece görünürlük
  -- (visible CTE) kontrolü yapıyordu, gizli bir hesabın beğendiği postlar
  -- viewer accepted takipçi olmasa bile sızıyordu.
  'liked_posts', (
    select coalesce(jsonb_agg(
             enrich_post_json(p.id, p_viewer)
             order by lk.created_at desc), '[]'::jsonb)
    from likes lk
    join posts p on p.id = lk.post_id
    where lk.user_id = p_owner
      and (p_viewer = p_owner or not coalesce((select pr.is_private from profiles pr where pr.id = p_owner), false)
           or exists (select 1 from follows f where f.follower_id = p_viewer and f.following_id = p_owner and f.status = 'accepted'))
      and p.id in (select id from visible)
  ),
  'bookmarked_posts', case when p_include_bookmarks then (
    select coalesce(jsonb_agg(
             enrich_post_json(p.id, p_viewer)
             || jsonb_build_object('bookmark_collection_id', bm.collection_id)
             order by bm.created_at desc), '[]'::jsonb)
    from bookmarks bm
    join posts p on p.id = bm.post_id
    where bm.user_id = p_viewer
      and p.id in (select id from visible)
  ) else '[]'::jsonb end,
  'followers_count', (select count(*) from follows where following_id = p_owner and status = 'accepted'),
  'following_count', (select count(*) from follows where follower_id = p_owner and status = 'accepted')
)
$$;
