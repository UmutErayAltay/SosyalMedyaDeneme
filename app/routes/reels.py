"""Reels (dikey kısa video) akışı."""
from datetime import datetime, timezone
from flask import render_template, request, session
from . import bp
from ._common import _my_id, _attach_post_metrics, PAGE_SIZE
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids, close_friend_author_ids, filter_visible
from ..blocks import blocked_user_ids


@bp.route("/reels")
@login_required
@retry_on_connection_error
def reels():
    """Dikey video akışı: is_reel=true, video zorunlu, taslak/arşiv değil.
    Takip/engelleme/gizlilik filtreleri discover() ile AYNI `filter_visible()`
    helper'ı kullanır (2026-08-09 kullanıcı kararı) — ÖNCEDEN burada SADECE
    visibility=public postlar çekiliyordu, ama site politikası yeni postların
    varsayılanını "followers" yaptığından (bkz. posts.py create_post())
    kullanıcı elle "Herkese açık" seçmedikçe kendi reel'i KENDİ reels
    akışında bile hiç görünmüyordu. Artık takipçiye özel/yakın arkadaş
    postları da (izin verilen görüntüleyicilere) reels akışında görünür —
    sıralama yine basit: en yeni başta (kaydırma akışı, karma algoritma yok)."""
    sb = get_sb()
    me = _my_id()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    # Temel sorgu: is_reel=true, video_url not null, taslak/arşiv değil —
    # visibility filtresi DB seviyesinde DEĞİL, aşağıda filter_visible() ile
    # Python tarafında uygulanıyor (discover()'daki AYNI desen).
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
        # Fallback: is_reel migration'ı henüz uygulanmamışsa boş liste döner
        posts = []

    # Gizlilik filtresi (public/followers/close_friends + is_private) —
    # discover()'daki AYNI filter_visible() çağrısı.
    visible_author_ids = followed_and_self_ids(sb, me)
    if posts:
        close_friend_ids = close_friend_author_ids(sb, me)
        posts = filter_visible(sb, posts, visible_author_ids, close_friend_ids, me)

        # Deaktif kullanıcı filtresi — filter_visible() bunu KAPSAMAZ (sadece
        # visibility/is_private), ayrı tutuldu.
        deactivated_ids = {
            p.get("user_id") for p in posts
            if p.get("profiles", {}).get("is_deactivated")
        }
        if deactivated_ids:
            posts = [p for p in posts if p.get("user_id") not in deactivated_ids]

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
