from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb


# ----------------------- FCM PUSH KAYDI (Faz 5 Dalga 4A, native Android) -----------------------
# app/push.py'nin (VAPID web push) subscribe()/unsubscribe() ile AYNI mantığı —
# fcm_tokens.token UNIQUE olduğu için "farklı kullanıcı aynı cihazda giriş
# yaptı" senaryosunda önce silinip yeniden eklenir (upsert yerine).

@bp.route("/push/register", methods=["POST"])
@api_login_required
def api_push_register():
    """Native Android'den gelen FCM token'ını kaydeder.

    Aynı token başka bir user_id altında zaten kayıtlıysa (cihaz el
    değiştirmiş/farklı hesapla giriş yapılmış) önce silinip bu kullanıcı
    için yeniden eklenir — push.py subscribe()'daki AYNI gerekçe.
    """
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="eksik_veri"), 400

    sb = get_sb()
    me = request.api_user["id"]
    try:
        sb.table("fcm_tokens").delete().eq("token", token).execute()
        sb.table("fcm_tokens").insert({
            "user_id": me, "token": token, "platform": "android",
        }).execute()
        return jsonify(ok=True)
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(error="ozellik_henuz_aktif_degil"), 503
        raise


@bp.route("/push/unregister", methods=["POST"])
@api_login_required
def api_push_unregister():
    """Kullanıcı bildirimleri kapattığında/çıkış yaptığında token satırını siler."""
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="eksik_veri"), 400
    sb = get_sb()
    me = request.api_user["id"]
    try:
        sb.table("fcm_tokens").delete().eq("token", token).eq("user_id", me).execute()
    except Exception:
        pass
    return jsonify(ok=True)
