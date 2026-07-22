-- feed_page_posts RPC'sine muted_by_me alanı eklendi (bookmarked_by_me ile
-- aynı desen, enrich_post_json_muted_by_me migration'ıyla birlikte) — bu
-- fonksiyon enrich_post_json() ÇAĞIRMIYOR, post JSON'unu kendi inline
-- jsonb_build_object'iyle üretiyor, bu yüzden ayrıca güncellenmesi gerekti.
create or replace function public.feed_page_posts(p_me uuid, p_offset integer default 0, p_limit integer default 25)
returns jsonb
language sql
stable
set search_path to 'public'
as $function$
with vis as (
  select p.*
  from posts p
  where coalesce(p.is_draft, false) = false
    and coalesce(p.is_archived, false) = false
    and (p.user_id = p_me
         or not coalesce((select pr.is_private from profiles pr where pr.id = p.user_id), false)
         or exists (select 1 from follows f2 where f2.follower_id = p_me and f2.following_id = p.user_id and f2.status = 'accepted'))
    and not exists (
      select 1 from blocks b
      where (b.blocker_id = p_me and b.blocked_id = p.user_id)
         or (b.blocker_id = p.user_id and b.blocked_id = p_me)
    )
    -- Sessize alınan kullanıcılar: ben bu kişiyi mute etmişsem feed'de gösterme
    and not exists (
      select 1 from muted_users mu
      where mu.muter_id = p_me and mu.muted_id = p.user_id
    )
    -- Deaktif edilmiş hesapların postlarını gizle
    and not coalesce((select pr.is_deactivated from profiles pr where pr.id = p.user_id), false)
    and (
      p.visibility = 'public'
      or (p.visibility = 'followers' and (p.user_id = p_me or exists (
            select 1 from follows f
            where f.follower_id = p_me and f.following_id = p.user_id and f.status = 'accepted')))
      or (p.visibility = 'close_friends' and (p.user_id = p_me or exists (
            select 1 from close_friends cf
            where cf.owner_id = p.user_id and cf.friend_id = p_me)))
    )
  order by p.created_at desc
  offset p_offset
  limit p_limit
)
select coalesce(jsonb_agg(j.post order by j.created_at desc), '[]'::jsonb)
from (
  select
    v.created_at,
    to_jsonb(v)
    || jsonb_build_object(
      'profiles', (select jsonb_build_object('username', pr.username, 'avatar_url', pr.avatar_url)
                   from profiles pr where pr.id = v.user_id),
      'like_count', (select count(*) from likes l where l.post_id = v.id),
      'comment_count', (select count(*) from comments c where c.post_id = v.id),
      'liked_by_me', exists (select 1 from likes l where l.post_id = v.id and l.user_id = p_me),
      'my_reaction', (select coalesce(l.reaction_type, 'like') from likes l
                      where l.post_id = v.id and l.user_id = p_me),
      'bookmarked_by_me', exists (select 1 from bookmarks bm
                                  where bm.post_id = v.id and bm.user_id = p_me),
      'muted_by_me', exists (select 1 from muted_posts mp
                             where mp.post_id = v.id and mp.user_id = p_me),
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
        from polls po where po.post_id = v.id
        limit 1
      )
    ) as post
  from vis v
) j
$function$;
