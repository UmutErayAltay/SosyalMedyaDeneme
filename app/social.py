"""Sosyal etkileşimler: beğeni, yorum, takip."""
from flask import Blueprint, request, redirect, url_for, session, flash, abort, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .notifications import notify
from .mentions import notify_mentions

bp = Blueprint("social", __name__)

# Emoji reaksiyon türleri (likes.reaction_type) — bkz. sql/migration_reactions.sql
REACTIONS = {"like": "👍", "love": "❤️", "haha": "😂", "wow": "😮", "sad": "😢"}


# ----------------------- BEĞENİ / REAKSİYON -----------------------

@bp.route("/like/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_like(post_id):
    sb = get_sb()
    me = session["user"]["id"]

    reaction = request.form.get("reaction") or request.args.get("reaction") or "like"
    if reaction not in REACTIONS:
        reaction = "like"

    existing = sb.table("likes").select("reaction_type").eq("post_id", post_id).eq("user_id", me).execute()
    if existing.data:
        current = existing.data[0].get("reaction_type") or "like"
        if current == reaction:
            # Aynı reaksiyona tekrar tıklandı → kaldır
            sb.table("likes").delete().eq("post_id", post_id).eq("user_id", me).execute()
            liked = False
            reaction = None
        else:
            # Farklı bir reaksiyon seçildi → değiştir
            sb.table("likes").update({"reaction_type": reaction}).eq(
                "post_id", post_id
            ).eq("user_id", me).execute()
            liked = True
    else:
        sb.table("likes").insert({
            "post_id": post_id, "user_id": me, "reaction_type": reaction
        }).execute()
        liked = True
        post = sb.table("posts").select("user_id").eq("id", post_id).execute().data
        if post:
            notify(sb, recipient_id=post[0]["user_id"], actor_id=me,
                   type_="like", post_id=post_id)

    count = len(sb.table("likes").select("post_id").eq("post_id", post_id).execute().data)

    # JS'ten fetch ile gelen istekse JSON dön, normal form submit ise (JS kapalıysa) eskisi gibi redirect
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(liked=liked, count=count, reaction=reaction)
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- YORUM -----------------------

@bp.route("/comment/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def add_comment(post_id):
    content = request.form.get("content", "").strip()
    if not content:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Boş yorum yapılamaz"), 400
        flash("Boş yorum yapılamaz.", "error")
        return redirect(url_for("routes.post_detail", post_id=post_id))

    me = session["user"]
    sb = get_sb()
    res = sb.table("comments").insert({
        "post_id": post_id,
        "user_id": me["id"],
        "content": content,
    }).execute()
    comment_id = res.data[0]["id"] if res.data else None

    post = sb.table("posts").select("user_id").eq("id", post_id).execute().data
    if post:
        notify(sb, recipient_id=post[0]["user_id"], actor_id=me["id"],
               type_="comment", post_id=post_id, comment_id=comment_id)
    notify_mentions(sb, actor_id=me["id"], content=content, post_id=post_id, comment_id=comment_id)

    # Profil bilgisini çek (avatar + username)
    prof = sb.table("profiles").select("username, avatar_url").eq("id", me["id"]).execute()
    prof_data = prof.data[0] if prof.data else {}

    # AJAX isteği ise JSON dön
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(
            id=comment_id,
            content=content,
            username=prof_data.get("username", me.get("email", "Sen")),
            avatar_url=prof_data.get("avatar_url"),
        )

    flash("Yorum eklendi.", "success")
    return redirect(url_for("routes.post_detail", post_id=post_id))


@bp.route("/comment/<comment_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_comment(comment_id):
    # Uygulama katmanı güvenliği: sadece kendi yorumunu sil
    get_sb().table("comments").delete().eq("id", comment_id).eq(
        "user_id", session["user"]["id"]
    ).execute()
    flash("Yorum silindi.", "success")
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- YORUM BEĞENME -----------------------

@bp.route("/comment/like/<comment_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_comment_like(comment_id):
    sb = get_sb()
    me = session["user"]["id"]

    existing = sb.table("comment_likes").select("user_id").eq(
        "comment_id", comment_id
    ).eq("user_id", me).execute()
    if existing.data:
        sb.table("comment_likes").delete().eq(
            "comment_id", comment_id
        ).eq("user_id", me).execute()
        liked = False
    else:
        sb.table("comment_likes").insert({
            "comment_id": comment_id, "user_id": me
        }).execute()
        liked = True
        c = sb.table("comments").select("user_id, post_id").eq("id", comment_id).execute().data
        if c:
            notify(sb, recipient_id=c[0]["user_id"], actor_id=me,
                   type_="comment_like", post_id=c[0]["post_id"], comment_id=comment_id)

    count = len(sb.table("comment_likes").select("user_id").eq(
        "comment_id", comment_id
    ).execute().data)

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(liked=liked, count=count)
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- YORUM YANITLAMA -----------------------

@bp.route("/comment/<post_id>/reply/<parent_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def reply_comment(post_id, parent_id):
    content = request.form.get("content", "").strip()
    if not content:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Boş yorum yapılamaz"), 400
        flash("Boş yorum yapılamaz.", "error")
        return redirect(url_for("routes.post_detail", post_id=post_id))

    me = session["user"]
    sb = get_sb()
    res = sb.table("comments").insert({
        "post_id": post_id,
        "user_id": me["id"],
        "content": content,
        "parent_comment_id": parent_id,
    }).execute()
    comment_id = res.data[0]["id"] if res.data else None

    parent = sb.table("comments").select("user_id").eq("id", parent_id).execute().data
    if parent:
        notify(sb, recipient_id=parent[0]["user_id"], actor_id=me["id"],
               type_="reply", post_id=post_id, comment_id=comment_id)
    notify_mentions(sb, actor_id=me["id"], content=content, post_id=post_id, comment_id=comment_id)

    prof = sb.table("profiles").select("username, avatar_url").eq("id", me["id"]).execute()
    prof_data = prof.data[0] if prof.data else {}

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(
            id=comment_id,
            content=content,
            parent_id=parent_id,
            username=prof_data.get("username", me.get("email", "Sen")),
            avatar_url=prof_data.get("avatar_url"),
        )

    flash("Yanıt eklendi.", "success")
    return redirect(url_for("routes.post_detail", post_id=post_id))


@bp.route("/follow/<username>", methods=["POST"])
@login_required
@retry_on_connection_error
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
        notify(sb, recipient_id=target_id, actor_id=me, type_="follow")

    # AJAX isteği ise JSON dön, değilse redirect
    if request.headers.get("X-Requested-With") == "fetch":
        followers_count = len(sb.table("follows").select("follower_id").eq(
            "following_id", target_id
        ).execute().data)
        return jsonify(following=following, followers_count=followers_count)

    return redirect(url_for("routes.profile", username=username))


# ----------------------- KAYDEDİLENLER (bookmarks) -----------------------
# Beğeni/takip gibi herkese açık değil — sadece sahibi görebilir (kişisel
# "sonra oku" listesi), bkz. sql/migration_bookmarks.sql RLS politikaları.

@bp.route("/bookmark/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_bookmark(post_id):
    sb = get_sb()
    me = session["user"]["id"]

    try:
        existing = sb.table("bookmarks").select("post_id").eq(
            "post_id", post_id
        ).eq("user_id", me).execute()
        if existing.data:
            sb.table("bookmarks").delete().eq("post_id", post_id).eq("user_id", me).execute()
            bookmarked = False
        else:
            sb.table("bookmarks").insert({"post_id": post_id, "user_id": me}).execute()
            bookmarked = True
    except Exception:
        # sql/migration_bookmarks.sql henüz uygulanmamış — sayfayı kırmak yerine
        # nazikçe "değişmedi" dön
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(bookmarked=False, error="Kaydetme özelliği henüz aktif değil."), 503
        flash("Kaydetme özelliği henüz aktif değil.", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(bookmarked=bookmarked)
    return redirect(request.referrer or url_for("routes.feed"))