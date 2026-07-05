"""Arama ve algoritmik keşfet."""
from datetime import datetime, timedelta, timezone
from flask import render_template, request, session
from . import bp
from ._common import _my_id, _attach_post_metrics
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids, filter_visible
from ..blocks import blocked_user_ids, filter_not_blocked
from ..polls import attach_polls


@bp.route("/search")
@login_required
@retry_on_connection_error
def search():
    q = request.args.get("q", "").strip()
    sb = get_sb()
    if len(q) < 2:
        return render_template("search.html", q=q, users=[], posts=[], me=session.get("user"),
                               valid_usernames=get_valid_usernames(sb))

    me = _my_id()
    blocked_ids = blocked_user_ids(sb, me)

    # Kullanıcı ara (username ILIKE)
    users = sb.table("profiles").select(
        "id, username, full_name, avatar_url"
    ).ilike("username", f"%{q}%").limit(20).execute().data
    users = [u for u in users if u["id"] not in blocked_ids]

    # Post ara (content ILIKE) — beğeni/yorum sayıları feed ile aynı desende.
    # "*" kullanılıyor (açık kolon listesi değil) çünkü visibility/video_url
    # gibi opsiyonel kolonlar henüz migration'ı çalıştırılmamışsa bile PostgREST
    # hata vermez — açık isimle istenen var olmayan bir kolon HATA verirdi.
    posts = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).ilike("content", f"%{q}%").order("created_at", desc=True).limit(50).execute().data
    posts = [p for p in posts if not p.get("is_draft")]  # taslaklar aramada görünmez
    posts = filter_visible(posts, followed_and_self_ids(sb, me))
    posts = filter_not_blocked(posts, blocked_ids)
    _attach_post_metrics(sb, posts, me)
    attach_polls(sb, posts, me)

    return render_template("search.html", q=q, users=users, posts=posts, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb))


@bp.route("/kesfet")
@login_required
@retry_on_connection_error
def discover():
    """Algoritmik keşfet: takip ETMEDİĞİN kişilerin son 7 gündeki HERKESE AÇIK
    postlarından beğeni+yorum toplamına göre en popüler ~20'si. Gündem
    (trending hashtag) sayfasının post versiyonu — burada da engelleme
    ilişkileri viewer'a özel süzülür ama gündem/keşfet listesi kişiselleştirme
    açısından basit tutuldu (takip grafiği dışında bir öneri algoritması yok)."""
    sb = get_sb()
    me = _my_id()

    exclude_ids = followed_and_self_ids(sb, me)  # ben + zaten takip ettiklerim — hariç tutulur
    blocked_ids = blocked_user_ids(sb, me)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url), "
                   "likes(count), comments(count)")
    try:
        posts = sb.table("posts").select(select_cols).gte(
            "created_at", cutoff
        ).eq("visibility", "public").eq("is_draft", False).execute().data
    except Exception:
        posts = sb.table("posts").select(select_cols).gte("created_at", cutoff).execute().data

    posts = [p for p in posts if p["user_id"] not in exclude_ids]
    posts = filter_not_blocked(posts, blocked_ids)
    _attach_post_metrics(sb, posts, me)
    attach_polls(sb, posts, me)

    for p in posts:
        p["_score"] = (p.get("like_count") or 0) + (p.get("comment_count") or 0)
    posts.sort(key=lambda p: p["_score"], reverse=True)
    posts = posts[:20]

    return render_template("discover.html", posts=posts, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb))
