"""Mesaj tepkileri (emoji reactions) — toggle ekle/sil."""
from flask import request, session, abort, jsonify
from . import bp
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error


@bp.route("/message/<message_id>/react", methods=["POST"])
@login_required
@retry_on_connection_error
def react_message(message_id):
    """Mesaja emoji tepkisi ekle/değiştir/sil (toggle).

    Request body: {"reaction": "❤️"}
    - Aynı tepki zaten varsa: sil
    - Farklı tepki varsa: güncelle
    - Tepki yoksa: ekle

    message_reactions tablosu henüz oluşturulmadıysa 503 döner.
    """
    sb = get_sb()
    me = session["user"]["id"]

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    reaction = data.get("reaction", "").strip()
    if not reaction:
        return jsonify({"error": "empty_reaction"}), 400

    # Message'ı bul → conversation_id'yi çek
    try:
        msg = sb.table("messages").select("conversation_id").eq("id", message_id).execute()
        if not msg.data:
            abort(404)  # Enumeration koruması
        conversation_id = msg.data[0]["conversation_id"]
    except Exception:
        abort(404)

    # Ben bu conversation'da mı? (404 enumeration koruması)
    try:
        part = sb.table("conversation_participants").select().eq(
            "conversation_id", conversation_id
        ).eq("user_id", me).execute()
        if not part.data:
            abort(404)  # 403 değil, enumeration koruması
    except Exception:
        abort(404)

    # message_reactions toggle — tablo yoksa migration beklentisi
    try:
        # Mevcut tepki kontrolü
        existing = sb.table("message_reactions").select().eq(
            "message_id", message_id
        ).eq("user_id", me).execute()

        if existing.data:
            # Mevcut tepki var
            existing_reaction = existing.data[0].get("reaction")
            if existing_reaction == reaction:
                # Aynı tepki → sil (toggle-off)
                sb.table("message_reactions").delete().eq(
                    "message_id", message_id
                ).eq("user_id", me).execute()
                return jsonify(ok=True, reaction=None), 200
            else:
                # Farklı tepki → güncelle
                sb.table("message_reactions").update({"reaction": reaction}).eq(
                    "message_id", message_id
                ).eq("user_id", me).execute()
                return jsonify(ok=True, reaction=reaction), 200
        else:
            # Tepki yok → ekle
            sb.table("message_reactions").insert({
                "message_id": message_id,
                "user_id": me,
                "reaction": reaction,
            }).execute()
            return jsonify(ok=True, reaction=reaction), 201

    except Exception as e:
        # message_reactions tablosu henüz oluşturulmadı
        if "message_reactions" in str(e) or "does not exist" in str(e):
            return jsonify({"error": "feature_not_yet_active"}), 503
        raise
