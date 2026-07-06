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

bp = Blueprint("stories", __name__)


def _cleanup_expired_stories(sb) -> None:
    """Süresi dolmuş (expires_at geçmiş) hikayeleri siler.

    Ayrı bir cron/scheduler YOK (bu projede hiç yok, bkz. notifications.py
    RETENTION_DAYS deseni) — feed her ziyaret edildiğinde fırsatçı temizlik
    yeterli, bu ölçekte (arkadaş grubu) hikayelerin saatlerce/günlerce
    görüntülenmeden birikmesi olası değil."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("stories").delete().lt("expires_at", now).execute()
    except Exception:
        pass  # sql/migration_stories.sql henüz uygulanmamış olabilir


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

    if not caption and not has_image and not has_video:
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
        sb.table("stories").insert({
            "user_id": me, "image_url": image_url, "video_url": video_url, "caption": caption,
        }).execute()
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
    işlenir (halka rengi için)."""
    sb = get_sb()
    me = session["user"]["id"]
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

    prof = sb.table("profiles").select("username, avatar_url").eq("id", user_id).execute().data
    prof = prof[0] if prof else {}

    return jsonify(
        username=prof.get("username", "Bilinmeyen"),
        avatar_url=prof.get("avatar_url"),
        is_mine=(user_id == me),
        stories=rows,
    )


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
