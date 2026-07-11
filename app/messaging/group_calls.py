"""LiveKit grup sesli/görüntülü arama token üretimi."""
import os
from datetime import timedelta
from flask import jsonify, session, abort
from concurrent.futures import ThreadPoolExecutor
from livekit import api
from . import bp
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error


@bp.route("/<conversation_id>/call-token", methods=["POST"])
@login_required
@retry_on_connection_error
def call_token(conversation_id):
    """LiveKit grup sohbeti token'ı üret.

    - Başarı 200: {"token": "...", "url": "...", "room": "grp-<conversation_id>"}
    - LiveKit yapılandırılmamışsa 503: {"error": "group_calls_not_configured"}
    - Katılımcı değilse veya is_group=false ise 404
    - CSRF: global before_request zaten doğruluyor
    """
    # LiveKit ortam değişkenlerini kontrol et
    livekit_url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")

    if not livekit_url or not api_key or not api_secret:
        return jsonify({"error": "group_calls_not_configured"}), 503

    me = session["user"]["id"]
    # .get: eski oturumlarda username alanı bulunmayabilir — KeyError yerine boş ad
    my_username = session["user"].get("username") or ""
    sb = get_sb()

    # --- Katılımcı + grup bilgisi doğrulaması ---
    def _check_participant():
        return sb.table("conversation_participants").select().eq(
            "conversation_id", conversation_id
        ).eq("user_id", me).execute().data

    def _fetch_conv_meta():
        try:
            conv = sb.table("conversations").select("is_group").eq(
                "id", conversation_id
            ).execute().data
            if conv:
                return bool(conv[0].get("is_group"))
        except Exception:
            pass
        return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        part_future = executor.submit(_check_participant)
        meta_future = executor.submit(_fetch_conv_meta)

        part = part_future.result()
        is_group = meta_future.result()

    # Katılımcı değilse veya grup değilse 404 (enumeration koruması)
    if not part or not is_group:
        abort(404)

    # --- Token üret ---
    try:
        room_name = f"grp-{conversation_id}"
        token = api.AccessToken(api_key, api_secret)
        token.with_identity(me)
        token.with_name(my_username)
        token.with_grants(
            api.VideoGrants(room_join=True, room=room_name)
        )
        token.with_ttl(timedelta(hours=1))
        jwt_token = token.to_jwt()

        return jsonify({
            "token": jwt_token,
            "url": livekit_url,
            "room": room_name
        }), 200
    except Exception:
        # Token üretim hatası nadir ama graceful fallback
        return jsonify({"error": "token_generation_failed"}), 500
