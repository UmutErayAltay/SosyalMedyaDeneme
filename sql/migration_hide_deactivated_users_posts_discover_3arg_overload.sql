-- discover_page_posts'un 3-arg overload'u (p_me, p_limit, p_offset) — asıl
-- /discover sayfasının kullandığı sürüm — migration_hide_deactivated_users_posts_from_feed_discover.sql
-- sadece 2-arg overload'u (posts.py'nin explore_suggestions'ı) güncellemişti,
-- bu overload'da is_deactivated filtresi eksik kalmıştı.
create or replace function public.discover_page_posts(p_me uuid, p_limit integer default 20, p_offset integer default 0)
returns jsonb
language sql
stable
set search_path to 'public'
as $function$
with cand as (
  select p.id,
         (select count(*) from likes l where l.post_id = p.id)
       + (select count(*) from comments c where c.post_id = p.id) as _score
  from posts p
  where coalesce(p.is_draft, false) = false
    and coalesce(p.is_archived, false) = false
    and p.visibility = 'public'
    and p.created_at >= now() - interval '7 days'
    and p.user_id <> p_me
    and not exists (select 1 from follows f
                    where f.follower_id = p_me and f.following_id = p.user_id)
    and (not coalesce((select pr.is_private from profiles pr where pr.id = p.user_id), false)
         or exists (select 1 from follows f2 where f2.follower_id = p_me and f2.following_id = p.user_id and f2.status = 'accepted'))
    and not exists (
      select 1 from blocks b
      where (b.blocker_id = p_me and b.blocked_id = p.user_id)
         or (b.blocker_id = p.user_id and b.blocked_id = p_me)
    )
    -- Deaktif edilmiş hesapların postlarını gizle
    and not coalesce((select pr.is_deactivated from profiles pr where pr.id = p.user_id), false)
  order by _score desc, p.created_at desc
  limit p_limit offset p_offset
)
select coalesce(jsonb_agg(
         enrich_post_json(cand.id, p_me) || jsonb_build_object('_score', cand._score)
         order by cand._score desc, cand.id desc), '[]'::jsonb)
from cand
$function$;
