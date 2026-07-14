"""Hikaye (Story): 24 saatte kaybolan ephemeral paylaşım.

Kapsam BİLİNÇLİ OLARAK dar tutuldu (bkz. .context/active_context.md):
tek görsel/video + opsiyonel altyazı, sadece görüntüleme + kendi hikayeni
silme. Yorum/beğeni/"kim gördü" listesi YOK — bunlar normal postlarda zaten
var, hikayeyi ayrı bir "hafif ve hızlı" özellik olarak tutmak için kapsam
dışı bırakıldı.
"""
from datetime import datetime, timezone
from flask import Blueprint, request, redirect, url_for, session, flash, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image, upload_video
from .blocks import is_blocked_either_way
from .messaging._common import _get_or_create_conversation, _notify_conversation
from .polls import create_poll

bp = Blueprint("stories", __name__)

_last_cleanup = 0.0


def _cleanup_expired_stories(sb) -> None:
    """Süresi dolmuş (expires_at geçmiş) hikayeleri siler.

    Ayrı bir cron/scheduler YOK (bu projede hiç yok, bkz. notifications.py
    RETENTION_DAYS deseni) — feed her ziyaret edildiğinde fırsatçı temizlik
    yeterli, bu ölçekte (arkadaş grubu) hikayelerin saatlerce/günlerce
    görüntülenmeden birikmesi olası değil. En fazla 10 dakikada bir çalışması
    için throttle'lenir (cleanup DDL çalışması pahalı)."""
    global _last_cleanup
    import time
    now_ts = time.time()
    if now_ts - _last_cleanup < 600:
        return
    _last_cleanup = now_ts

    now = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("stories").delete().lt("expires_at", now).execute()
    except Exception:
        pass  # sql/migration_stories.sql henüz uygulanmamış olabilir


def attach_story_poll(sb, story: dict, me: str) -> None:
    """Tekil bir hikayeye anket verisini ekler (varsa).

    story["poll"] = {"id": ..., "options": [...], "total_votes": ..., "my_vote": ...,
                     "position_x": ..., "position_y": ..., "scale": ...} benzeri.
    """
    story["poll"] = None
    if not story.get("id"):
        return

    try:
        poll = sb.table("polls").select("id, position_x, position_y, scale").eq("story_id", story["id"]).execute().data
        if not poll:
            return
        poll_id = poll[0]["id"]
        position_x = poll[0].get("position_x", 0.5)
        position_y = poll[0].get("position_y", 0.75)
        scale = poll[0].get("scale", 1.0)

        options = sb.table("poll_options").select("id, option_text, position").eq(
            "poll_id", poll_id
        ).order("position").execute().data

        votes = sb.table("poll_votes").select("poll_id, option_id, user_id").eq(
            "poll_id", poll_id
        ).execute().data

        counts: dict = {}
        my_vote = None
        for v in votes:
            counts[v["option_id"]] = counts.get(v["option_id"], 0) + 1
            if v["user_id"] == me:
                my_vote = v["option_id"]

        total = sum(counts.values())
        opt_list = [{
            "id": o["id"], "text": o["option_text"],
            "votes": counts.get(o["id"], 0),
            "pct": round((counts.get(o["id"], 0) / total) * 100) if total else 0,
        } for o in options]

        story["poll"] = {
            "id": poll_id, "options": opt_list, "total_votes": total, "my_vote": my_vote,
            "position_x": position_x, "position_y": position_y, "scale": scale,
        }
    except Exception:
        pass


def active_stories_bar(sb, me: str, blocked_ids: set) -> list[dict]:
    """Feed'in üstündeki hikaye çubuğu için aktif hikayeleri kullanıcıya göre
    gruplar. Her grup: en yeni hikaye zamanı + "hepsi görüldü mü" bayrağı
    (halka rengini belirlemek için — görülmemiş varsa renkli, hepsi
    görülmüşse gri halka, Instagram deseni)."""
    _cleanup_expired_stories(sb)
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = sb.table("stories").select(
            "id, user_id, created_at, profiles!stories_user_id_fkey(username, avatar_url)"
        ).gt("expires_at", now).order("created_at", desc=True).execute().data
    except Exception:
        return []  # migration henüz uygulanmamışsa çubuk boş görünür, feed kırılmaz

    rows = [r for r in rows if r["user_id"] not in blocked_ids]
    if not rows:
        return []

    story_ids = [r["id"] for r in rows]
    my_viewed = set()
    try:
        my_viewed = {v["story_id"] for v in sb.table("story_views").select("story_id")
                     .eq("user_id", me).in_("story_id", story_ids).execute().data}
    except Exception:
        pass

    grouped: dict = {}
    order: list = []
    for r in rows:
        uid = r["user_id"]
        if uid not in grouped:
            prof = r.get("profiles") or {}
            grouped[uid] = {
                "user_id": uid,
                "username": prof.get("username", "Bilinmeyen"),
                "avatar_url": prof.get("avatar_url"),
                "all_seen": True,
            }
            order.append(uid)
        if r["id"] not in my_viewed:
            grouped[uid]["all_seen"] = False

    result = [grouped[uid] for uid in order]
    # Kendi hikayen varsa listenin en başına al (Instagram deseni)
    result.sort(key=lambda g: 0 if g["user_id"] == me else 1)
    return result


@bp.route("/stories/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_story():
    sb = get_sb()
    me = session["user"]["id"]
    caption = request.form.get("caption", "").strip()
    image_file = request.files.get("image")
    video_file = request.files.get("video")
    has_image = bool(image_file and image_file.filename)
    has_video = bool(video_file and video_file.filename)

    # Anket seçenekleri (post'taki same pattern: poll_option_1 .. poll_option_4)
    poll_options_raw = [request.form.get(f"poll_option_{i}", "").strip() for i in range(1, 5)]
    poll_options = [o for o in poll_options_raw if o]
    has_poll = len(poll_options) >= 2

    # Hikaye anketinin sürükle-bırak pozisyon ve boyut değerleri
    poll_position_x = 0.5
    poll_position_y = 0.75
    poll_scale = 1.0
    if has_poll:
        try:
            x_raw = request.form.get("poll_position_x", "0.5")
            poll_position_x = float(x_raw)
            if not (0 <= poll_position_x <= 1):
                poll_position_x = 0.5
        except ValueError:
            poll_position_x = 0.5

        try:
            y_raw = request.form.get("poll_position_y", "0.75")
            poll_position_y = float(y_raw)
            if not (0 <= poll_position_y <= 1):
                poll_position_y = 0.75
        except ValueError:
            poll_position_y = 0.75

        try:
            scale_raw = request.form.get("poll_scale", "1.0")
            poll_scale = float(scale_raw)
            if not (0.3 <= poll_scale <= 3):
                poll_scale = 1.0
        except ValueError:
            poll_scale = 1.0

    if not caption and not has_image and not has_video and not has_poll:
        flash("Boş hikaye paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    # Hikaye tek medyalı (post'un aksine) — basitlik için: görsel + video
    # aynı anda desteklenmiyor, ilk bulunan kullanılır.
    image_url = None
    video_url = None
    if has_image:
        image_url = upload_image(image_file, folder="stories")
        if not image_url:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))
    elif has_video:
        video_url = upload_video(video_file, folder="stories")
        if not video_url:
            flash("Video yüklenemedi (geçersiz format veya 25MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))

    try:
        story_data = {
            "user_id": me, "image_url": image_url, "video_url": video_url, "caption": caption,
        }
        result = sb.table("stories").insert(story_data).execute()
        story_id = result.data[0]["id"] if result.data else None

        if story_id and has_poll:
            create_poll(sb, poll_options, story_id=story_id,
                       position_x=poll_position_x, position_y=poll_position_y, scale=poll_scale)
    except Exception:
        flash("Hikaye paylaşılamadı (özellik henüz aktif değil).", "error")
        return redirect(url_for("routes.feed"))

    flash("Hikaye paylaşıldı.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/stories/user/<user_id>")
@login_required
@retry_on_connection_error
def user_stories(user_id):
    """Bir kullanıcının aktif hikayelerini JSON döner (hikaye görüntüleyici
    modalı için) — kendi hikayen DEĞİLSE görüntülenince story_views'e
    işlenir (halka rengi için). Her hikayeye (varsa) anket verisini ekler."""
    sb = get_sb()
    me = session["user"]["id"]

    # Engelleme kontrolü: harita yönde olursa olsun bir engelleme varsa boş liste dön
    if user_id != me and is_blocked_either_way(sb, me, user_id):
        return jsonify(username="Bilinmeyen", avatar_url=None, is_mine=False, stories=[])

    now = datetime.now(timezone.utc).isoformat()

    try:
        rows = sb.table("stories").select(
            "id, image_url, video_url, caption, created_at"
        ).eq("user_id", user_id).gt("expires_at", now).order("created_at").execute().data
    except Exception:
        rows = []

    if user_id != me:
        for r in rows:
            try:
                sb.table("story_views").upsert({"story_id": r["id"], "user_id": me}).execute()
            except Exception:
                pass

    # Her hikayeye anket verisini ekle
    for story in rows:
        attach_story_poll(sb, story, me)

    prof = sb.table("profiles").select("username, avatar_url").eq("id", user_id).execute().data
    prof = prof[0] if prof else {}

    return jsonify(
        username=prof.get("username", "Bilinmeyen"),
        avatar_url=prof.get("avatar_url"),
        is_mine=(user_id == me),
        stories=rows,
    )


@bp.route("/stories/<story_id>/react", methods=["POST"])
@login_required
@retry_on_connection_error
def react_to_story(story_id):
    """Hikayeye emoji tepkisi — Instagram deseni: sadece hikaye sahibine hafif
    bildirim gönderilir. Tepki transient'dir (hikaye 24 saatinde silinirse tepki
    de kaybolur), bu yüzden ayrı bir tablo tutulmaz — sadece notify() ile
    bildirim oluşturulur."""
    sb = get_sb()
    me = session["user"]["id"]
    emoji = request.form.get("emoji", "").strip()

    # İzin verilen emoji'ler (Instagram'ın hızlı tepki seti)
    allowed_emojis = {"❤️", "😂", "😮", "😢", "🔥", "👏"}
    if emoji not in allowed_emojis:
        return jsonify(error="Geçersiz emoji."), 400

    # Hikayeyi fetch et
    story = sb.table("stories").select("user_id, expires_at").eq("id", story_id).execute().data
    if not story:
        return jsonify(error="Hikaye bulunamadı."), 404

    owner_id = story[0]["user_id"]
    expires_at = story[0]["expires_at"]

    # Kendi hikayene tepki veremezsin
    if owner_id == me:
        return jsonify(error="Kendi hikayene tepki veremezsin."), 400

    # Engelleme kontrolü: hangi yönde olursa olsun bir engelleme varsa tepki reddedilir
    if is_blocked_either_way(sb, me, owner_id):
        return jsonify(error="Bu kullanıcının hikayesine tepki veremezsin."), 403

    # Süresi dolmuş hikaye kontrolü
    now = datetime.now(timezone.utc).isoformat()
    if expires_at < now:
        return jsonify(error="Süresi dolmuş hikayeye tepki veremezsin."), 410

    # Bildirim oluştur
    from .notifications import notify
    notify(sb, recipient_id=owner_id, actor_id=me, type_="story_reaction")

    return jsonify(ok=True)


@bp.route("/stories/<story_id>/reply", methods=["POST"])
@login_required
@retry_on_connection_error
def reply_to_story(story_id):
    """Hikayeye yanıt — Instagram deseni: sahibine DM olarak gider, hikayenin
    görseli (varsa) bağlam için mesaja iliştirilir. Kapsam bilinçli dar:
    yorum/beğeni gibi hikayeye özgü bir depolama YOK, doğrudan mevcut
    mesajlaşma sistemine düşer (bkz. dosya başı yorumu)."""
    sb = get_sb()
    me = session["user"]["id"]
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify(error="Yanıt boş olamaz."), 400
    if len(text) > 500:
        return jsonify(error="Yanıt çok uzun."), 400

    story = sb.table("stories").select("user_id, image_url").eq("id", story_id).execute().data
    if not story:
        return jsonify(error="Hikaye bulunamadı."), 404
    owner_id = story[0]["user_id"]
    if owner_id == me:
        return jsonify(error="Kendi hikayene yanıt veremezsin."), 400
    if is_blocked_either_way(sb, me, owner_id):
        return jsonify(error="Bu kullanıcıya mesaj gönderemezsin."), 403

    conv_id = _get_or_create_conversation(me, owner_id)
    reply_text = f"↩️ Hikayene yanıt:\n{text}"
    sb.table("messages").insert({
        "conversation_id": conv_id,
        "sender_id": me,
        "content": reply_text,
        "image_url": story[0].get("image_url"),
    }).execute()
    _notify_conversation(sb, conv_id, me)

    return jsonify(ok=True, conversation_id=conv_id)


@bp.route("/stories/<story_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_story(story_id):
    sb = get_sb()
    me = session["user"]["id"]
    sb.table("stories").delete().eq("id", story_id).eq("user_id", me).execute()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    return redirect(url_for("routes.feed"))


def _get_highlights(sb, user_id: str) -> list[dict]:
    """Bir kullanıcının highlight'larını (id, title, cover_url, item_count) döner.

    Profil sayfası server-render'ı ve JS'teki "mevcut highlight'a ekle"
    picker'ı bu helper'ı ortak kullanır."""
    try:
        rows = sb.table("story_highlights").select(
            "id, title, cover_url, story_highlight_items(count)"
        ).eq("user_id", user_id).order("created_at").execute().data
    except Exception:
        return []  # migration henüz uygulanmamışsa boş liste, sayfa kırılmaz
    for r in rows:
        counts = r.pop("story_highlight_items", None)
        r["item_count"] = counts[0]["count"] if counts else 0
    return rows


@bp.route("/stories/<story_id>/save-highlight", methods=["POST"])
@login_required
@retry_on_connection_error
def save_highlight(story_id):
    """Bir hikayeyi mevcut bir highlight'a ekler veya yeni highlight
    oluşturup ekler. Body: {"highlight_id": ...} XOR {"new_title": ...}."""
    sb = get_sb()
    me = session["user"]["id"]
    body = request.get_json(silent=True) or {}
    highlight_id = body.get("highlight_id")
    new_title = body.get("new_title")

    if not highlight_id and not new_title:
        return jsonify(error="highlight_id veya new_title gerekli."), 400
    if new_title is not None and not new_title.strip():
        return jsonify(error="Başlık boş olamaz."), 400

    story = sb.table("stories").select(
        "image_url, video_url, caption, created_at, user_id"
    ).eq("id", story_id).execute().data
    if not story:
        return jsonify(error="Hikaye bulunamadı."), 404
    story = story[0]
    if story["user_id"] != me:
        return jsonify(error="Sadece kendi hikayeni öne çıkarabilirsin."), 403

    try:
        if highlight_id:
            hl = sb.table("story_highlights").select("id, user_id").eq(
                "id", highlight_id
            ).execute().data
            if not hl:
                return jsonify(error="Highlight bulunamadı."), 404
            if hl[0]["user_id"] != me:
                return jsonify(error="Başkasının highlight'ına ekleyemezsin."), 403
        else:
            created = sb.table("story_highlights").insert({
                "user_id": me, "title": new_title.strip(), "cover_url": story["image_url"],
            }).execute().data
            highlight_id = created[0]["id"]

        # Hikayenin medyasını KOPYALA — orijinal hikaye (24 saatte) silinse
        # bile highlight kalıcı kalmalı, bu yüzden stories.id'ye FK verilmez.
        sb.table("story_highlight_items").insert({
            "highlight_id": highlight_id,
            "image_url": story["image_url"],
            "video_url": story["video_url"],
            "caption": story["caption"],
            "original_created_at": story["created_at"],
        }).execute()
    except Exception:
        return jsonify(error="Öne çıkanlar özelliği henüz aktif değil."), 503

    return jsonify(ok=True, highlight_id=highlight_id)


@bp.route("/stories/highlights/<user_id>")
@login_required
@retry_on_connection_error
def get_highlights(user_id):
    sb = get_sb()
    me = session["user"]["id"]

    # Engelleme kontrolü: hangi yönde olursa olsun bir engelleme varsa boş liste dön
    if user_id != me and is_blocked_either_way(sb, me, user_id):
        return jsonify(highlights=[])

    return jsonify(highlights=_get_highlights(sb, user_id))


@bp.route("/stories/highlights/<highlight_id>/view")
@login_required
@retry_on_connection_error
def view_highlight(highlight_id):
    sb = get_sb()
    me = session["user"]["id"]
    try:
        hl = sb.table("story_highlights").select("id, user_id, title").eq(
            "id", highlight_id
        ).execute().data
        if not hl:
            return jsonify(error="Highlight bulunamadı."), 404

        # Engelleme kontrolü: hangi yönde olursa olsun bir engelleme varsa 404 dön
        # (enumeration önleme — highlight bulunamazsa da aynı 404)
        if hl[0]["user_id"] != me and is_blocked_either_way(sb, me, hl[0]["user_id"]):
            return jsonify(error="Highlight bulunamadı."), 404

        items = sb.table("story_highlight_items").select(
            "id, image_url, video_url, caption, original_created_at"
        ).eq("highlight_id", highlight_id).order("added_at").execute().data
    except Exception:
        return jsonify(error="Öne çıkanlar özelliği henüz aktif değil."), 503

    return jsonify(title=hl[0]["title"], is_mine=(hl[0]["user_id"] == me), items=items)


@bp.route("/stories/highlights/<highlight_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_highlight(highlight_id):
    sb = get_sb()
    me = session["user"]["id"]
    try:
        sb.table("story_highlights").delete().eq("id", highlight_id).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="Öne çıkanlar özelliği henüz aktif değil."), 503
    return jsonify(ok=True)
