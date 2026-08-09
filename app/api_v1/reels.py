from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..routes._common import _attach_post_metrics, PAGE_SIZE
from ..visibility import followed_and_self_ids, close_friend_author_ids, filter_visible
from ..blocks import blocked_user_ids


# ----------------------- REELS (Faz 3, native Android reels ekranı) -----------------------
# app/routes/reels.py reels()'in AYNI mantığı — RPC yok (basit sorgu), anket yok.

@bp.route("/reels")
@api_login_required
def api_reels():
    """Dikey video akışı — reels.py reels()'in AYNI filtre/sıralama mantığı,
    JSON döner. is_reel migration'ı henüz uygulanmamışsa boş liste döner
    (hata değil, web'deki AYNI graceful degradation).

    Visibility filtresi (2026-08-09): DB seviyesinde visibility=public
    ZORUNLU DEĞİL artık — reels.py::reels() ile AYNI gerekçe, filter_visible()
    ile Python tarafında public/followers/close_friends uygulanır (kullanıcı
    kararı: takipçiye özel bir reel, izin verilen görüntüleyicilere reels
    akışında da görünmeli)."""
    sb = get_sb()
    me = request.api_user["id"]
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url, is_private, is_deactivated), "
                   "likes(count), comments(count)")

    try:
        posts = sb.table("posts").select(select_cols).eq(
            "is_reel", True
        ).not_.is_(
            "video_url", "null"
        ).eq("is_draft", False).eq("is_archived", False).order(
            "created_at", desc=True
        ).range(offset, offset + PAGE_SIZE).execute().data
    except Exception:
        posts = []

    visible_author_ids = followed_and_self_ids(sb, me)
    if posts:
        close_friend_ids = close_friend_author_ids(sb, me)
        posts = filter_visible(sb, posts, visible_author_ids, close_friend_ids, me)

        deactivated_ids = {
            p.get("user_id") for p in posts
            if p.get("profiles", {}).get("is_deactivated")
        }
        if deactivated_ids:
            posts = [p for p in posts if p.get("user_id") not in deactivated_ids]

    blocked_ids = blocked_user_ids(sb, me)
    posts = [p for p in posts if p.get("user_id") not in blocked_ids]

    has_more = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]

    _attach_post_metrics(sb, posts, me)

    return jsonify(posts=posts, has_more=has_more, page=page)
