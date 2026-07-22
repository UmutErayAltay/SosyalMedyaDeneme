-- enrich_post_json() RPC'sine muted_by_me alanı eklendi (bookmarked_by_me ile
-- aynı desen) — post-bazlı sessize alma özelliğinin post kartında doğru
-- başlangıç durumunu (mute'lu/değil) gösterebilmesi için. Python fallback
-- tarafı (_attach_post_metrics, app/routes/_common.py) zaten aynı alanı
-- ekliyor; RPC ve fallback şemaları eşleşmeli (.claude/rules/backend.md).
create or replace function public.enrich_post_json(p_post_id uuid, p_me uuid)
returns jsonb
language sql
stable
set search_path to 'public'
as $function$
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
    'muted_by_me', exists (select 1 from muted_posts mp
                           where mp.post_id = p.id and mp.user_id = p_me),
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
$function$;
