from flask import request, jsonify

from . import bp
from ._common import _str_field, api_login_required
from ..supabase_client import get_sb, _CONNECTION_ERRORS
from ..routes._common import _attach_post_metrics, _can_view_post
from ..polls import attach_polls
from ..notifications import notify
from ..mentions import notify_mentions
from ..cache import invalidate
from ..social import REACTIONS, _find_recent_duplicate_comment, _notify_pool
from ..post_views import record_view
from ..storage_helper import upload_image, upload_video
from ..hashtags import sync_post_hashtags, notify_hashtag_followers, extract_hashtags


# ----------------------- ETKİLEŞİMLER: BEĞENİ + YORUM (Faz 4, native Android) -----------------------
# app/social.py toggle_like()/add_comment()/reply_comment() ve app/routes/posts.py
# post_detail()'in BİLİNÇLİ bir alt kümesi JSON'a taşınır. KAPSAM DIŞI (bu turda
# hiç dokunulmadı): sticker/gif yorum, yorum düzenleme/silme/tepki (comment_reactions),
# yorum beğenme (comment_likes) — SADECE post beğenme + metin yorumu (+yanıt) var.

@bp.route("/posts/<post_id>/like", methods=["POST"])
@api_login_required
def api_toggle_like(post_id):
    """Post beğen/beğenmekten vazgeç — social.py toggle_like()'ın AYNI mantığı
    (reaksiyon değiştirme dahil), sadece JSON döner (redirect dalı yok)."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        return jsonify(error="not_found"), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    reaction = _str_field(data, "reaction") or "like"
    if reaction not in REACTIONS:
        reaction = "like"

    existing = sb.table("likes").select("reaction_type").eq("post_id", post_id).eq("user_id", me).execute()
    if existing.data:
        current = existing.data[0].get("reaction_type") or "like"
        if current == reaction:
            sb.table("likes").delete().eq("post_id", post_id).eq("user_id", me).execute()
            liked = False
            reaction = None
        else:
            sb.table("likes").update({"reaction_type": reaction}).eq(
                "post_id", post_id
            ).eq("user_id", me).execute()
            liked = True
    else:
        sb.table("likes").insert({
            "post_id": post_id, "user_id": me, "reaction_type": reaction
        }).execute()
        liked = True
        notify(sb, recipient_id=post[0]["user_id"], actor_id=me, type_="like", post_id=post_id)

    count = len(sb.table("likes").select("post_id").eq("post_id", post_id).execute().data)
    return jsonify(liked=liked, reaction=reaction, count=count)


def _serialize_comment(c: dict) -> dict:
    """post_detail()'daki yorum enrichment'ının (like_count/liked_by_me/
    sticker/reactions/replies) AYNI şeklini JSON'a taşır — burada ayrı bir
    serialization İCAT edilmedi, sadece Python dict'i olduğu gibi jsonify'a girer."""
    return c


@bp.route("/posts/<post_id>")
@api_login_required
def api_post_detail(post_id):
    """Tek bir post + hiyerarşik yorum listesi — posts.py post_detail()'in
    AYNI okuma mantığı (yazma/form işleme YOK, o ayrı endpoint'lerde).
    Sticker/gif YORUM oluşturma bu iterasyonda kapsam dışı ama VAR OLAN
    sticker/gif'li yorumlar görüntüleme için JSON'da GÖSTERİLİR (veri
    kaybı olmasın, native şimdilik render etmeyecek olsa da)."""
    sb = get_sb()
    me = request.api_user["id"]

    res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).eq("id", post_id).execute()
    if not res.data:
        return jsonify(error="not_found"), 404
    post = res.data[0]

    if post.get("is_draft") and post["user_id"] != me:
        return jsonify(error="not_found"), 404
    if not _can_view_post(sb, post, me):
        return jsonify(error="not_found"), 404

    record_view(sb, post_id, me, post["user_id"])

    _attach_post_metrics(sb, [post], me)
    attach_polls(sb, [post], me)

    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url), comment_likes(count)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

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

    try:
        sticker_ids = [c["sticker_id"] for c in comments if c.get("sticker_id")]
        stickers_by_id = {}
        if sticker_ids:
            for s in sb.table("stickers").select("id, image_url").in_("id", sticker_ids).execute().data:
                stickers_by_id[s["id"]] = {"id": s["id"], "image_url": s["image_url"]}
        for c in comments:
            c["sticker"] = stickers_by_id.get(c.get("sticker_id"))
    except Exception:
        for c in comments:
            c["sticker"] = None

    try:
        reactions_by_comment = {}
        if comment_ids:
            rows = sb.table("comment_reactions").select("comment_id, user_id, reaction") \
                .in_("comment_id", comment_ids).execute().data
            for r in rows:
                bucket = reactions_by_comment.setdefault(r["comment_id"], {})
                bucket.setdefault(r["reaction"], []).append(r["user_id"])
        for c in comments:
            grouped = reactions_by_comment.get(c["id"], {})
            c["reactions"] = [
                {"reaction": emoji, "count": len(users), "mine": me in users}
                for emoji, users in grouped.items()
            ]
    except Exception:
        for c in comments:
            c["reactions"] = []

    top_comments = [c for c in comments if not c.get("parent_comment_id")]
    for tc in top_comments:
        tc["replies"] = [_serialize_comment(c) for c in comments if c.get("parent_comment_id") == tc["id"]]

    return jsonify(post=post, comments=[_serialize_comment(c) for c in top_comments])


@bp.route("/posts/<post_id>/comments", methods=["POST"])
@api_login_required
def api_add_comment(post_id):
    """Yorum ekle (+ isteğe bağlı yanıt) — social.py add_comment()/
    reply_comment()'in SADECE metin dalı (sticker/gif İCAT edilmedi, bu
    iterasyonun kapsamı dışı). body'de `parent_comment_id` varsa yanıt,
    yoksa ana yorum — hangi bildirim türünün ('comment' vs 'reply')
    gönderileceği buna göre seçilir, kaynak fonksiyonlarla AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        return jsonify(error="not_found"), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    content = _str_field(data, "content")
    parent_comment_id = _str_field(data, "parent_comment_id") or None

    if not content:
        return jsonify(error="empty"), 400

    if parent_comment_id:
        # reply_comment()'daki AYNI doğrulama: farklı posttansa/mevcut değilse
        # yanıtsız (ana yorum gibi) gönder, hata değil.
        parent = sb.table("comments").select("id").eq("id", parent_comment_id).eq(
            "post_id", post_id
        ).execute().data
        if not parent:
            parent_comment_id = None

    dup = _find_recent_duplicate_comment(sb, post_id, me, content, None, None, parent_id=parent_comment_id)
    if dup:
        comment_row = dup
    else:
        insert_data = {"post_id": post_id, "user_id": me, "content": content}
        if parent_comment_id:
            insert_data["parent_comment_id"] = parent_comment_id
        try:
            res = sb.table("comments").insert(insert_data).execute()
        except _CONNECTION_ERRORS:
            raise  # dıştaki katman yeniden dener, dup kontrolü idempotent devam ettirir
        except Exception:
            # parent_comment_id kolonu henüz yoksa (migration öncesi) onsuz dene
            res = sb.table("comments").insert({
                "post_id": post_id, "user_id": me, "content": content
            }).execute()
        comment_row = res.data[0] if res.data else {}

    comment_id = comment_row.get("id")

    def _bg_notify():
        try:
            if parent_comment_id:
                parent_row = sb.table("comments").select("user_id").eq("id", parent_comment_id).execute().data
                if parent_row:
                    notify(sb, recipient_id=parent_row[0]["user_id"], actor_id=me,
                           type_="reply", post_id=post_id, comment_id=comment_id)
            else:
                notify(sb, recipient_id=post[0]["user_id"], actor_id=me,
                       type_="comment", post_id=post_id, comment_id=comment_id)
            notify_mentions(sb, actor_id=me, content=content, post_id=post_id, comment_id=comment_id)
        except Exception:
            pass

    _notify_pool.submit(_bg_notify)

    prof = sb.table("profiles").select("username, avatar_url").eq("id", me).execute()
    prof_data = prof.data[0] if prof.data else {}

    return jsonify(comment={
        "id": comment_id,
        "content": content,
        "parent_comment_id": parent_comment_id,
        "user_id": me,
        "created_at": comment_row.get("created_at"),
        "profiles": {"username": prof_data.get("username"), "avatar_url": prof_data.get("avatar_url")},
        "like_count": 0,
        "liked_by_me": False,
        "reactions": [],
        "sticker": None,
    })


# ----------------------- POST OLUŞTURMA (Faz 4, native Android) -----------------------
# app/routes/posts.py create_post()'un BİLİNÇLİ bir alt kümesi: metin + TEK
# görsel + TEK video/reel + görünürlük. KAPSAM DIŞI (bu turda hiç dokunulmadı):
# çoklu görsel (max 4), GIF, anket, taslak/planlama, konum.

@bp.route("/posts", methods=["POST"])
@api_login_required
def api_create_post():
    """Yeni post oluştur — multipart/form-data (web formuyla AYNI kodlama,
    JSON DEĞİL, çünkü görsel/video dosyası içeriyor). Alanlar: `content`
    (metin), `visibility` (public/followers/close_friends, varsayılan
    public), `image` (opsiyonel, TEK dosya — create_post()'daki `images`
    çoklu alanının BİLİNÇLİ olarak tekil hali), `video` (opsiyonel, TEK
    dosya) + `is_reel` ("true"/"false" — native her zaman açık gönderir,
    web'in checkbox `"on"`'undan FARKLI, bkz. settings.py _bool_form_field).

    create_post()'daki AYNI kural: is_reel=true iken video YOKSA
    `reel_requires_video` hatası döner. Reels sekmesi (api_v1/reels.py)
    is_reel=True + video_url IS NOT NULL + visibility=public filtresiyle
    ZATEN doğru postları buluyor — burada SADECE video_url/is_reel eklemek
    yeterli, reels.py'ye dokunulmadı.
    """
    sb = get_sb()
    me = request.api_user["id"]

    content = (request.form.get("content") or "").strip()
    image_file = request.files.get("image")
    has_image = bool(image_file and image_file.filename)
    video_file = request.files.get("video")
    has_video = bool(video_file and video_file.filename)
    is_reel = request.form.get("is_reel") == "true"

    if not content and not has_image and not has_video:
        return jsonify(error="empty"), 400

    if is_reel and not has_video:
        return jsonify(error="reel_requires_video"), 400

    visibility = request.form.get("visibility", "public")
    if visibility not in ("public", "followers", "close_friends"):
        visibility = "public"

    image_urls = []
    if has_image:
        image_url = upload_image(image_file, folder="posts")
        if not image_url:
            return jsonify(error="upload_failed"), 400
        image_urls = [image_url]

    video_url = None
    if has_video:
        video_url = upload_video(video_file, folder="posts")
        if not video_url:
            return jsonify(error="video_upload_failed"), 400

    insert_data = {"user_id": me, "content": content, "image_urls": image_urls}
    if image_urls:
        insert_data["image_url"] = image_urls[0]

    try:
        full_data = {**insert_data, "visibility": visibility, "is_draft": False}
        if video_url:
            full_data["video_url"] = video_url
        if is_reel:
            full_data["is_reel"] = is_reel
        inserted = sb.table("posts").insert(full_data).execute()
    except Exception:
        inserted = sb.table("posts").insert(insert_data).execute()

    post_id = inserted.data[0]["id"] if inserted.data else None
    if not post_id:
        return jsonify(error="create_failed"), 500

    # create_post()'daki AYNI yayın-sonrası işler — sadece BAŞARIYLA
    # yayınlanan (taslak olmayan) bir post için (bu endpoint hiç taslak
    # üretmiyor, HER ZAMAN yayınlanır).
    invalidate(f"sidebar:{me}")
    if content:
        sync_post_hashtags(sb, post_id, content)
        invalidate("trending:")
        notify_mentions(sb, actor_id=me, content=content, post_id=post_id)
        notify_hashtag_followers(sb, actor_id=me, post_id=post_id, tags=extract_hashtags(content))

    post_res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).eq("id", post_id).execute()
    post = post_res.data[0] if post_res.data else {"id": post_id, **insert_data}
    _attach_post_metrics(sb, [post], me)

    return jsonify(post=post)
