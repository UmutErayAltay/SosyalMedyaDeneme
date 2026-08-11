"""JSON REST API — Hikayeler (Stories), native Android Faz 5.

app/stories.py'deki web mantığının BİREBİR mirror'ı — hiçbir yeni davranış
İCAT EDİLMEDİ. Paylaşılan yardımcılar (`active_stories_bar`, `attach_story_poll`,
`_visible_story_filter`, `_get_highlights`, `_cleanup_expired_stories`) doğrudan
`..stories`'den import edilir, KOPYALANMAZ.

Route seçimi: web'in `/stories/new`'i yerine api_v1 konvansiyonuna uygun
`POST /stories` seçildi (diğer kaynak-oluşturma uçları — `/posts`, `/reels`
POST'un URL'i kaynağın kendisi, `/new` soneki yok).

Tepki (react) ve yanıt (reply) uçları story_reactions/story_replies gibi bir
tablo KULLANMAZ — web ile AYNI şekilde birer DM mesajı olarak
`_get_or_create_conversation`/`_notify_conversation` (app/messaging/_common.py)
ile kaydedilir.
"""
import json
import re
from datetime import datetime, timezone

from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..storage_helper import upload_image, upload_video
from ..blocks import is_blocked_either_way, blocked_user_ids
from ..messaging._common import _get_or_create_conversation, _notify_conversation
from ..polls import create_poll
from ..stories import (
    active_stories_bar,
    attach_story_poll,
    _visible_story_filter,
    _get_highlights,
)


# ----------------------- HİKAYE ÇUBUĞU (feed'in üstü) -----------------------

@bp.route("/stories/bar")
@api_login_required
def api_stories_bar():
    """Feed'in üstündeki hikaye çubuğu — active_stories_bar()'ın AYNI mantığı
    (web'de bu ayrı bir route değil, feed render'ına gömülüydü; native'in
    ayrı bir story-bar bileşeni olduğu için burada AYRI bir uç olarak açıldı)."""
    sb = get_sb()
    me = request.api_user["id"]
    bar = active_stories_bar(sb, me, blocked_user_ids(sb, me))
    return jsonify(stories=bar)


# ----------------------- HİKAYE OLUŞTUR -----------------------

@bp.route("/stories", methods=["POST"])
@api_login_required
def api_create_story():
    """Yeni hikaye — create_story()'nin AYNI mantığı, multipart/form-data
    (görsel/video içerdiği için JSON değil). Alanlar: `caption`, `image`
    VEYA `video` (tek biri, ikisi de gönderilirse image kazanır),
    `poll_option_1..4`, `poll_position_x/y`, `poll_scale`,
    `caption_position_x/y`, `background_color` (medya varsa yok sayılır),
    `visibility` (public/followers/close_friends, varsayılan public),
    `overlay_elements` (ÇOKLU GIF/etiket görseli — JSON-ENCODED STRING, dizi:
    `[{"url":..., "position_x":..., "position_y":..., "scale":...}, ...]`;
    dosya YÜKLEMESİ değil, gif_url'deki (interactions.py) AYNI güven
    seviyesiyle client'tan olduğu gibi kabul edilir, en fazla 3 eleman)."""
    sb = get_sb()
    me = request.api_user["id"]
    caption = (request.form.get("caption") or "").strip()
    image_file = request.files.get("image")
    video_file = request.files.get("video")
    has_image = bool(image_file and image_file.filename)
    has_video = bool(video_file and video_file.filename)

    poll_options_raw = [request.form.get(f"poll_option_{i}", "").strip() for i in range(1, 5)]
    poll_options = [o for o in poll_options_raw if o]
    has_poll = len(poll_options) >= 2

    poll_position_x = 0.5
    poll_position_y = 0.75
    poll_scale = 1.0
    if has_poll:
        try:
            poll_position_x = float(request.form.get("poll_position_x", "0.5"))
            if not (0 <= poll_position_x <= 1):
                poll_position_x = 0.5
        except ValueError:
            poll_position_x = 0.5

        try:
            poll_position_y = float(request.form.get("poll_position_y", "0.75"))
            if not (0 <= poll_position_y <= 1):
                poll_position_y = 0.75
        except ValueError:
            poll_position_y = 0.75

        try:
            poll_scale = float(request.form.get("poll_scale", "1.0"))
            if not (0.3 <= poll_scale <= 3):
                poll_scale = 1.0
        except ValueError:
            poll_scale = 1.0

    caption_position_x = 0.5
    try:
        caption_position_x = float(request.form.get("caption_position_x", "0.5"))
        if not (0 <= caption_position_x <= 1):
            caption_position_x = 0.5
    except ValueError:
        caption_position_x = 0.5

    caption_position_y = 0.75
    try:
        caption_position_y = float(request.form.get("caption_position_y", "0.75"))
        if not (0 <= caption_position_y <= 1):
            caption_position_y = 0.75
    except ValueError:
        caption_position_y = 0.75

    # Overlay görselleri (GIF/etiket, ÇOKLU) — tek `overlay_elements` alanı,
    # JSON-encoded string olarak gelir (multipart form'da dizi/nesne taşımanın
    # standart yolu). Dekoratif/kritik-olmayan bir özellik olduğu için
    # bozuk JSON tüm isteği ERROR ETMEZ, poll_scale parse fallback'iyle AYNI
    # fail-open felsefesi: sessizce boş listeye düşer. Her eleman ayrı ayrı
    # doğrulanır (url boşsa o eleman ATLANIR, pozisyon/scale clamp edilir,
    # caption_position/poll_position ile AYNI desen) ve en fazla 3 elemanla
    # sınırlanır (upload_images max_count=4'teki "sessiz kırpma" emsaliyle
    # aynı). Boş liste DEĞİL None saklanır — "yokluk = render etme" kontratı.
    overlay_elements_raw = request.form.get("overlay_elements")
    overlay_elements = []
    if overlay_elements_raw:
        try:
            parsed = json.loads(overlay_elements_raw)
        except (ValueError, TypeError):
            parsed = []
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if not isinstance(url, str) or not url.strip():
                    continue

                position_x = 0.5
                try:
                    position_x = float(item.get("position_x", 0.5))
                    if not (0 <= position_x <= 1):
                        position_x = 0.5
                except (TypeError, ValueError):
                    position_x = 0.5

                position_y = 0.5
                try:
                    position_y = float(item.get("position_y", 0.5))
                    if not (0 <= position_y <= 1):
                        position_y = 0.5
                except (TypeError, ValueError):
                    position_y = 0.5

                scale = 1.0
                try:
                    scale = float(item.get("scale", 1.0))
                    if not (0.3 <= scale <= 3):
                        scale = 1.0
                except (TypeError, ValueError):
                    scale = 1.0

                overlay_elements.append({
                    "url": url.strip(), "position_x": position_x,
                    "position_y": position_y, "scale": scale,
                })
                if len(overlay_elements) >= 3:
                    break

    if not caption and not has_image and not has_video and not has_poll and not overlay_elements:
        return jsonify(error="empty_story"), 400

    image_url = None
    video_url = None
    if has_image:
        image_url = upload_image(image_file, folder="stories")
        if not image_url:
            return jsonify(error="upload_failed"), 400
    elif has_video:
        video_url = upload_video(video_file, folder="stories")
        if not video_url:
            return jsonify(error="upload_failed"), 400

    background_color = (request.form.get("background_color") or "").strip()
    if background_color and not re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", background_color):
        background_color = None
    if has_image or has_video:
        background_color = None

    visibility = request.form.get("visibility", "public")
    if visibility not in ("public", "followers", "close_friends"):
        visibility = "public"

    story_data = {
        "user_id": me, "image_url": image_url, "video_url": video_url, "caption": caption,
        "background_color": background_color, "visibility": visibility,
        "caption_position_x": caption_position_x, "caption_position_y": caption_position_y,
        # Supabase python client jsonb kolonuna native list/dict kabul eder —
        # burada string'e ÇEVRİLMEZ, JSON-encode SADECE gelen form alanında.
        "overlay_elements": overlay_elements or None,
    }
    try:
        result = sb.table("stories").insert(story_data).execute()
    except Exception:
        try:
            # overlay_elements kolonu migration'sız ortamda yoksa (503
            # değil) fallback: caption_position gibi eski kolonları da atıp
            # SADECE çekirdek alanlarla tekrar dene.
            story_data_legacy = {
                "user_id": me, "image_url": image_url, "video_url": video_url, "caption": caption,
            }
            result = sb.table("stories").insert(story_data_legacy).execute()
        except Exception:
            return jsonify(error="stories_not_available"), 503

    story_id = result.data[0]["id"] if result.data else None
    if story_id and has_poll:
        try:
            create_poll(sb, poll_options, story_id=story_id,
                        position_x=poll_position_x, position_y=poll_position_y, scale=poll_scale)
        except Exception:
            return jsonify(ok=True, story_id=story_id, poll_error=True)

    return jsonify(ok=True, story_id=story_id)


# ----------------------- KULLANICININ HİKAYELERİ -----------------------

@bp.route("/stories/user/<user_id>")
@api_login_required
def api_user_stories(user_id):
    """Bir kullanıcının aktif hikayeleri — user_stories()'in AYNI mantığı
    (GET içinde yan etki: kendi hikayen DEĞİLSE story_views upsert edilir).
    Native tarafı bu isteği story viewer'ın SADECE ilk açılışında (init) bir
    kez çağırmalı, her recomposition'da DEĞİL."""
    sb = get_sb()
    me = request.api_user["id"]

    if user_id != me and is_blocked_either_way(sb, me, user_id):
        return jsonify(username="Bilinmeyen", avatar_url=None, is_mine=False, stories=[])

    now = datetime.now(timezone.utc).isoformat()

    try:
        rows = sb.table("stories").select(
            "id, user_id, image_url, video_url, caption, created_at, visibility, background_color, "
            "caption_position_x, caption_position_y, overlay_elements"
        ).eq("user_id", user_id).gt("expires_at", now).order("created_at").execute().data
    except Exception:
        rows = []

    rows = _visible_story_filter(sb, me, rows)

    if user_id != me:
        for r in rows:
            try:
                sb.table("story_views").upsert({"story_id": r["id"], "user_id": me}).execute()
            except Exception:
                pass

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


# ----------------------- TEPKİ / YANIT (DM olarak kaydedilir) -----------------------

@bp.route("/stories/<story_id>/react", methods=["POST"])
@api_login_required
def api_react_to_story(story_id):
    """Hikayeye emoji tepkisi — react_to_story()'nin AYNI mantığı: tepki bir
    tabloda TUTULMAZ, hikaye sahibine DM olarak gider (hikayenin görseli
    mesaja iliştirilir) + bildirim üretilir. JSON body: {"emoji": "..."}."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    emoji = (data.get("emoji") or "").strip() if isinstance(data, dict) else ""
    if not emoji:
        emoji = (request.form.get("emoji") or "").strip()

    allowed_emojis = {"❤️", "😂", "😮", "😢", "🔥", "👏"}
    if emoji not in allowed_emojis:
        return jsonify(error="invalid_emoji"), 400

    story = sb.table("stories").select("user_id, expires_at, image_url").eq("id", story_id).execute().data
    if not story:
        return jsonify(error="not_found"), 404

    owner_id = story[0]["user_id"]
    expires_at = story[0]["expires_at"]

    if owner_id == me:
        return jsonify(error="cannot_react_own_story"), 400

    if is_blocked_either_way(sb, me, owner_id):
        return jsonify(error="blocked"), 403

    now = datetime.now(timezone.utc).isoformat()
    if expires_at < now:
        return jsonify(error="story_expired"), 410

    from ..notifications import notify
    notify(sb, recipient_id=owner_id, actor_id=me, type_="story_reaction")

    conv_id = _get_or_create_conversation(me, owner_id)
    sb.table("messages").insert({
        "conversation_id": conv_id,
        "sender_id": me,
        "content": f"{emoji} Hikayene tepki verdi",
        "image_url": story[0].get("image_url"),
    }).execute()
    _notify_conversation(sb, conv_id, me)

    return jsonify(ok=True, conversation_id=conv_id)


@bp.route("/stories/<story_id>/reply", methods=["POST"])
@api_login_required
def api_reply_to_story(story_id):
    """Hikayeye yanıt — reply_to_story()'nin AYNI mantığı, DM olarak kaydedilir.
    JSON body: {"text": "..."}."""
    sb = get_sb()
    me = request.api_user["id"]
    text = (request.get_json(silent=True) or {}).get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return jsonify(error="empty_reply"), 400
    if len(text) > 500:
        return jsonify(error="reply_too_long"), 400

    story = sb.table("stories").select("user_id, image_url").eq("id", story_id).execute().data
    if not story:
        return jsonify(error="not_found"), 404
    owner_id = story[0]["user_id"]
    if owner_id == me:
        return jsonify(error="cannot_reply_own_story"), 400
    if is_blocked_either_way(sb, me, owner_id):
        return jsonify(error="blocked"), 403

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


# ----------------------- SİLME -----------------------

@bp.route("/stories/<story_id>/delete", methods=["POST"])
@api_login_required
def api_delete_story(story_id):
    sb = get_sb()
    me = request.api_user["id"]
    sb.table("stories").delete().eq("id", story_id).eq("user_id", me).execute()
    return jsonify(ok=True)


# ----------------------- HIGHLIGHTS (öne çıkanlar) -----------------------
# ProfileScreen'e BAĞLANMAYACAK bu turda (Dalga 4'e ertelendi) ama backend
# ve native VM/ekranları hazır olacak şekilde yazılıyor.

@bp.route("/stories/<story_id>/save-highlight", methods=["POST"])
@api_login_required
def api_save_highlight(story_id):
    """Hikayeyi mevcut bir highlight'a ekler veya yenisini oluşturur.
    JSON body: {"highlight_id": ...} XOR {"new_title": ...}."""
    sb = get_sb()
    me = request.api_user["id"]
    body = request.get_json(silent=True) or {}
    highlight_id = body.get("highlight_id")
    new_title = body.get("new_title")

    if not highlight_id and not new_title:
        return jsonify(error="highlight_id_or_new_title_required"), 400
    if new_title is not None and not str(new_title).strip():
        return jsonify(error="empty_title"), 400

    story = sb.table("stories").select(
        "image_url, video_url, caption, created_at, user_id"
    ).eq("id", story_id).execute().data
    if not story:
        return jsonify(error="not_found"), 404
    story = story[0]
    if story["user_id"] != me:
        return jsonify(error="not_owner"), 403

    try:
        if highlight_id:
            hl = sb.table("story_highlights").select("id, user_id").eq(
                "id", highlight_id
            ).execute().data
            if not hl:
                return jsonify(error="highlight_not_found"), 404
            if hl[0]["user_id"] != me:
                return jsonify(error="not_owner"), 403
        else:
            created = sb.table("story_highlights").insert({
                "user_id": me, "title": str(new_title).strip(), "cover_url": story["image_url"],
            }).execute().data
            highlight_id = created[0]["id"]

        sb.table("story_highlight_items").insert({
            "highlight_id": highlight_id,
            "image_url": story["image_url"],
            "video_url": story["video_url"],
            "caption": story["caption"],
            "original_created_at": story["created_at"],
        }).execute()
    except Exception:
        return jsonify(error="highlights_not_available"), 503

    return jsonify(ok=True, highlight_id=highlight_id)


@bp.route("/stories/highlights/<user_id>")
@api_login_required
def api_get_highlights(user_id):
    sb = get_sb()
    me = request.api_user["id"]

    if user_id != me and is_blocked_either_way(sb, me, user_id):
        return jsonify(highlights=[])

    return jsonify(highlights=_get_highlights(sb, user_id))


@bp.route("/stories/highlights/<highlight_id>/view")
@api_login_required
def api_view_highlight(highlight_id):
    sb = get_sb()
    me = request.api_user["id"]
    try:
        hl = sb.table("story_highlights").select("id, user_id, title").eq(
            "id", highlight_id
        ).execute().data
        if not hl:
            return jsonify(error="not_found"), 404

        if hl[0]["user_id"] != me and is_blocked_either_way(sb, me, hl[0]["user_id"]):
            return jsonify(error="not_found"), 404

        items = sb.table("story_highlight_items").select(
            "id, image_url, video_url, caption, original_created_at"
        ).eq("highlight_id", highlight_id).order("added_at").execute().data
    except Exception:
        return jsonify(error="highlights_not_available"), 503

    return jsonify(title=hl[0]["title"], is_mine=(hl[0]["user_id"] == me), items=items)


@bp.route("/stories/highlights/<highlight_id>/delete", methods=["POST"])
@api_login_required
def api_delete_highlight(highlight_id):
    sb = get_sb()
    me = request.api_user["id"]
    try:
        sb.table("story_highlights").delete().eq("id", highlight_id).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="highlights_not_available"), 503
    return jsonify(ok=True)


@bp.route("/stories/highlights/<highlight_id>/update", methods=["POST"])
@api_login_required
def api_update_highlight(highlight_id):
    """Highlight başlığını ve/veya kapak görselini günceller — SIFIRDAN
    (web'de de yoktu). JSON body: {"title": ...} ve/veya {"cover_url": ...},
    en az biri dolu olmalı. Sahiplik `.eq("user_id", me)` update filtresiyle
    uygulama katmanında zorlanır (get_sb() service-role, RLS'i bypass eder);
    başkasının highlight'ı için 404 döner (403 DEĞİL — api_view_highlight
    ile AYNI enumeration-önleme deseni, highlight ID'lerinin var/yok
    olduğu dışarıdan ayırt edilemesin)."""
    sb = get_sb()
    me = request.api_user["id"]
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}

    title = body.get("title")
    cover_url = body.get("cover_url")
    update_data = {}
    if isinstance(title, str) and title.strip():
        update_data["title"] = title.strip()
    if isinstance(cover_url, str) and cover_url.strip():
        update_data["cover_url"] = cover_url.strip()

    if not update_data:
        return jsonify(error="nothing_to_update"), 400

    try:
        hl = sb.table("story_highlights").select("id, user_id").eq(
            "id", highlight_id
        ).execute().data
        if not hl or hl[0]["user_id"] != me:
            return jsonify(error="not_found"), 404

        sb.table("story_highlights").update(update_data).eq(
            "id", highlight_id
        ).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="highlights_not_available"), 503

    return jsonify(ok=True)
