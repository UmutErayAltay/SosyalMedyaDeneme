from datetime import datetime, timezone

from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb


# ----------------------- AKTİF OTURUMLAR (Faz 4 sonrası eksik giderme, native Android) -----------------------
# Web'in routes/profile.py revoke_session()/revoke_other_sessions()'ının KAVRAMSAL
# karşılığı, ama FARKLI bir mekanizma üzerinde: web tarayıcı oturumlarını
# (user_sessions tablosu, Flask session'daki session_record_id) yönetir; native'in
# hiç Flask session'ı yok, kendi opak bearer token'ları (api_tokens tablosu, HER
# SATIR = bir cihazın login()/register()/google-login()'da aldığı token) var.
# Bu yüzden burada user_sessions DEĞİL, api_tokens üzerinde çalışılır — "aktif
# oturum" burada "aynı hesaba giriş yapmış başka bir native cihaz" demektir.
# Ayrıca: revoke web'de user_sessions satırını HARD DELETE eder; native'de
# api_tokens'ın KENDİ revocation deseni (revoked_at set etmek — logout()/
# deactivate()'teki AYNI yöntem) kullanılır, hard delete YAPILMAZ (tutarlılık).

@bp.route("/sessions")
@api_login_required
def api_list_sessions():
    """Aynı hesaba giriş yapmış TÜM aktif (iptal edilmemiş) native cihazlar —
    revoke_session() sayfasının native karşılığı. is_current, BU isteği
    doğrulayan bearer token'ın satırını işaretler (kendi cihazını yanlışlıkla
    iptal etmeyi native tarafın engelleyebilmesi için)."""
    sb = get_sb()
    me = request.api_user["id"]
    my_token_hash = request.api_token_hash

    rows = sb.table("api_tokens").select(
        "id, token_hash, device_name, created_at, last_used_at"
    ).eq("user_id", me).is_("revoked_at", "null").order(
        "last_used_at", desc=True
    ).execute().data

    return jsonify(sessions=[{
        "id": r["id"],
        "device_name": r.get("device_name"),
        "created_at": r.get("created_at"),
        "last_used_at": r.get("last_used_at"),
        "is_current": r["token_hash"] == my_token_hash,
    } for r in rows])


@bp.route("/sessions/<session_id>/revoke", methods=["POST"])
@api_login_required
def api_revoke_session(session_id):
    """Belirtilen (BAŞKA bir cihazın) oturumunu sonlandırır — revoke_session()'ın
    AYNI kuralları: satır bana ait değilse 403, KENDİ mevcut token'ımı bu
    yoldan iptal etmeye çalışırsam (use_logout — /auth/logout kullanılmalı) 400.
    """
    sb = get_sb()
    me = request.api_user["id"]

    row = sb.table("api_tokens").select("token_hash, user_id").eq("id", session_id).execute().data
    if not row or row[0]["user_id"] != me:
        return jsonify(error="forbidden"), 403

    if row[0]["token_hash"] == request.api_token_hash:
        return jsonify(error="use_logout"), 400

    try:
        sb.table("api_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "sb_access_token_enc": None,
            "sb_refresh_token_enc": None,
            "sb_token_expires_at": None,
        }).eq("id", session_id).execute()
    except Exception:
        return jsonify(error="revoke_failed"), 500

    return jsonify(ok=True)


@bp.route("/sessions/revoke-others", methods=["POST"])
@api_login_required
def api_revoke_other_sessions():
    """Mevcut cihaz HARİÇ tüm aktif oturumları sonlandırır — revoke_other_sessions()'ın
    AYNI mantığı (şifre çalınmış olabilir şüphesinde kullanışlı). Bu isteği
    doğrulayan token'a ASLA dokunulmaz (neq ile hariç tutulur)."""
    sb = get_sb()
    me = request.api_user["id"]

    try:
        sb.table("api_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "sb_access_token_enc": None,
            "sb_refresh_token_enc": None,
            "sb_token_expires_at": None,
        }).eq("user_id", me).is_("revoked_at", "null").neq(
            "token_hash", request.api_token_hash
        ).execute()
    except Exception:
        return jsonify(error="revoke_failed"), 500

    return jsonify(ok=True)
