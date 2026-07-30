"""Native Android için Supabase Auth (Realtime) oturumunun şifreli saklanması.

AYRI MODÜL (api_v1.py'ye değil) — gerekçe: api_v1.py zaten ~2900 satır ve bu
şifreleme yüzeyi (Fernet anahtarı, encrypt/decrypt, DB'ye yazma) davranış
olarak "bearer token doğrulama" (api_login_required) veya "iş mantığı"
endpoint'lerinden bağımsız, tek başına denetlenebilir küçük bir birim olmalı
— hemen ardından bir security incelemesinden geçecek (bkz. görev tanımı),
izole bir dosya o incelemeyi kolaylaştırır.

Mimari arka plan: web'in Realtime'ı (app/auth.py) kullanıcının GERÇEK
Supabase Auth JWT'sini session'da SAKLAR (Flask session, sunucu tarafında
şifrelenmiş cookie). Native'in HİÇ Flask session'ı yok — bunun yerine bu
çift (access_token/refresh_token) `api_tokens` tablosunda, Fernet ile
ŞİFRELİ olarak saklanır (sql/migration_api_tokens_realtime_session.sql,
commit dc4bafa) ki bir DB dump'ı tek başına bu token'ları kullanılabilir
kılmasın.

FAIL-OPEN gerekçesi: REALTIME_TOKEN_ENCRYPTION_KEY ortam değişkeni yoksa
(veya geçersizse) bu modüldeki HER fonksiyon exception fırlatmadan None/no-op
döner — Realtime o kullanıcı/cihaz için sessizce devre dışı kalır ama
login/register/google/realtime-token akışlarının GERİ KALANI asla 500
dönmez (proje felsefesi: "canlı kararlılık > özellik hızı", bkz. CLAUDE.md).
"""
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from .auth import _access_token_exp


def _get_fernet() -> "Fernet | None":
    """REALTIME_TOKEN_ENCRYPTION_KEY'den bir Fernet örneği kurar.

    current_app.config'e KOYULMADI (bilinçli): bu modülün her fonksiyonu
    app context olmadan da (örn. testlerde saf unit-test olarak) çağrılabilsin
    diye os.environ doğrudan okunur — app/config.py'deki diğer SUPABASE_*
    değişkenlerinin aksine burada current_app'e bağımlılık YOK.
    """
    import os
    key = os.environ.get("REALTIME_TOKEN_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception:
        # Anahtar formatı geçersiz (Fernet-uyumlu base64 değil) — Realtime
        # sessizce devre dışı kalır, exception dışarı SIZMAZ.
        return None


def _encrypt_token(raw: str) -> "str | None":
    """Ham bir Supabase Auth token'ı (access veya refresh) şifreler.

    Fernet yoksa/kurulamıyorsa None döner (çağıran taraf bunu "bu satır için
    Realtime saklanamadı" olarak yorumlar, hata FIRLATMAZ)."""
    if not raw:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return None
    try:
        return fernet.encrypt(raw.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def _decrypt_token(enc: "str | None") -> "str | None":
    """Şifreli bir token'ı çözer.

    Fernet yoksa VEYA decrypt patlarsa (InvalidToken — anahtar rotasyonu ya
    da bozuk veri) None döner; exception ASLA çağırana sızmaz (bu, /realtime-
    token endpoint'inin "unavailable" 503'e sessizce düşmesini sağlayan tek
    kontrol noktasıdır)."""
    if not enc:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def _store_realtime_session(sb, token_hash: str, access_token: "str | None",
                             refresh_token: "str | None") -> None:
    """Bir api_tokens satırına şifreli Realtime oturumunu yazar (no-op fail-open).

    access_token/refresh_token'dan biri eksikse VEYA Fernet yoksa VEYA
    şifreleme başarısız olursa NO-OP (sessizce) — bu fonksiyon login/register/
    google akışlarının ÜÇÜNDEN de "iş bitince çağrılır, sonucu asla kontrol
    edilmez" şeklinde kullanılır; token issuance'ın (asıl kritik akış) hiçbir
    zaman bu adım yüzünden 500 dönmemesi gerekiyor (fail-open, bkz. modül
    docstring'i).
    """
    if not access_token or not refresh_token:
        return
    enc_access = _encrypt_token(access_token)
    enc_refresh = _encrypt_token(refresh_token)
    if not enc_access or not enc_refresh:
        return

    try:
        exp = _access_token_exp(access_token)
        # exp=0 → JWT parse edilemedi (beklenmez ama _access_token_exp
        # exception yutuyor) — bu durumda expires_at NULL bırakılır ki
        # /realtime-token bunu "süresi belirsiz" sayıp HER seferinde
        # yenilemeye çalışsın (bilinmeyen bir süreye güvenmektense daha güvenli).
        expires_at = (
            datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
            if exp > 0 else None
        )
        sb.table("api_tokens").update({
            "sb_access_token_enc": enc_access,
            "sb_refresh_token_enc": enc_refresh,
            "sb_token_expires_at": expires_at,
        }).eq("token_hash", token_hash).execute()
    except Exception:
        # DB yazımı başarısız (geçici ağ hatası vb.) — login/register/google
        # akışının TAMAMI bu yüzden 500 DÖNMEMELİ, sadece Realtime o oturum
        # için kaydedilmemiş sayılır.
        pass
