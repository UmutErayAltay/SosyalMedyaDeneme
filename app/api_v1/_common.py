"""api_v1/ paketindeki tüm alt-modüllerin paylaştığı küçük yardımcılar.

Bu dosya sadece bp'ye route TANIMLAMAZ — sadece paylaşılan helper/decorator'ları
barındırır (routes/_common.py ile AYNI desen, bkz. app/routes/_common.py docstring'i).
"""
import hashlib
from datetime import datetime, timezone
from functools import wraps

from flask import request, jsonify

from ..supabase_client import get_sb
from ..routes._common import PAGE_SIZE

API_DEFAULT_LIMIT = PAGE_SIZE
API_MAX_LIMIT = 50


def _str_field(data: dict, key: str) -> str:
    """JSON body alanını güvenle str'e çevirir.

    request.get_json() güvenilmeyen istemciden gelir — bir alan int/list/dict
    olarak gönderilirse çıplak `.strip()` AttributeError fırlatıp 500 döndürür
    (JSON-only sözleşmeyi bozar, native client'ın parser'ını kırar). Str
    olmayan değerler boş string sayılır (aşağıdaki "missing_credentials"
    kontrolüyle zaten reddedilir).
    """
    v = data.get(key)
    return v.strip() if isinstance(v, str) else ""


def _hash_token(raw_token: str) -> str:
    """Ham bearer token'ı DB'de saklanan hash'e çevirir.

    sha256 yeterli (bcrypt/argon2 GEREKMEZ): bu bir kullanıcı parolası değil,
    secrets.token_urlsafe(32) ile üretilmiş yüksek entropili opak bir değer —
    sözlük/brute-force saldırısına açık değil, tersine mühendislikle tahmin
    edilemez.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def api_login_required(view):
    """Authorization: Bearer <token> header'ını doğrular, request.api_user set eder.

    Token bulunamaz/iptal edilmişse 401 döner. session["user"] (web) ile
    KARIŞTIRILMAZ — bu tamamen ayrı, stateless bir auth yolu.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify(error="unauthorized"), 401
        raw_token = auth_header[len("Bearer "):].strip()
        if not raw_token:
            return jsonify(error="unauthorized"), 401

        token_hash = _hash_token(raw_token)
        sb = get_sb()
        try:
            rows = sb.table("api_tokens").select("*").eq(
                "token_hash", token_hash
            ).is_("revoked_at", "null").execute().data
        except Exception:
            # Supabase'e GEÇİCİ erişim hatası — token'ın geçersiz olduğu
            # anlamına GELMEZ. "unauthorized" dönersek native taraf
            # (FeedViewModel.refresh/loadMore, result.code=="unauthorized")
            # token'ı SİLİP kullanıcıyı login'e atıyor — geçici bir ağ/DB
            # hatası yüzünden gerçek bir çıkış yaşanmaması için ayrı kod
            # (kullanıcı raporu: "uygulamayı açtıkça çıkış yapıyor" — cold-start'taki
            # ilk istek transient bağlantı hatasına diğerlerinden daha açık).
            return jsonify(error="service_unavailable"), 503
        if not rows:
            return jsonify(error="unauthorized"), 401
        token_row = rows[0]

        try:
            prof = sb.table("profiles").select(
                "id, email, username, avatar_url, is_admin"
            ).eq("id", token_row["user_id"]).execute().data
        except Exception:
            return jsonify(error="service_unavailable"), 503
        if not prof:
            return jsonify(error="unauthorized"), 401
        prof_data = prof[0]

        # session["user"] şekliyle tutarlı: id/email/username/is_admin
        request.api_user = {
            "id": prof_data["id"],
            "email": prof_data.get("email"),
            "username": prof_data.get("username"),
            "avatar_url": prof_data.get("avatar_url"),
            "is_admin": bool(prof_data.get("is_admin")),
        }
        request.api_token_hash = token_hash

        # last_used_at güncellemesi senkron/blocking — düşük hacimli bir
        # auth-check adımı, _notify_pool gibi arkaplana almaya gerek yok.
        try:
            sb.table("api_tokens").update({
                "last_used_at": datetime.now(timezone.utc).isoformat()
            }).eq("token_hash", token_hash).execute()
        except Exception:
            pass

        return view(*args, **kwargs)
    return wrapped
