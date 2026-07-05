"""Ana rotalar: feed, post paylaşma, profil, post detayı."""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image, upload_images
from .hashtags import sync_post_hashtags

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


PAGE_SIZE = 20


def _attach_post_metrics(sb, posts: list, me: str) -> None:
    """Postlara like_count / comment_count / liked_by_me / my_reaction /
    bookmarked_by_me ekler.

    Sayılar embedded count ile tek sorguda gelir; kullanıcıya özel alanlar için
    tüm postlar üzerinden tek birer IN sorgusu yapılır (N+1 önlenir).
    """
    post_ids = [p["id"] for p in posts]
    my_reactions: dict = {}
    my_bookmarks: set = set()
    if post_ids:
        my_reactions = {
            l["post_id"]: l.get("reaction_type") or "like"
            for l in sb.table("likes").select("post_id, reaction_type")
            .eq("user_id", me).in_("post_id", post_ids).execute().data
        }
        try:
            my_bookmarks = {
                b["post_id"] for b in sb.table("bookmarks").select("post_id")
                .eq("user_id", me).in_("post_id", post_ids).execute().data
            }
        except Exception:
            pass  # sql/migration_bookmarks.sql henüz uygulanmamışsa sessizce atla
    for p in posts:
        p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
        p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
        p["liked_by_me"] = p["id"] in my_reactions
        p["my_reaction"] = my_reactions.get(p["id"])
        p["bookmarked_by_me"] = p["id"] in my_bookmarks


@bp.route("/")
@login_required
@retry_on_connection_error
def feed():
    """Ana akış: postlar (yeni → eski) yazar + etkileşim sayılarıyla, sayfalı."""
    sb = get_sb()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    # Post + yazar + beğeni/yorum sayıları tek sorguda.
    # Bir fazla satır çekilir: sonraki sayfa var mı bilgisi için.
    posts = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), "
        "likes(count), comments(count)"
    ).order("created_at", desc=True).range(offset, offset + PAGE_SIZE).execute().data

    has_next = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]
    _attach_post_metrics(sb, posts, _my_id())

    return render_template("feed.html", posts=posts, me=session.get("user"),
                           page=page, has_next=has_next)


@bp.route("/post/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_post():
    content = request.form.get("content", "").strip()
    image_files = request.files.getlist("images")

    # En azından metin veya görsel olmalı
    valid_files = [f for f in image_files if f and f.filename]
    if not content and not valid_files:
        flash("Boş post paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    # Çoklu görsel yükle (maksimum 4)
    image_urls = []
    if valid_files:
        image_urls = upload_images(valid_files, folder="posts", max_count=4)
        if not image_urls:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))

    # Geriye dönük uyumluluk: image_url (ilk görsel) + image_urls (array)
    insert_data = {
        "user_id": _my_id(),
        "content": content,
        "image_urls": image_urls,
    }
    if image_urls:
        insert_data["image_url"] = image_urls[0]

    sb = get_sb()
    inserted = sb.table("posts").insert(insert_data).execute()
    post_id = inserted.data[0]["id"] if inserted.data else None
    if post_id and content:
        sync_post_hashtags(sb, post_id, content)

    flash("Post paylaşıldı.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/post/<post_id>")
@login_required
@retry_on_connection_error
def post_detail(post_id):
    sb = get_sb()
    res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), "
        "likes(count), comments(count)"
    ).eq("id", post_id).execute()
    if not res.data:
        abort(404)
    post = res.data[0]

    me = _my_id()
    _attach_post_metrics(sb, [post], me)

    # Yorumlar + beğeni sayıları tek sorguda
    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url), comment_likes(count)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

    # Benim beğendiğim yorumlar tek IN sorgusuyla (N+1 önlenir)
    comment_ids = [c["id"] for c in comments]
    my_comment_likes = set()
    if comment_ids:
        my_comment_likes = {
            l["comment_id"] for l in sb.table("comment_likes").select("comment_id")
            .eq("user_id", me).in_("comment_id", comment_ids).execute().data
        }
    for c in comments:
        c["like_count"] = c["comment_likes"][0]["count"] if c.get("comment_likes") else 0
        c["liked_by_me"] = c["id"] in my_comment_likes

    # Hiyerarşik yapı: ana yorumlar + cevaplar
    top_comments = [c for c in comments if not c.get("parent_comment_id")]
    for tc in top_comments:
        tc["replies"] = [c for c in comments if c.get("parent_comment_id") == tc["id"]]
    comments = top_comments

    return render_template("post_detail.html", post=post, comments=comments,
                           me=session.get("user"))


@bp.route("/post/<post_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_post(post_id):
    # Uygulama katmanı güvenliği: sadece kendi postunu sil
    get_sb().table("posts").delete().eq("id", post_id).eq(
        "user_id", _my_id()
    ).execute()
    flash("Post silindi.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/u/<username>")
@login_required
@retry_on_connection_error
def profile(username):
    sb = get_sb()
    prof = sb.table("profiles").select("*").eq("username", username).execute()
    if not prof.data:
        abort(404)
    prof = prof.data[0]

    me = _my_id()
    is_self = me == prof["id"]

    # Postlar + etkileşim sayıları tek sorguda
    posts = sb.table("posts").select(
        "*, likes(count), comments(count)"
    ).eq("user_id", prof["id"]).order("created_at", desc=True).execute().data
    _attach_post_metrics(sb, posts, me)

    # Medya sekmesi: görsel içeren postlar (ek sorgu yok, mevcut listeden süzülür)
    media_posts = [p for p in posts if p.get("image_urls") or p.get("image_url")]

    # Beğenilenler sekmesi: bu profilin beğendiği postlar (beğeni sırasına göre yeni→eski)
    liked_rows = sb.table("likes").select("post_id").eq(
        "user_id", prof["id"]
    ).order("created_at", desc=True).execute().data
    liked_ids = [l["post_id"] for l in liked_rows]
    liked_posts = []
    if liked_ids:
        liked_posts = sb.table("posts").select(
            "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
        ).in_("id", liked_ids).execute().data
        _attach_post_metrics(sb, liked_posts, me)
        # .in_() sırayı garanti etmez; beğeni sırasına (liked_ids) göre yeniden diz
        order = {pid: i for i, pid in enumerate(liked_ids)}
        liked_posts.sort(key=lambda p: order.get(p["id"], 0))

    # Kaydedilenler sekmesi: sadece kendi profilini görüntülerken (kişisel liste,
    # bkz. sql/migration_bookmarks.sql RLS — başkasının kaydettikleri görünmez)
    bookmarked_posts = []
    if is_self:
        try:
            bm_rows = sb.table("bookmarks").select("post_id").eq(
                "user_id", me
            ).order("created_at", desc=True).execute().data
            bm_ids = [b["post_id"] for b in bm_rows]
            if bm_ids:
                bookmarked_posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", bm_ids).execute().data
                _attach_post_metrics(sb, bookmarked_posts, me)
                bm_order = {pid: i for i, pid in enumerate(bm_ids)}
                bookmarked_posts.sort(key=lambda p: bm_order.get(p["id"], 0))
        except Exception:
            pass  # migration henüz uygulanmamışsa sekme boş görünür, sayfa kırılmaz

    # --- Profil istatistikleri (count='exact' ile satır çekmeden say) ---
    followers_count = sb.table("follows").select(
        "follower_id", count="exact", head=True
    ).eq("following_id", prof["id"]).execute().count or 0
    following_count = sb.table("follows").select(
        "following_id", count="exact", head=True
    ).eq("follower_id", prof["id"]).execute().count or 0
    # Toplam beğeni (kullanıcının tüm postlarına gelen)
    total_likes = sum(p["like_count"] for p in posts)

    is_following = False
    if not is_self:
        f = sb.table("follows").select().eq("follower_id", me).eq(
            "following_id", prof["id"]
        ).execute()
        is_following = bool(f.data)

    return render_template("profile.html", profile=prof, posts=posts,
                           media_posts=media_posts, liked_posts=liked_posts,
                           bookmarked_posts=bookmarked_posts,
                           is_self=is_self, is_following=is_following, me=session.get("user"),
                           stats={
                               "posts": len(posts),
                               "followers": followers_count,
                               "following": following_count,
                               "likes": total_likes,
                           })


def _follow_list(username: str, kind: str):
    """Takipçi ('followers') veya takip edilen ('following') listesi ortak render'ı."""
    sb = get_sb()
    prof = _profile(username=username)
    if not prof:
        abort(404)
    me = _my_id()

    if kind == "followers":
        rows = sb.table("follows").select(
            "profiles!follows_follower_id_fkey(id, username, avatar_url, full_name)"
        ).eq("following_id", prof["id"]).execute().data
        title = "Takipçiler"
    else:
        rows = sb.table("follows").select(
            "profiles!follows_following_id_fkey(id, username, avatar_url, full_name)"
        ).eq("follower_id", prof["id"]).execute().data
        title = "Takip Edilenler"

    users = [r["profiles"] for r in rows if r.get("profiles")]

    # Ben bu kullanıcıları takip ediyor muyum? (her satırdaki takip butonu için)
    user_ids = [u["id"] for u in users]
    following_ids = set()
    if user_ids:
        following_ids = {
            f["following_id"] for f in sb.table("follows").select("following_id")
            .eq("follower_id", me).in_("following_id", user_ids).execute().data
        }
    for u in users:
        u["is_following"] = u["id"] in following_ids
        u["is_self"] = u["id"] == me

    return render_template("follow_list.html", profile=prof, users=users,
                           title=title, kind=kind, me=session.get("user"))


@bp.route("/u/<username>/followers")
@login_required
@retry_on_connection_error
def followers_list(username):
    return _follow_list(username, "followers")


@bp.route("/u/<username>/following")
@login_required
@retry_on_connection_error
def following_list(username):
    return _follow_list(username, "following")


@bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
@retry_on_connection_error
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

        # Session'daki kullanıcı bilgilerini senkronize et (navbar avatarı için)
        session["user"]["username"] = username
        if "avatar_url" in update_data:
            session["user"]["avatar_url"] = update_data["avatar_url"]
        session.modified = True

        flash("Profil güncellendi.", "success")
        return redirect(url_for("routes.profile", username=username))

    # GET: mevcut profil bilgilerini göster
    prof = sb.table("profiles").select("*").eq("id", me).execute()
    if not prof.data:
        abort(404)
    return render_template("profile_edit.html", profile=prof.data[0], me=session["user"])


@bp.route("/search")
@login_required
@retry_on_connection_error
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return render_template("search.html", q=q, users=[], posts=[], me=session.get("user"))

    sb = get_sb()
    # Kullanıcı ara (username ILIKE)
    users = sb.table("profiles").select(
        "id, username, full_name, avatar_url"
    ).ilike("username", f"%{q}%").limit(20).execute().data

    # Post ara (content ILIKE) — beğeni/yorum sayıları feed ile aynı desende
    posts = sb.table("posts").select(
        "id, content, image_url, image_urls, created_at, user_id, "
        "profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).ilike("content", f"%{q}%").order("created_at", desc=True).limit(50).execute().data
    _attach_post_metrics(sb, posts, _my_id())

    return render_template("search.html", q=q, users=users, posts=posts, me=session.get("user"))