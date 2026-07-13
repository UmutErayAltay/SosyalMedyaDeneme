"""Sosyal etkileşimler: beğeni, yorum, takip."""
from flask import Blueprint, request, redirect, url_for, session, flash, abort, jsonify, render_template
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .notifications import notify
from .mentions import notify_mentions
from .blocks import is_blocked_either_way

bp = Blueprint("social", __name__)

# Emoji reaksiyon türleri (likes.reaction_type) — bkz. sql/migration_reactions.sql
REACTIONS = {"like": "👍", "love": "❤️", "haha": "😂", "wow": "😮", "sad": "😢"}


# ----------------------- BEĞENİ / REAKSİYON -----------------------

@bp.route("/like/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_like(post_id):
    from .routes._common import _can_view_post

    sb = get_sb()
    me = session["user"]["id"]

    # Post erişimini kontrol et
    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        abort(404)

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
    from .routes._common import _can_view_post

    content = request.form.get("content", "").strip()
    sticker_id = request.form.get("sticker_id", "").strip()
    gif_url = request.form.get("gif_url", "").strip()

    # Sticker var mı kontrol et
    me = session["user"]
    sb = get_sb()

    # Post erişimini kontrol et
    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me["id"]):
        abort(404)
    if sticker_id:
        try:
            sticker = sb.table("stickers").select("id").eq("id", sticker_id).execute()
            if not sticker.data:
                sticker_id = None
        except Exception:
            sticker_id = None

    # GIF URL kontrolü — sadece Klipy'den kabul et
    if gif_url and not gif_url.startswith("https://static.klipy.com/"):
        gif_url = None

    # Content veya sticker/gif olmalı
    if not content and not sticker_id and not gif_url:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Boş yorum yapılamaz"), 400
        flash("Boş yorum yapılamaz.", "error")
        return redirect(url_for("routes.post_detail", post_id=post_id))

    # Insert data
    insert_data = {
        "post_id": post_id,
        "user_id": me["id"],
        "content": content,
    }
    try:
        # Opsiyonel kolonlar: sticker_id, gif_url
        data = dict(insert_data)
        if sticker_id:
            data["sticker_id"] = sticker_id
        if gif_url:
            data["gif_url"] = gif_url
        res = sb.table("comments").insert(data).execute()
    except Exception:
        res = sb.table("comments").insert(insert_data).execute()
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
    from .routes._common import _can_view_post

    sb = get_sb()
    me = session["user"]["id"]

    # Comment'ten post_id ve user_id'yi bul ve post erişimini kontrol et
    c = sb.table("comments").select("post_id, user_id").eq("id", comment_id).execute().data
    if not c:
        abort(404)
    post = sb.table("posts").select("*").eq("id", c[0]["post_id"]).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        abort(404)

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
        notify(sb, recipient_id=c[0]["user_id"], actor_id=me,
               type_="comment_like", post_id=c[0]["post_id"], comment_id=comment_id)

    count = len(sb.table("comment_likes").select("user_id").eq(
        "comment_id", comment_id
    ).execute().data)

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(liked=liked, count=count)
    return redirect(request.referrer or url_for("routes.feed"))


# ----------------------- YORUM EMOJİ TEPKİSİ -----------------------
# messaging/reactions.py react_message() ile birebir aynı toggle deseni,
# yorumlara uyarlanmış. comment_likes (♥) tablosundan AYRI bir katman.

@bp.route("/comment/<comment_id>/react", methods=["POST"])
@login_required
@retry_on_connection_error
def react_comment(comment_id):
    """Yoruma emoji tepkisi ekle/değiştir/sil (toggle).

    Request body: {"reaction": "❤️"}
    comment_reactions tablosu henüz oluşturulmadıysa 503 döner.
    """
    from .routes._common import _can_view_post

    sb = get_sb()
    me = session["user"]["id"]

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    reaction = data.get("reaction", "").strip()
    if not reaction:
        return jsonify({"error": "empty_reaction"}), 400

    c = sb.table("comments").select("id, user_id, post_id").eq("id", comment_id).execute()
    if not c.data:
        abort(404)  # Enumeration koruması
    comment_owner_id = c.data[0]["user_id"]
    post_id = c.data[0]["post_id"]

    # Post erişimini kontrol et
    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        abort(404)

    try:
        existing = sb.table("comment_reactions").select().eq(
            "comment_id", comment_id
        ).eq("user_id", me).execute()

        if existing.data:
            existing_reaction = existing.data[0].get("reaction")
            if existing_reaction == reaction:
                sb.table("comment_reactions").delete().eq(
                    "comment_id", comment_id
                ).eq("user_id", me).execute()
                return jsonify(ok=True, reaction=None), 200
            else:
                sb.table("comment_reactions").update({"reaction": reaction}).eq(
                    "comment_id", comment_id
                ).eq("user_id", me).execute()
                return jsonify(ok=True, reaction=reaction), 200
        else:
            sb.table("comment_reactions").insert({
                "comment_id": comment_id,
                "user_id": me,
                "reaction": reaction,
            }).execute()
            if comment_owner_id != me:
                try:
                    notify(sb, recipient_id=comment_owner_id, actor_id=me,
                           type_="comment_reaction", post_id=post_id, comment_id=comment_id)
                except Exception as notify_error:
                    # notify() başarısızlığı tepki eklemeyi engellemesin;
                    # kolon eksikse (migration uygulanmamışsa) sessizce yut
                    import sys
                    print(f"Bildirim hatası (ignored): {notify_error}", file=sys.stderr)
            return jsonify(ok=True, reaction=reaction), 201

    except Exception as e:
        if "comment_reactions" in str(e) or "does not exist" in str(e):
            return jsonify({"error": "feature_not_yet_active"}), 503
        # Başka hata: JSON döndür (HTML 500 değil) ama iç hata metnini
        # istemciye SIZDIRMA — detay sunucu loguna
        import sys
        print(f"react_comment hatası: {e}", file=sys.stderr)
        return jsonify({"error": "server_error"}), 500


# ----------------------- YORUM YANITLAMA -----------------------

@bp.route("/comment/<post_id>/reply/<parent_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def reply_comment(post_id, parent_id):
    from .routes._common import _can_view_post

    content = request.form.get("content", "").strip()
    sticker_id = request.form.get("sticker_id", "").strip()
    gif_url = request.form.get("gif_url", "").strip()

    me = session["user"]
    sb = get_sb()

    # Post erişimini kontrol et
    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me["id"]):
        abort(404)

    # Sticker var mı kontrol et
    if sticker_id:
        try:
            sticker = sb.table("stickers").select("id").eq("id", sticker_id).execute()
            if not sticker.data:
                sticker_id = None
        except Exception:
            sticker_id = None

    # GIF URL kontrolü — sadece Klipy'den kabul et
    if gif_url and not gif_url.startswith("https://static.klipy.com/"):
        gif_url = None

    # Content veya sticker/gif olmalı
    if not content and not sticker_id and not gif_url:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Boş yorum yapılamaz"), 400
        flash("Boş yorum yapılamaz.", "error")
        return redirect(url_for("routes.post_detail", post_id=post_id))

    # Insert data
    insert_data = {
        "post_id": post_id,
        "user_id": me["id"],
        "content": content,
        "parent_comment_id": parent_id,
    }
    try:
        # Opsiyonel kolonlar: sticker_id, gif_url
        data = dict(insert_data)
        if sticker_id:
            data["sticker_id"] = sticker_id
        if gif_url:
            data["gif_url"] = gif_url
        res = sb.table("comments").insert(data).execute()
    except Exception:
        res = sb.table("comments").insert(insert_data).execute()
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

    target = sb.table("profiles").select("id, is_private").eq("username", username).execute()
    if not target.data:
        abort(404)
    target_data = target.data[0]
    target_id = target_data["id"]
    is_private = target_data.get("is_private", False)

    if target_id == me:
        flash("Kendini takip edemezsin.", "error")
        return redirect(url_for("routes.profile", username=username))

    if is_blocked_either_way(sb, me, target_id):
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="blocked"), 403
        flash("Bu kullanıcıyı takip edemezsin.", "error")
        return redirect(url_for("routes.profile", username=username))

    existing = sb.table("follows").select("status").eq("follower_id", me).eq(
        "following_id", target_id
    ).execute()
    if existing.data:
        # Varsa sil (pending veya accepted, hangisi olursa olsun)
        sb.table("follows").delete().eq("follower_id", me).eq(
            "following_id", target_id
        ).execute()
        following = False
        is_pending = False
    else:
        # Yeni takip isteği
        if is_private:
            # Gizli profil: pending istek gönder
            sb.table("follows").insert({
                "follower_id": me, "following_id": target_id, "status": "pending"
            }).execute()
            following = False
            is_pending = True
            notify(sb, recipient_id=target_id, actor_id=me, type_="follow_request")
        else:
            # Herkese açık profil: direkt accepted
            sb.table("follows").insert({
                "follower_id": me, "following_id": target_id, "status": "accepted"
            }).execute()
            following = True
            is_pending = False
            notify(sb, recipient_id=target_id, actor_id=me, type_="follow")

    # AJAX isteği ise JSON dön, değilse redirect
    if request.headers.get("X-Requested-With") == "fetch":
        # followers_count: accepted olan takipçi sayısı (pending sayılmaz)
        followers_count = len(sb.table("follows").select("follower_id").eq(
            "following_id", target_id
        ).eq("status", "accepted").execute().data)
        return jsonify(following=following, followers_count=followers_count, is_pending=is_pending)

    return redirect(url_for("routes.profile", username=username))


# ----------------------- TAKIP İSTEKLERİ (özel profiller) -----------------------

@bp.route("/follow-requests")
@login_required
@retry_on_connection_error
def list_follow_requests():
    """Bekleyen (pending) takip istekleri listesi."""
    sb = get_sb()
    me = session["user"]["id"]

    requests = sb.table("follows").select(
        "follower_id, created_at, profiles!follows_follower_id_fkey(id, username, avatar_url, full_name)"
    ).eq("following_id", me).eq("status", "pending").order("created_at", desc=True).execute().data

    users = [r["profiles"] for r in requests if r.get("profiles")]
    for u in users:
        u["is_self"] = u["id"] == me

    return render_template("follow_requests.html", users=users, me=session.get("user"))


@bp.route("/follow-requests/<follower_id>/accept", methods=["POST"])
@login_required
@retry_on_connection_error
def accept_follow_request(follower_id):
    """Pending takip isteğini kabul et (sadece alıcı yapabilir)."""
    sb = get_sb()
    me = session["user"]["id"]

    # Sadece ALICI (me) kabul edebilir
    follow_req = sb.table("follows").select("status").eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()

    if not follow_req.data or follow_req.data[0].get("status") != "pending":
        abort(404)  # Enumeration önleme: geçersiz istek veya zaten accepted

    sb.table("follows").update({"status": "accepted"}).eq(
        "follower_id", follower_id
    ).eq("following_id", me).execute()

    notify(sb, recipient_id=follower_id, actor_id=me, type_="follow_accept")

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Takip isteği kabul edildi.", "success")
    return redirect(request.referrer or url_for("social.list_follow_requests"))


@bp.route("/follow-requests/<follower_id>/reject", methods=["POST"])
@login_required
@retry_on_connection_error
def reject_follow_request(follower_id):
    """Pending takip isteğini reddet (sadece alıcı yapabilir)."""
    sb = get_sb()
    me = session["user"]["id"]

    # Sadece ALICI (me) reddedebilir
    follow_req = sb.table("follows").select("status").eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()

    if not follow_req.data or follow_req.data[0].get("status") != "pending":
        abort(404)  # Enumeration önleme

    sb.table("follows").delete().eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Takip isteği reddedildi.", "success")
    return redirect(request.referrer or url_for("social.list_follow_requests"))


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
            collection_id = (request.get_json(silent=True) or {}).get("collection_id") or request.form.get("collection_id") or None
            try:
                # collection_id ile insert'i dene
                sb.table("bookmarks").insert({"post_id": post_id, "user_id": me, "collection_id": collection_id}).execute()
                bookmarked = True
            except Exception:
                # migration_bookmark_collections henüz uygulanmamışsa, collection_id kolonu olmayabilir
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


@bp.route("/collections")
@login_required
@retry_on_connection_error
def list_collections():
    """Kullanıcının kaydetme klasörlerini döner (id, name)."""
    sb = get_sb()
    me = session["user"]["id"]
    try:
        cols = sb.table("bookmark_collections").select("id, name").eq(
            "user_id", me).order("created_at").execute().data
    except Exception:
        # migration_bookmark_collections henüz uygulanmamışsa, boş liste dön
        cols = []
    return jsonify(collections=cols)


# ------------------- KAYDEDİLENLER KLASÖRLERİ (collections) -------------------
# Bookmarks'a ait, isteğe bağlı gruplama — bkz. sql/migration_bookmark_collections.sql.
# collection_id NULL = "Genel" (klasörsüz), ayrı bir "Genel" satırı YOK.

@bp.route("/collections/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_collection():
    sb = get_sb()
    me = session["user"]["id"]
    # bookmarks.js JSON gövde gönderir, kaydedilenler sayfasındaki form ise form-data
    _json = request.get_json(silent=True) or {}
    name = (_json.get("name") or request.form.get("name", "")).strip()[:40]
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    if not name:
        if is_fetch:
            return jsonify(error="Klasör adı boş olamaz."), 400
        flash("Klasör adı boş olamaz.", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    try:
        col = sb.table("bookmark_collections").insert({"user_id": me, "name": name}).execute().data[0]
    except Exception:
        if is_fetch:
            return jsonify(error="Klasör oluşturulamadı (özellik henüz aktif değil)."), 503
        flash("Klasör oluşturulamadı (özellik henüz aktif değil).", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    if is_fetch:
        return jsonify(id=col["id"], name=col["name"])
    return redirect(request.referrer or url_for("routes.feed"))


@bp.route("/collections/<collection_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_collection(collection_id):
    sb = get_sb()
    me = session["user"]["id"]
    # .eq("user_id", me) yetkisiz silmeyi engeller (RLS zaten aynısını yapar,
    # ama service-role client RLS'i bypass ettiği için burada da kontrol şart).
    sb.table("bookmark_collections").delete().eq("id", collection_id).eq("user_id", me).execute()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    return redirect(request.referrer or url_for("routes.feed"))


@bp.route("/bookmark/<post_id>/collection", methods=["POST"])
@login_required
@retry_on_connection_error
def set_bookmark_collection(post_id):
    sb = get_sb()
    me = session["user"]["id"]
    collection_id = request.form.get("collection_id") or None
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    try:
        sb.table("bookmarks").update({"collection_id": collection_id}).eq(
            "post_id", post_id).eq("user_id", me).execute()
    except Exception:
        if is_fetch:
            return jsonify(error="Taşınamadı."), 503
        flash("Taşınamadı (özellik henüz aktif değil).", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    if is_fetch:
        return jsonify(ok=True)
    return redirect(request.referrer or url_for("routes.feed"))