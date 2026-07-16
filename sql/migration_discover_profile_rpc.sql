-- ============================================================
-- Keşfet + Profil sayfaları RPC'leri (feed_page_posts deseninin devamı).
-- enrich_post_json: post ID'sini alarak, uygulamadaki _attach_post_metrics +
-- attach_polls sonrası dict ile birebir aynı JSON'a çevirir.
--
-- VERI SÖZLEŞMESİ: Bu RPC'nin şeması (enrich_post_json dönüş yapısı)
-- app/routes/_common.py'deki _attach_post_metrics() ve app/polls.py'deki
-- attach_polls() fonksiyonlarıyla senkron tutulmalıdır. Alanlar uyumsuzsa,
-- RPC başarılı olduğu zaman (feed/discover/profile sayfaları RPC yolunda) ile
-- RPC başarısız/migration uygulanmamışken (Python fallback yolu) farklı veri
-- şeması döner, template _post_card.html'de KeyError/None ortaya çıkar.
-- ============================================================

-- Post zenginleştirme helper: post_id ve viewer'ın UUID'sini alarak,
-- tam zenginleştirilmiş post JSON'ı döner (profil, sayaçlar, beğeni,
-- anket vb.). Tip sorunu olmayacak şekilde post_id ile query.
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

-- Keşfet: takip ETMEDİĞİN kişilerin son 7 gündeki herkese açık postları,
-- beğeni+yorum toplamına göre en popüler p_limit tanesi. Engel kontrolü
-- dahil. Skor eşitliğinde created_at desc ile tie-break.
create or replace function public.discover_page_posts(p_me uuid, p_limit int default 20)
returns jsonb
language sql
stable
set search_path = public
as $$
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
    -- Gizli profil kontrolü: yazarın profili açık VEYA (zaten takip ediyor — önceki satırda false ama accepted kontrol edelim)
    and (not coalesce((select pr.is_private from profiles pr where pr.id = p.user_id), false)
         or exists (select 1 from follows f2 where f2.follower_id = p_me and f2.following_id = p.user_id and f2.status = 'accepted'))
    and not exists (
      select 1 from blocks b
      where (b.blocker_id = p_me and b.blocked_id = p.user_id)
         or (b.blocker_id = p.user_id and b.blocked_id = p_me)
    )
  order by _score desc, p.created_at desc
  limit p_limit
)
select coalesce(jsonb_agg(
         enrich_post_json(cand.id, p_me) || jsonb_build_object('_score', cand._score)
         order by cand._score desc, cand.id desc), '[]'::jsonb)
from cand
$$;

-- Profil: viewer'a görünür post listesi + beğendikleri + (kendisiyse)
-- kaydettikleri + takipçi sayıları + özel profil alanları TEK round-trip'te.
-- Görünürlük semantiği app/visibility.py filter_visible ile birebir:
-- public → herkes; close_friends → yazar viewer'ı yakın arkadaş eklediyse
-- veya kendisiyse; DİĞER HER DEĞER (followers, NULL, bilinmeyen) →
-- viewer yazarı (ACCEPTED olarak) takip ediyorsa veya kendisiyse. (Feed'dekinden farklı:
-- feed NULL visibility'yi gizler, profil 'followers' gibi davranır —
-- bu fark mevcut Python kodundan geliyor, AYNEN korunuyor.)
-- is_private=true ve viewer accepted değilse (ve owner değilse) posts boş array döner.
create or replace function public.profile_page_data(p_viewer uuid, p_owner uuid, p_include_bookmarks boolean default false)
returns jsonb
language sql
stable
set search_path = public
as $$
with visible as (
  select p.id
  from posts p
  where coalesce(p.is_archived, false) = false
    and (
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

-- PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
