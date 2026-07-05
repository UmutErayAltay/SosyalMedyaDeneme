"""Bir konuşmaya mesaj/görsel/ses/paylaşılan post gönderme."""
from flask import request, redirect, url_for, session, abort, flash, jsonify
from . import bp
from ._common import _notify_conversation, _get_or_create_conversation
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..storage_helper import upload_image, upload_audio
from ..blocks import is_blocked_either_way


@bp.route("/<conversation_id>/send", methods=["POST"])
@login_required
@retry_on_connection_error
def send_message(conversation_id):
    sb = get_sb()
    me = session["user"]["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute()
    if not part.data:
        abort(403)

    content = request.form.get("content", "").strip()
    image_file = request.files.get("image")
    has_image = image_file and image_file.filename
    audio_file = request.files.get("audio")
    has_audio = audio_file and audio_file.filename
    wants_json = "application/json" in request.headers.get("Accept", "")

    # Engelleme: konuşma bir engellemeden ÖNCE başlamış olabilir — her mesaj
    # gönderiminde diğer katılımcı(lar)la aramda bir engelleme var mı kontrol et.
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        if wants_json:
            return jsonify({"error": "blocked"}), 403
        flash("Bu kullanıcıyla mesajlaşamazsın.", "error")
        return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    if not content and not has_image and not has_audio:
        if wants_json:
            return jsonify({"error": "empty"}), 400
        return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    image_url = None
    if has_image:
        image_url = upload_image(image_file, folder="messages")
        if not image_url:
            if wants_json:
                return jsonify({"error": "upload_failed"}), 400
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    audio_url = None
    if has_audio:
        audio_url = upload_audio(audio_file, folder="messages")
        if not audio_url:
            if wants_json:
                return jsonify({"error": "upload_failed"}), 400
            flash("Sesli mesaj yüklenemedi (geçersiz format veya 10MB'tan büyük).", "error")
            return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    insert_data = {
        "conversation_id": conversation_id,
        "sender_id": me,
        "content": content,
        "image_url": image_url,
    }
    try:
        # sql/migration_voice_messages.sql henüz uygulanmamışsa 'audio_url'
        # kolonu yok — mesaj gönderimi bundan etkilenmesin diye kolonsuz dene
        data = dict(insert_data)
        if audio_url:
            data["audio_url"] = audio_url
        inserted = sb.table("messages").insert(data).execute()
    except Exception:
        inserted = sb.table("messages").insert(insert_data).execute()
    _notify_conversation(sb, conversation_id, me)

    if wants_json:
        return jsonify(inserted.data[0])
    return redirect(url_for("messaging.conversation", conversation_id=conversation_id))


@bp.route("/<conversation_id>/share-post/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def share_post(conversation_id, post_id):
    sb = get_sb()
    me = session["user"]["id"]

    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        abort(403)

    # Postu, görselleriyle birlikte çek
    post = sb.table("posts").select(
        "id, content, image_url, image_urls, profiles!posts_user_id_fkey(username)"
    ).eq("id", post_id).execute().data

    if not post: abort(404)
    p = post[0]

    # İlk görseli al (öncelik image_urls dizisindeyse onu kullan)
    first_img = None
    if p.get("image_urls") and len(p["image_urls"]) > 0:
        first_img = p["image_urls"][0]
    elif p.get("image_url"):
        first_img = p["image_url"]

    # Mesajın metin kısmını hazırla
    share_text = f"📎 Post paylaştı: @{p['profiles']['username']}\n{p['content'][:50]}..."

    # Veritabanına kaydet
    sb.table("messages").insert({
        "conversation_id": conversation_id,
        "sender_id": me,
        "content": share_text,
        "image_url": first_img, # Görseli buraya ekliyoruz!
    }).execute()
    _notify_conversation(sb, conversation_id, me)

    return redirect(url_for("messaging.conversation", conversation_id=conversation_id))


@bp.route("/share/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def share_post_multiple(post_id):
    """Postu seçili birden fazla kullanıcıya DM olarak gönderir (yeni modal yöntemi)."""
    me = session["user"]["id"]
    sb = get_sb()
    data = request.get_json()

    user_ids = data.get("user_ids", [])
    note = data.get("note", "").strip()

    if not user_ids:
        return jsonify({"error": "Kullanıcı seçilmedi"}), 400

    # Post önizlemesini çek (GÖRSELLERİ DE DAHİL ETTİK)
    post = sb.table("posts").select(
        "id, content, image_url, image_urls, profiles!posts_user_id_fkey(username)"
    ).eq("id", post_id).execute().data

    if not post:
        return jsonify({"error": "Post bulunamadı"}), 404
    post_data = post[0]

    # Postun ilk görselini bul
    post_image = None
    if post_data.get("image_urls") and len(post_data["image_urls"]) > 0:
        post_image = post_data["image_urls"][0]
    elif post_data.get("image_url"):
        post_image = post_data["image_url"]

    share_text = note + "\n\n" if note else ""
    share_text += f"📎 Paylaşılan post: /post/{post_id}\n\"{post_data['content'][:100]}\""
    if post_data.get("profiles"):
        share_text += f"\n— @{post_data['profiles']['username']}"

    sent_count = 0
    for target_id in set(user_ids):
        cid = _get_or_create_conversation(me, target_id)
        sb.table("messages").insert({
            "conversation_id": cid,
            "sender_id": me,
            "content": share_text.strip(),
            "image_url": post_image
        }).execute()
        _notify_conversation(sb, cid, me)
        sent_count += 1

    return jsonify({"sent": sent_count})
