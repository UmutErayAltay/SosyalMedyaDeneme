-- GERİ KAZANILDI (2026-07-17): apply_migration ile uygulanmış (version
-- 20260712222126) ama dosya olarak repoya hiç eklenmemiş —
-- supabase_migrations.schema_migrations.statements'ten kurtarıldı.
-- TEKRAR UYGULANMASINA GEREK YOK, zaten canlıda. Bu fonksiyonların GÜNCEL
-- hâli daha sonraki migration'larla (bkz. migration_discover_profile_rpc.sql)
-- üzerine yazılmış olabilir — bu dosya sadece tarihsel kayıt.
create or replace function public.enrich_post_json(p_post_id uuid, p_me uuid)
returns jsonb
language sql
stable
set search_path = public
as $$
  select to_jsonb(p)
  || jsonb_build_object(
    'profiles', (select jsonb_build_object('username', pr.username, 'avatar_url', pr.avatar_url)
                 from profiles pr where pr.id = p.user_id),
    'like_count', (select count(*) from likes l where l.post_id = p.id),
    'comment_count', (select count(*) from comments c where c.post_id = p.id),
    'liked_by_me', exists (select 1 from likes l where l.post_id = p.id and l.user_id = p_me),
    'my_reaction', (select coalesce(l.reaction_type, 'like') from likes l
                    where l.post_id = p.id and l.user_id = p_me),
    'bookmarked_by_me', exists (select 1 from bookmarks bm
                                where bm.post_id = p.id and bm.user_id = p_me),
    'poll', (
      select jsonb_build_object(
        'id', po.id,
        'total_votes', (select count(*) from poll_votes pv where pv.poll_id = po.id),
        'my_vote', (select pv.option_id from poll_votes pv
                    where pv.poll_id = po.id and pv.user_id = p_me),
        'options', (
          select coalesce(jsonb_agg(jsonb_build_object(
                   'id', o.id,
                   'text', o.option_text,
                   'votes', (select count(*) from poll_votes pv2 where pv2.option_id = o.id),
                   'pct', case
                     when (select count(*) from poll_votes pv3 where pv3.poll_id = po.id) = 0 then 0
                     else round(100.0 * (select count(*) from poll_votes pv2 where pv2.option_id = o.id)
                          / (select count(*) from poll_votes pv3 where pv3.poll_id = po.id))
                   end
                 ) order by o.position), '[]'::jsonb)
          from poll_options o where o.poll_id = po.id
        )
      )
      from polls po where po.post_id = p.id
      limit 1
    )
  )
  from posts p
  where p.id = p_post_id
$$;

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
  'liked_posts', (
    select coalesce(jsonb_agg(
             enrich_post_json(p.id, p_viewer)
             order by lk.created_at desc), '[]'::jsonb)
    from likes lk
    join posts p on p.id = lk.post_id
    where lk.user_id = p_owner
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

NOTIFY pgrst, 'reload schema';
