"""Ana rotalar: feed, post paylaşma, profil, post detayı."""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb

bp = Blueprint("routes", __name__)


def _profile(username: str | None = None, uid: str | None = None) -> dict | None:
    """Verilen username veya id ile profil döndürür."""
    sb = get_sb()
    if uid:
        res = sb.table("profiles").select("*").eq("id", uid).execute()
    elif username:
        res = sb.table("profiles").select("*").eq("username", username).execute()
    else:
        return None
    return res.data[0] if res.data else None


def _my_id() -> str:
    return session["user"]["id"]


@bp.route("/")
@login_required
def feed():
    """Ana akış: tüm postları (yeni → eski) yazarı + etkileşim sayılarıyla getir."""
    sb = get_sb()

    # post + yazar profili
    posts = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url)"
    ).order("created_at", desc=True).limit(50).execute().data

    me = _my_id()
    for p in posts:
        # beğeni sayısı + ben beğendim mi?
        like_res = sb.table("likes").select("user_id").eq("post_id", p["id"]).execute()
        p["like_count"] = len(like_res.data)
        p["liked_by_me"] = me in [l["user_id"] for l in like_res.data]

    return render_template("feed.html", posts=posts, me=session.get("user"))


@bp.route("/post/new", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    if not content:
        flash("Boş post paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    get_sb().table("posts").insert({
        "user_id": _my_id(),
        "content": content,
    }).execute()
    flash("Post paylaşıldı.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/post/<post_id>")
@login_required
def post_detail(post_id):
    sb = get_sb()
    res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url)"
    ).eq("id", post_id).execute()
    if not res.data:
        abort(404)
    post = res.data[0]

    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

    return render_template("post_detail.html", post=post, comments=comments)


@bp.route("/post/<post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    # Uygulama katmanı güvenliği: sadece kendi postunu sil
    get_sb().table("posts").delete().eq("id", post_id).eq(
        "user_id", _my_id()
    ).execute()
    flash("Post silindi.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/u/<username>")
@login_required
def profile(username):
    sb = get_sb()
    prof = sb.table("profiles").select("*").eq("username", username).execute()
    if not prof.data:
        abort(404)
    prof = prof.data[0]

    posts = sb.table("posts").select("*, created_at").eq(
        "user_id", prof["id"]
    ).order("created_at", desc=True).execute().data

    me = _my_id()
    is_self = me == prof["id"]
    is_following = False
    if not is_self:
        f = sb.table("follows").select().eq("follower_id", me).eq(
            "following_id", prof["id"]
        ).execute()
        is_following = bool(f.data)

    return render_template("profile.html", profile=prof, posts=posts,
                           is_self=is_self, is_following=is_following)
