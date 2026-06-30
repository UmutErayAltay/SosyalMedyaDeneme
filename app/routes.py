"""Ana rotalar: feed, post paylaşma, profil, post detayı."""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb
from .storage_helper import upload_image

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
    image_file = request.files.get("image")

    # En azından metin veya görsel olmalı
    if not content and not (image_file and image_file.filename):
        flash("Boş post paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    # Görsel varsa yükle
    image_url = None
    if image_file and image_file.filename:
        image_url = upload_image(image_file, folder="posts")
        if not image_url:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))

    get_sb().table("posts").insert({
        "user_id": _my_id(),
        "content": content,
        "image_url": image_url,
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

    # beğeni sayısı + ben beğendim mi? (feed() ile aynı mantık)
    me = _my_id()
    like_res = sb.table("likes").select("user_id").eq("post_id", post_id).execute()
    post["like_count"] = len(like_res.data)
    post["liked_by_me"] = me in [l["user_id"] for l in like_res.data]

    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

    return render_template("post_detail.html", post=post, comments=comments,
                           me=session.get("user"))


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


@bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    sb = get_sb()
    me = _my_id()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        bio = request.form.get("bio", "").strip()
        username = request.form.get("username", "").strip()
        avatar_file = request.files.get("avatar")

        if not username or len(username) < 3:
            flash("Kullanıcı adı en az 3 karakter olmalı.", "error")
            return redirect(url_for("routes.profile_edit"))

        # Kullanıcı adı başkası tarafından kullanılıyor mu?
        if username != session["user"].get("username", ""):
            taken = sb.table("profiles").select("id").eq("username", username).neq(
                "id", me
            ).execute()
            if taken.data:
                flash("Bu kullanıcı adı zaten alınmış.", "error")
                return redirect(url_for("routes.profile_edit"))

        # Güncellenecek alanlar
        update_data = {"full_name": full_name or None, "bio": bio or None, "username": username}

        # Avatar yüklendiyse
        if avatar_file and avatar_file.filename:
            avatar_url = upload_image(avatar_file, folder="avatars")
            if avatar_url:
                update_data["avatar_url"] = avatar_url
            else:
                flash("Avatar yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
                return redirect(url_for("routes.profile_edit"))

        # Profili güncelle
        sb.table("profiles").update(update_data).eq("id", me).execute()

        # Session'daki username'i de güncelle
        session["user"]["username"] = username
        session.modified = True

        flash("Profil güncellendi.", "success")
        return redirect(url_for("routes.profile", username=username))

    # GET: mevcut profil bilgilerini göster
    prof = sb.table("profiles").select("*").eq("id", me).execute()
    if not prof.data:
        abort(404)
    return render_template("profile_edit.html", profile=prof.data[0])


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return render_template("search.html", q=q, users=[], posts=[])

    sb = get_sb()
    # Kullanıcı ara (username ILIKE)
    users = sb.table("profiles").select(
        "id, username, full_name, avatar_url"
    ).ilike("username", f"%{q}%").limit(20).execute().data

    # Post ara (content ILIKE)
    posts = sb.table("posts").select(
        "id, content, image_url, created_at, user_id, "
        "profiles!posts_user_id_fkey(username, avatar_url)"
    ).ilike("content", f"%{q}%").order("created_at", desc=True).limit(50).execute().data

    return render_template("search.html", q=q, users=users, posts=posts)