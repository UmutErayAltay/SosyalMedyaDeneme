"""FCM (Firebase Cloud Messaging) push bildirimleri — native Android uygulaması
için. app/push.py (VAPID web push) ile AYNI graceful-degradation deseni:
.env'de FIREBASE_SERVICE_ACCOUNT_JSON yoksa veya `firebase-admin` paketi
kurulu değilse özellik sessizce devre dışı kalır (servis hesabı anahtarı
henüz üretilmedi — bu dosya anahtar OLMADAN da app'i çökertmeden çalışmalı).
"""
import json
import os

FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

# Lazy-init bayrağı: firebase_admin.initialize_app() modül import edilirken
# DEĞİL, ilk gönderim çağrısında tetiklenir (Render gibi platformlarda env var
# henüz set edilmemişken import hatası app'i çökertmesin diye).
_initialized = False


def _ensure_initialized() -> bool:
    """firebase_admin uygulamasını (varsa) lazy başlatır. Başarılıysa True döner.

    Zaten initialize edilmişse tekrar ETMEZ (`firebase_admin._apps` dolu ise
    `initialize_app()` ValueError fırlatır — bu normal, yutulur).
    """
    global _initialized
    if _initialized:
        return True
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        return False

    try:
        if not firebase_admin._apps:
            cred_dict = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        _initialized = True
        return True
    except Exception:
        return False


def send_fcm_to_user(sb, user_id: str, title: str, body: str, url: str = "/") -> None:
    """Bir kullanıcının kayıtlı TÜM Android cihazlarına FCM bildirimi gönderir.

    FIREBASE_SERVICE_ACCOUNT_JSON yoksa veya `fcm_tokens` tablosu henüz
    oluşturulmamışsa sessizce hiçbir şey yapmaz — bu fonksiyon `notify()`
    içinden çağrılır ve gönderimdeki bir hata ASLA normal bildirim akışını
    (DB satırı oluşturma) kesintiye uğratmamalı (bkz. push.py docstring'i,
    AYNI gerekçe). Geçersiz/süresi dolmuş token'lar otomatik silinir.
    """
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        return
    try:
        from firebase_admin import messaging
    except ImportError:
        return

    if not _ensure_initialized():
        return

    try:
        tokens = sb.table("fcm_tokens").select(
            "id, token"
        ).eq("user_id", user_id).execute().data
    except Exception:
        return

    if not tokens:
        return

    for row in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={"url": url},
                token=row["token"],
            )
            messaging.send(message)
        except messaging.UnregisteredError:
            try:
                sb.table("fcm_tokens").delete().eq("id", row["id"]).execute()
            except Exception:
                pass
        except Exception:
            pass  # Tek bir cihaza gönderim hatası diğer cihazları etkilemesin


def send_call_wake_fcm(sb, user_id: str) -> None:
    """1:1 arama başlatılınca hedefi UYANDIRMAK için data-only, yüksek
    öncelikli bir FCM mesajı gönderir (2026-08-08, kullanıcı raporu: "bir
    süre sonra web'den mobile arama gelmiyor").

    1:1 arama sinyalleşmesi (offer/answer/ice) TAMAMEN client-to-client
    Supabase Realtime broadcast üzerinden yürüyor (bkz. app/realtime_topics.py,
    CallSignalingManager.kt) — backend bu akışa hiç KARIŞMAZ, sadece kanal
    adlarını üretir. Realtime broadcast KALICI DEĞİLDİR: alıcı o an bağlı
    değilse mesaj bir daha asla teslim edilmez. Android'in arka plan pil
    optimizasyonu bir süre sonra bu WebSocket'i sessizce bayatlatabiliyor —
    normal (4sn aralıklı) yeniden bağlanma döngüsü bunu YETERİNCE HIZLI
    yakalamayabilir. Bu fonksiyon `notification=` BLOĞU OLMADAN (sistem
    otomatik bir bildirim göstermesin, sessiz/data-only) gönderir — hedefin
    FcmService'i bunu görünce arama dinleyicisini ZORLA/ANINDA yeniden
    bağlar (bkz. FcmService.kt onMessageReceived). Arayan taraf, hedefin
    yeniden bağlanması için gereken birkaç saniyeyi karşılamak üzere offer
    broadcast'ini ZATEN periyodik tekrarlıyor (bkz. CallSessionManager.
    startCall() resend deseni) — bu ikisi BİRLİKTE çalışır.

    send_fcm_to_user() ile AYNI graceful-degradation (anahtar/tablo yoksa
    sessizce çık, tek cihaz hatası diğerlerini etkilemez) — arama sinyal
    akışını ASLA kesintiye uğratmamalı, bu yüzden HİÇBİR İSTİSNA çağırana
    yansıtılmaz.
    """
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        return
    try:
        from firebase_admin import messaging
    except ImportError:
        return

    if not _ensure_initialized():
        return

    try:
        tokens = sb.table("fcm_tokens").select(
            "id, token"
        ).eq("user_id", user_id).execute().data
    except Exception:
        return

    if not tokens:
        return

    for row in tokens:
        try:
            message = messaging.Message(
                data={"type": "incoming_call_wake"},
                token=row["token"],
                android=messaging.AndroidConfig(priority="high"),
            )
            messaging.send(message)
        except messaging.UnregisteredError:
            try:
                sb.table("fcm_tokens").delete().eq("id", row["id"]).execute()
            except Exception:
                pass
        except Exception:
            pass
