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
