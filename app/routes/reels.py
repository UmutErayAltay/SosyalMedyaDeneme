"""Reels (dikey kısa video) akışı."""
from datetime import datetime, timezone
from flask import render_template, request, session
from . import bp
from ._common import _my_id, _attach_post_metrics, PAGE_SIZE
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids
from ..blocks import blocked_user_ids


@bp.route("/reels")
@login_required
@retry_on_connection_error
def reels():
    """Dikey video akışı: herkese açık, is_reel=true, video zorunlu, taslak/arşiv değil.
    Takip/engelleme/gizlilik filtreleri discover() gibi uygulanır, ama sıralama basit:
    en yeni başta (kaydırma akışı, karma algoritma yok)."""
    sb = get_sb()
    me = _my_id()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    # Temel sorgu: public, is_reel=true, video_url not null, taslak/arşiv değil
    select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url), "
                   "likes(count), comments(count)")

    try:
        posts = sb.table("posts").select(select_cols).eq(
            "visibility", "public"
        ).eq("is_reel", True).not_.is_(
            "video_url", "null"
        ).eq("is_draft", False).eq("is_archived", False).order(
            "created_at", desc=True
        ).range(offset, offset + PAGE_SIZE).execute().data
    except Exception:
        # Fallback: is_reel migration'ı henüz uygulanmamışsa boş liste döner
        posts = []

    # Gizli profil filtreleri (discover() deseni)
    visible_author_ids = followed_and_self_ids(sb, me)
    if posts:
        author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
        is_private_map = {}
        if author_ids:
            try:
                profiles = sb.table("profiles").select("id, is_private").in_(
                    "id", list(author_ids)
                ).execute().data
                is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
            except Exception:
                pass
        posts = [p for p in posts if not (
            is_private_map.get(p.get("user_id"), False) and
            p.get("user_id") != me and
            p.get("user_id") not in visible_author_ids
        )]

    # Engelleme filtresi
    blocked_ids = blocked_user_ids(sb, me)
    posts = [p for p in posts if p.get("user_id") not in blocked_ids]

    # has_more kontrol ve PAGE_SIZE kesme (feed deseni)
    has_more = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]

    # Metrikleri ekle (anket yok reels'te)
    _attach_post_metrics(sb, posts, me)

    # Valid usernames ve template render
    valid_usernames = get_valid_usernames(sb)

    return render_template(
        "reels.html", posts=posts, me=session.get("user"),
        page=page, has_more=has_more, valid_usernames=valid_usernames
    )
