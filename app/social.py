"""Sosyal etkileşimler: beğeni, yorum, takip."""
from flask import Blueprint, request, redirect, url_for, session, flash, abort, jsonify
from .decorators import login_required
from .supabase_client import get_sb

bp = Blueprint("social", __name__)


# ----------------------- BEĞENİ -----------------------

@bp.route("/like/<post_id>", methods=["POST"])
@login_required
def toggle_like(post_id):
    sb = get_sb()
    me = session["user"]["id"]

    existing = sb.table("likes").select("post_id").eq("post_id", post_id).eq("user_id", me).execute()
    if existing.data:
        sb.table("likes").delete().eq("post_id", post_id).eq("user_id", me).execute()
        liked = False
    else:
        sb.table("likes").insert({"post_id": post_id, "user_id": me}).execute()
        liked = True

    count = len(sb.table("likes").select("post_id").eq("post_id", post_id).execute().data)

    # JS'ten fetch ile gelen istekse JSON dön, normal form submit ise (JS kapalıysa) eskisi gibi redirect
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(liked=liked, count=count)
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- YORUM -----------------------

@bp.route("/comment/<post_id>", methods=["POST"])
@login_required
def add_comment(post_id):
    content = request.form.get("content", "").strip()
    if not content:
        flash("Boş yorum yapılamaz.", "error")
        return redirect(url_for("routes.post_detail", post_id=post_id))

    get_sb().table("comments").insert({
        "post_id": post_id,
        "user_id": session["user"]["id"],
        "content": content,
    }).execute()
    flash("Yorum eklendi.", "success")
    return redirect(url_for("routes.post_detail", post_id=post_id))


@bp.route("/comment/<comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    # Uygulama katmanı güvenliği: sadece kendi yorumunu sil
    get_sb().table("comments").delete().eq("id", comment_id).eq(
        "user_id", session["user"]["id"]
    ).execute()
    flash("Yorum silindi.", "success")
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- TAKİP -----------------------

@bp.route("/follow/<username>")
@login_required
def toggle_follow(username):
    sb = get_sb()
    me = session["user"]["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute()
    if not target.data:
        abort(404)
    target_id = target.data[0]["id"]

    if target_id == me:
        flash("Kendini takip edemezsin.", "error")
        return redirect(url_for("routes.profile", username=username))

    existing = sb.table("follows").select().eq("follower_id", me).eq(
        "following_id", target_id
    ).execute()
    if existing.data:
        sb.table("follows").delete().eq("follower_id", me).eq(
            "following_id", target_id
        ).execute()
        following = False
    else:
        sb.table("follows").insert({
            "follower_id": me, "following_id": target_id
        }).execute()
        following = True

    # AJAX isteği ise JSON dön, değilse redirect
    if request.headers.get("X-Requested-With") == "fetch":
        followers_count = len(sb.table("follows").select("follower_id").eq(
            "following_id", target_id
        ).execute().data)
        return jsonify(following=following, followers_count=followers_count)

    return redirect(url_for("routes.profile", username=username))