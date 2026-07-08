"""Web Push bildirimleri — VAPID anahtarıyla tarayıcı push subscription
yönetimi ve gönderimi. .env'de VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY yoksa
özellik sessizce devre dışı kalır (GIF/Klipy ile aynı graceful degradation
deseni — anahtar üretimi ve DB migration'ı Sprint dokümantasyonunda)."""
import json
import os
from flask import Blueprint, request, session, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("push", __name__, url_prefix="/push")

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")


@bp.route("/vapid-public-key")
@login_required
def vapid_public_key():
    """Tarayıcının `pushManager.subscribe()` çağrısında applicationServerKey
    olarak kullanacağı public key. Anahtar yoksa özellik devre dışı sinyali döner."""
    if not VAPID_PUBLIC_KEY:
        return jsonify({"enabled": False})
    return jsonify({"enabled": True, "key": VAPID_PUBLIC_KEY})


@bp.route("/subscribe", methods=["POST"])
@login_required
@retry_on_connection_error
def subscribe():
    """Tarayıcıdan gelen PushSubscription'ı kaydeder — aynı endpoint tekrar
    abone olursa (örn. farklı kullanıcı aynı cihazda giriş yaptıysa) önce
    silinip yeniden eklenir (upsert yerine, endpoint UNIQUE olduğu için)."""
    data = request.get_json() or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "eksik_veri"}), 400

    sb = get_sb()
    me = session["user"]["id"]
    try:
        sb.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()
        sb.table("push_subscriptions").insert({
            "user_id": me, "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
        }).execute()
        return jsonify({"ok": True})
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify({"error": "ozellik_henuz_aktif_degil"}), 503
        raise


@bp.route("/unsubscribe", methods=["POST"])
@login_required
@retry_on_connection_error
def unsubscribe():
    """Kullanıcı bildirimleri kapattığında abonelik satırını siler."""
    data = request.get_json() or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "eksik_veri"}), 400
    sb = get_sb()
    me = session["user"]["id"]
    try:
        sb.table("push_subscriptions").delete().eq("endpoint", endpoint).eq("user_id", me).execute()
    except Exception:
        pass
    return jsonify({"ok": True})


def send_push_to_user(sb, user_id: str, title: str, body: str, url: str = "/") -> None:
    """Bir kullanıcının kayıtlı TÜM cihazlarına push bildirimi gönderir.

    VAPID anahtarları yoksa veya `push_subscriptions` tablosu henüz
    oluşturulmamışsa sessizce hiçbir şey yapmaz — bu fonksiyon `notify()`
    içinden çağrılır ve push gönderimindeki bir hata ASLA normal bildirim
    akışını (DB satırı oluşturma) kesintiye uğratmamalı. Geçersiz/süresi
    dolmuş (404/410) abonelikler otomatik silinir (tarayıcı/cihaz kaldırılmış).
    """
    if not VAPID_PRIVATE_KEY:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    try:
        subs = sb.table("push_subscriptions").select(
            "id, endpoint, p256dh, auth"
        ).eq("user_id", user_id).execute().data
    except Exception:
        return

    if not subs:
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                try:
                    sb.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
                except Exception:
                    pass
        except Exception:
            pass  # Tek bir cihaza gönderim hatası diğer cihazları etkilemesin
