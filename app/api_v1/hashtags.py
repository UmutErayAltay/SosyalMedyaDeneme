from concurrent.futures import ThreadPoolExecutor

from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..routes._common import _attach_post_metrics
from ..visibility import followed_and_self_ids, close_friend_author_ids, filter_visible
from ..blocks import blocked_user_ids, filter_not_blocked
from ..polls import attach_polls
from ..hashtags import _trending_hashtags


# ----------------------- HASHTAG + GÜNDEM (Faz 4 sonrası eksik giderme, native Android) -----------------------
# app/hashtags.py hashtag_posts()/toggle_hashtag_follow()/_trending_hashtags()'in
# AYNI mantığı JSON'a taşınır — postlar /api/v1/feed ile AYNI sözleşmeyle
# (_attach_post_metrics/attach_polls) döner ki native'in mevcut PostCard/Post
# modeli DEĞİŞİKLİKSİZ render edebilsin. sync_post_hashtags/notify_hashtag_followers/
# extract_hashtags ZATEN import edilmişti (api_create_post için); burada SADECE
# _trending_hashtags eklendi (dosya başı importu).

@bp.route("/hashtag/<tag>")
@api_login_required
def api_hashtag_posts(tag):
    """Bir etiketin postları — hashtags.py hashtag_posts()'ın AYNI mantığı
    (paralel blocked/followed/close-friend çekimi + filter_visible/
    filter_not_blocked), ama HTML render YOK. Migration henüz uygulanmamışsa
    (hashtags tablosu yok) hashtag_posts()'daki AYNI davranış: geniş except
    boş sonuca düşer, istek asla 500 ile kırılmaz."""
    sb = get_sb()
    me = request.api_user["id"]
    tag = tag.lower()

    posts = []
    is_following = False
    try:
        ht = sb.table("hashtags").select("id").eq("tag", tag).execute().data
        if ht:
            hashtag_id = ht[0]["id"]
            rows = sb.table("post_hashtags").select("post_id").eq(
                "hashtag_id", hashtag_id
            ).execute().data
            post_ids = [r["post_id"] for r in rows]
            if post_ids:
                posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", post_ids).eq("is_archived", False).eq("is_draft", False).order(
                    "created_at", desc=True
                ).execute().data

                # Paralel: blocked_ids, followed_ids, close_friend_ids çek (hashtag_posts()'daki AYNI desen)
                def _fetch_blocked():
                    return blocked_user_ids(sb, me)

                def _fetch_followed():
                    return followed_and_self_ids(sb, me)

                def _fetch_close_friends():
                    return close_friend_author_ids(sb, me)

                with ThreadPoolExecutor(max_workers=3) as executor:
                    blocked_future = executor.submit(_fetch_blocked)
                    followed_future = executor.submit(_fetch_followed)
                    close_future = executor.submit(_fetch_close_friends)

                    blocked_ids = blocked_future.result()
                    followed_ids = followed_future.result()
                    close_friend_ids = close_future.result()

                posts = filter_visible(sb, posts, followed_ids, close_friend_ids, me)
                posts = filter_not_blocked(posts, blocked_ids)
                _attach_post_metrics(sb, posts, me)
                attach_polls(sb, posts, me)

            is_following = bool(sb.table("hashtag_follows").select("user_id")
                                 .eq("user_id", me).eq("hashtag_id", hashtag_id).execute().data)
    except Exception:
        pass  # sql/migration_hashtags.sql henüz uygulanmamışsa boş liste (hashtag_posts() ile AYNI tolerans)

    return jsonify(posts=posts, is_following=is_following)


@bp.route("/hashtag/<tag>/follow", methods=["POST"])
@api_login_required
def api_toggle_hashtag_follow(tag):
    """Etiket takip et/bırak — toggle_hashtag_follow()'ın AYNI mantığı, form/
    redirect dalı YOK (native zaten JSON bekliyor). Web tarafı gibi try/except
    İLE korunmaz: hashtag_follows migration'ı hashtags/post_hashtags ile AYNI
    dosyada geldiği için hashtag_posts()'un zaten görebildiği bir etiket
    sayfasında bu tabloyu bulamamak beklenmez."""
    sb = get_sb()
    me = request.api_user["id"]
    tag = tag.lower()

    existing_tag = sb.table("hashtags").select("id").eq("tag", tag).execute().data
    hashtag_id = existing_tag[0]["id"] if existing_tag else (
        sb.table("hashtags").insert({"tag": tag}).execute().data[0]["id"]
    )

    existing = sb.table("hashtag_follows").select("user_id").eq(
        "user_id", me).eq("hashtag_id", hashtag_id).execute().data
    if existing:
        sb.table("hashtag_follows").delete().eq("user_id", me).eq("hashtag_id", hashtag_id).execute()
        following = False
    else:
        sb.table("hashtag_follows").insert({"user_id": me, "hashtag_id": hashtag_id}).execute()
        following = True

    return jsonify(following=following)


@bp.route("/trending")
@api_login_required
def api_trending():
    """Gündem — web'in /gundem'iyle AYNI çağrı (`_trending_hashtags(sb, hours=24,
    limit=50)`), AYNI 120sn TTL cache (anahtar limit'ten bağımsız — bkz.
    hashtags.py _trending_hashtags docstring'i, cache key'e limit KATILMAZ)."""
    sb = get_sb()
    tags = _trending_hashtags(sb, hours=24, limit=50)
    return jsonify(tags=tags)
