"""JSON REST API katmanı — Faz 1 (native Android yol haritası).

Mevcut web mimarisine (session-cookie + CSRF, app/auth.py + app/decorators.py)
HİÇ DOKUNMADAN, token tabanlı (Bearer) auth ile EKLEME (additive) bir katman.
Hiçbir HTML route/template etkilenmez; bu blueprint sadece /api/v1 altında
yaşar ve kendi auth mekanizmasını (api_tokens tablosu, sql/migration_api_tokens.sql)
kullanır.

CSRF muafiyeti: app/__init__.py'deki csrf_protect() bu blueprint'in tüm
endpoint'lerini (request.endpoint.startswith("api_v1.")) muaf tutar — CSRF,
tarayıcının bir isteğe OTOMATİK olarak ambient credential (cookie) eklemesinden
kaynaklanan bir saldırıdır; Bearer token header'ı üçüncü-parti bir site/form
tarafından OTOMATİK eklenemez, bu yüzden token-authenticated endpoint'ler
doğaları gereği CSRF'e bağışıktır.

2FA: aktif TOTP'si olan bir hesap API üzerinden login olmaya çalışırsa VE
istek body'sinde `code` yoksa, şifre doğru olsa bile ham token ÜRETİLMEZ
(403 mfa_required) — bu SESSİZCE bypass edilmez, client'a açıkça bildirilir.
Client AYNI isteği `code` alanıyla tekrar atar (tek endpoint, iki deneme —
ayrı bir "2fa doğrulama" endpoint'i YOK). Enroll/disable/status için ayrı
"2FA (TOTP)" bölümüne bak (Faz 4, native Android post-parity).

Kayıt: POST /auth/register — auth.py register()'ın çekirdek mantığı, ama
username-çakışması ÖNDEN kontrol edilir (register()'daki sessiz yutma YOK)
ve otomatik giriş için ayrı bir sign_in_with_password YAPILMAZ (admin.
create_user() zaten user.id döndürür, token doğrudan bununla üretilir).

Google ile giriş: POST /auth/google — web'in tarayıcı-redirect akışından
FARKLI, Android Credential Manager'ın ürettiği bir Google ID token'ı
`sign_in_with_id_token()` ile Supabase'e değiştirir (bkz. api_google_login()
docstring'i). Supabase'in Google provider Client ID'si web ile PAYLAŞILIR.
"""
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, current_app
from supabase import create_client

from .supabase_client import get_sb, call_with_ssl_retry, _CONNECTION_ERRORS
from .rate_limit import is_rate_limited
from .routes._common import _attach_post_metrics, attach_repost_of, PAGE_SIZE, _profile
from .visibility import visible_or_filter, followed_and_self_ids, close_friend_author_ids, filter_visible
from .blocks import blocked_user_ids, filter_not_blocked, has_blocked, is_blocked_either_way
from .mutes import muted_user_ids
from .polls import attach_polls
from .presence import is_online
from .notifications import notify, NOTIFICATION_TYPES
from .cache import invalidate
from .mentions import notify_mentions
from .messaging._common import (
    _build_convos, _get_or_create_conversation, _mark_read,
    _mark_message_notifications_read, _notify_conversation,
)
from .messaging.views import MESSAGE_PAGE, _write_pool
from .routes._common import _can_view_post
from .social import REACTIONS, _find_recent_duplicate_comment, _notify_pool
from .post_views import record_view
from .storage_helper import upload_image
from .hashtags import sync_post_hashtags, notify_hashtag_followers, extract_hashtags
from .auth import _unique_username, _has_verified_totp

bp = Blueprint("api_v1", __name__)

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
            return jsonify(error="unauthorized"), 401
        if not rows:
            return jsonify(error="unauthorized"), 401
        token_row = rows[0]

        try:
            prof = sb.table("profiles").select(
                "id, email, username, avatar_url, is_admin"
            ).eq("id", token_row["user_id"]).execute().data
        except Exception:
            prof = None
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


@bp.route("/auth/register", methods=["POST"])
def api_register():
    """Yeni hesap oluştur — auth.py register()'ın AYNI çekirdek mantığı
    (admin.create_user + profiles upsert), ama register()'dan farklı olarak:
    1) SESSİZCE yutulan username-çakışması yerine önden bir uniqueness
       kontrolü yapılır (api_profile_edit()'teki AYNI desen — register()'ın
       "trigger zaten oluşturmuş olabilir" try/except'i, username zaten
       alınmışsa upsert'in username constraint'ine çarpıp sessizce
       yutulmasına açık; native'de bunu ÖNDEN engellemek daha doğru).
    2) Otomatik giriş için AYRI bir sign_in_with_password YAPILMAZ — admin.
       create_user() zaten user.id döndürüyor, native token'ı DOĞRUDAN bu
       id ile üretilir (login()'in aksine burada bir Supabase Auth session'ı
       hiç gerekmiyor, api_tokens tablosu sadece user_id ister).
    """
    if is_rate_limited(f"register:{request.remote_addr or 'unknown'}", 5, 600):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    email = _str_field(data, "email")
    password = _str_field(data, "password")
    username = _str_field(data, "username")
    device_name = _str_field(data, "device_name") or None

    if not email or not password or not username:
        return jsonify(error="missing_fields"), 400
    if len(username) < 3:
        return jsonify(error="short_username"), 400

    sb = get_sb()
    try:
        taken = sb.table("profiles").select("id").eq("username", username).execute()
        if taken.data:
            return jsonify(error="username_taken"), 400
    except Exception:
        pass

    try:
        res = sb.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"username": username},
        })
        user = getattr(res, "user", None)
    except Exception as e:
        msg = str(e)
        if "already" in msg.lower() or "registered" in msg.lower():
            return jsonify(error="email_taken"), 400
        return jsonify(error="register_failed"), 500

    if not user:
        return jsonify(error="register_failed"), 500

    try:
        sb.table("profiles").upsert({
            "id": user.id,
            "username": username,
            "email": email,
            "is_private": True,
        }, on_conflict="id").execute()
    except Exception:
        return jsonify(error="register_failed"), 500

    invalidate("valid_usernames")

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    try:
        sb.table("api_tokens").insert({
            "user_id": user.id,
            "token_hash": token_hash,
            "device_name": device_name,
        }).execute()
    except Exception:
        return jsonify(error="token_creation_failed"), 500

    return jsonify(
        token=raw_token,
        user={
            "id": user.id,
            "email": email,
            "username": username,
            "avatar_url": None,
            "is_admin": False,
        },
    )


@bp.route("/auth/login", methods=["POST"])
def login():
    """E-posta/şifre ile giriş → başarılıysa ham bearer token üretir (bir kez döner)."""
    # Web login()'le AYNI anahtar kullanılır (ayrı bir "api_login:" anahtarı
    # aynı IP'ye toplamda 10 yerine 20 deneme hakkı verirdi — security
    # incelemesinde bulundu, bilerek birleştirildi).
    if is_rate_limited(f"login:{request.remote_addr or 'unknown'}", 10, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    email = _str_field(data, "email")
    password = _str_field(data, "password")
    device_name = _str_field(data, "device_name") or None
    code = _str_field(data, "code")

    if not email or not password:
        return jsonify(error="missing_credentials"), 400

    try:
        tmp_auth = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        res = call_with_ssl_retry(
            lambda: tmp_auth.auth.sign_in_with_password({
                "email": email, "password": password,
            })
        )
    except Exception:
        return jsonify(error="invalid_credentials"), 401

    user = getattr(res, "user", None)
    sess = getattr(res, "session", None)
    if not user or not sess or not getattr(sess, "access_token", None):
        return jsonify(error="invalid_credentials"), 401

    sb = get_sb()
    try:
        prof = sb.table("profiles").select(
            "is_banned, username, avatar_url, is_admin"
        ).eq("id", user.id).execute().data
        prof_data = prof[0] if prof else {}
    except Exception:
        prof_data = {}

    if prof_data.get("is_banned"):
        return jsonify(error="banned"), 403

    # 2FA kontrolü — login()'deki (app/auth.py) aynı desenle: aktif/verified
    # TOTP factor varsa, ŞİFRE DOĞRU OLSA BİLE `code` gelmediği sürece token
    # üretilmez. `tmp` (session set edilmiş client) burada AÇIK bırakılır —
    # aşağıda code varsa AYNI client challenge/verify için reuse edilir (yeni
    # bir sign_in_with_password GEREKMEZ, bu noktada zaten canlı bir Auth
    # session'ımız var).
    has_totp = False
    totp_factor = None
    tmp = None
    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        tmp.auth.set_session(sess.access_token, getattr(sess, "refresh_token", None))
        factors = tmp.auth.mfa.list_factors()
        for f in (factors.totp or []):
            if f.factor_type == "totp" and f.status == "verified":
                totp_factor = f
                has_totp = True
                break
    except Exception:
        # MFA kontrolü başarısız — 2FA yok sayılır (auth.py login()'deki fail-open
        # davranışın aynısı; kontrol servisi geçiciyse giriş engellenmemeli)
        pass

    if has_totp and not code:
        return jsonify(
            error="mfa_required",
            message="Bu hesapta 2FA aktif. Aynı isteği `code` alanıyla tekrar gönder.",
        ), 403

    if has_totp and code:
        try:
            challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
            tmp.auth.mfa.verify({
                "factor_id": totp_factor.id,
                "challenge_id": challenge_resp.id,
                "code": code,
            })
        except Exception:
            # Ekstra rate limit gerekmez — üstteki login:{ip} limiter (satır ~148)
            # zaten her denemeyi (kod yanlış olsa bile) sayıyor.
            return jsonify(error="invalid_code"), 401

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    try:
        sb.table("api_tokens").insert({
            "user_id": user.id,
            "token_hash": token_hash,
            "device_name": device_name,
        }).execute()
    except Exception:
        return jsonify(error="token_creation_failed"), 500

    return jsonify(
        token=raw_token,
        user={
            "id": user.id,
            "email": user.email,
            "username": prof_data.get("username"),
            "avatar_url": prof_data.get("avatar_url"),
            "is_admin": bool(prof_data.get("is_admin")),
        },
    )


@bp.route("/auth/google", methods=["POST"])
def api_google_login():
    """Google ile giriş — web'in tarayıcı-yönlendirmeli auth.py google_complete()
    akışından MİMARİ OLARAK FARKLI: web bir Supabase OAuth authorize-redirect
    akışıyla (tarayıcı) ELDE EDİLMİŞ bir access_token'ı doğrular; native ise
    Android Credential Manager'ın DOĞRUDAN ürettiği bir Google ID token'ı
    (OIDC JWT) `sign_in_with_id_token()` ile Supabase'e DEĞİŞTİRİR (tarayıcı/
    redirect adımı hiç yok). Supabase'in Google provider ayarı (Client ID)
    web ile PAYLAŞILIR — native için ayrı bir OAuth client kaydı YOK.

    `nonce` opsiyonel: Android Credential Manager `GetSignInWithGoogleOption`
    ile hash'lenmiş bir nonce gönderip ID token'a gömebilir; client bunu ham
    haliyle buraya da yollarsa Supabase token'daki hash'lenmiş nonce'la
    karşılaştırıp replay saldırısına karşı ekstra doğrulama yapar. Native
    Kotlin tarafı bunu henüz göndermeyebilir — bu durumda Supabase nonce
    kontrolünü atlar (dict'te hiç anahtar yoksa sorun çıkmaz).
    """
    if is_rate_limited(f"login:{request.remote_addr or 'unknown'}", 10, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    id_token = _str_field(data, "id_token")
    nonce = _str_field(data, "nonce")
    code = _str_field(data, "code")
    device_name = _str_field(data, "device_name") or None

    if not id_token:
        return jsonify(error="missing_token"), 400

    credentials = {"provider": "google", "token": id_token}
    if nonce:
        credentials["nonce"] = nonce

    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        res = call_with_ssl_retry(lambda: tmp.auth.sign_in_with_id_token(credentials))
    except Exception:
        return jsonify(error="invalid_token"), 401

    user = getattr(res, "user", None)
    sess = getattr(res, "session", None)
    if not user or not sess or not getattr(sess, "access_token", None):
        return jsonify(error="invalid_token"), 401

    sb = get_sb()
    try:
        prof = sb.table("profiles").select(
            "is_banned, username, avatar_url, is_admin"
        ).eq("id", user.id).execute().data
        prof_data = prof[0] if prof else {}
    except Exception:
        prof_data = {}

    if prof_data.get("is_banned"):
        return jsonify(error="banned"), 403

    # İlk Google girişi: profiles satırı yoksa oluştur — auth.py
    # google_complete()'teki (satır 368-386) AYNI mantık, AYNI yardımcı
    # (_unique_username) reuse edilir.
    if not prof:
        meta = getattr(user, "user_metadata", None) or {}
        try:
            sb.table("profiles").upsert({
                "id": user.id,
                "username": _unique_username(sb, user.email),
                "email": user.email,
                "full_name": meta.get("full_name") or meta.get("name"),
                "avatar_url": meta.get("avatar_url") or meta.get("picture"),
                "is_private": True,
            }, on_conflict="id").execute()
            invalidate("valid_usernames")
        except Exception:
            pass
        try:
            prof = sb.table("profiles").select(
                "is_banned, username, avatar_url, is_admin"
            ).eq("id", user.id).execute().data
            prof_data = prof[0] if prof else {}
        except Exception:
            prof_data = {}

    # 2FA paritesi — login()'deki AYNI desen: ZATEN canlı olan bu session
    # (sign_in_with_id_token'dan) reuse edilir, ayrı bir sign-in gerekmez.
    has_totp = False
    totp_factor = None
    try:
        factors = tmp.auth.mfa.list_factors()
        for f in (factors.totp or []):
            if f.factor_type == "totp" and f.status == "verified":
                totp_factor = f
                has_totp = True
                break
    except Exception:
        pass

    if has_totp and not code:
        return jsonify(
            error="mfa_required",
            message="Bu hesapta 2FA aktif. Aynı isteği `code` alanıyla tekrar gönder.",
        ), 403

    if has_totp and code:
        try:
            challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
            tmp.auth.mfa.verify({
                "factor_id": totp_factor.id,
                "challenge_id": challenge_resp.id,
                "code": code,
            })
        except Exception:
            return jsonify(error="invalid_code"), 401

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    try:
        sb.table("api_tokens").insert({
            "user_id": user.id,
            "token_hash": token_hash,
            "device_name": device_name,
        }).execute()
    except Exception:
        return jsonify(error="token_creation_failed"), 500

    return jsonify(
        token=raw_token,
        user={
            "id": user.id,
            "email": user.email,
            "username": prof_data.get("username"),
            "avatar_url": prof_data.get("avatar_url"),
            "is_admin": bool(prof_data.get("is_admin")),
        },
    )


@bp.route("/auth/logout", methods=["POST"])
@api_login_required
def logout():
    """Sadece bu isteğin taşıdığı token'ı iptal eder (diğer cihazlar etkilenmez)."""
    sb = get_sb()
    try:
        sb.table("api_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat()
        }).eq("token_hash", request.api_token_hash).execute()
    except Exception:
        return jsonify(error="logout_failed"), 500
    return jsonify(ok=True)


@bp.route("/auth/me")
@api_login_required
def me():
    return jsonify(user=request.api_user)


@bp.route("/feed")
@api_login_required
def feed():
    """Sayfalanmış ana akış — app/routes/posts.py feed()'in AYNI RPC+fallback
    deseni (feed_page_posts RPC, başarısızsa çok-sorgulu Python yolu), ama
    JSON döner (HTML render yok). Post şekli _attach_post_metrics()/
    enrich_post_json() sözleşmesiyle AYNI (bkz. _common.py docstring'i) —
    burada ayrı bir serialization YAZILMADI, tutarsızlık riski taşımasın diye.
    """
    sb = get_sb()
    me_id = request.api_user["id"]

    try:
        limit = int(request.args.get("limit", API_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = API_DEFAULT_LIMIT
    limit = max(1, min(limit, API_MAX_LIMIT))

    try:
        cursor = int(request.args.get("cursor", 0))
    except (TypeError, ValueError):
        cursor = 0
    cursor = max(cursor, 0)

    try:
        posts = sb.rpc("feed_page_posts", {
            "p_me": me_id, "p_offset": cursor, "p_limit": limit + 1,
        }).execute().data or []
    except Exception:
        posts = None

    if posts is None:
        # Fallback: RPC henüz uygulanmamış/başarısız — posts.py feed()'deki
        # çok-sorgulu yolla BİREBİR aynı filtre sırası (görünürlük + engel +
        # sessize alma SQL seviyesinde, gizli-profil/deaktif Python'da).
        select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url, is_private, is_deactivated), "
                       "likes(count), comments(count)")
        blocked_ids_fb = blocked_user_ids(sb, me_id)
        muted_ids_fb = muted_user_ids(sb, me_id)
        try:
            query = sb.table("posts").select(select_cols).or_(
                visible_or_filter(sb, me_id)
            ).eq("is_draft", False).eq("is_archived", False)
            if blocked_ids_fb:
                query = query.not_.in_("user_id", list(blocked_ids_fb))
            if muted_ids_fb:
                query = query.not_.in_("user_id", list(muted_ids_fb))
            posts = query.order("created_at", desc=True).range(cursor, cursor + limit).execute().data
        except Exception:
            # sql/migration_post_visibility.sql henüz uygulanmamışsa 'visibility'
            # kolonu yok — filtresiz eski davranışa düş (feed asla kırılmasın)
            posts = sb.table("posts").select(select_cols).order(
                "created_at", desc=True
            ).range(cursor, cursor + limit).execute().data

        visible_author_ids = followed_and_self_ids(sb, me_id)
        if posts:
            author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
            is_private_map = {}
            is_deactivated_map = {}
            if author_ids:
                try:
                    profiles = sb.table("profiles").select(
                        "id, is_private, is_deactivated"
                    ).in_("id", list(author_ids)).execute().data
                    is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
                    is_deactivated_map = {p["id"]: p.get("is_deactivated", False) for p in profiles}
                except Exception:
                    pass
            posts = [p for p in posts if not (
                is_deactivated_map.get(p.get("user_id"), False) or
                (is_private_map.get(p.get("user_id"), False) and p.get("user_id") != me_id and p.get("user_id") not in visible_author_ids)
            )]

        has_next = len(posts) > limit
        posts = posts[:limit]
        _attach_post_metrics(sb, posts, me_id)
        attach_polls(sb, posts, me_id)
    else:
        has_next = len(posts) > limit
        posts = posts[:limit]
        # RPC yolu: sayaçlar/anket RPC'den (enrich_post_json) hazır geliyor,
        # sadece repost orijinali ekleniyor (posts.py feed()'deki aynı ayrım).
        attach_repost_of(sb, posts)

    return jsonify(
        posts=posts,
        has_next=has_next,
        next_cursor=(cursor + limit) if has_next else None,
    )


@bp.route("/discover")
@api_login_required
def discover():
    """Algoritmik keşfet — app/routes/discovery.py discover()'ın AYNI RPC+fallback
    deseni (discover_page_posts RPC, başarısızsa son 7 gün Python fallback'i),
    ama JSON döner (HTML render yok). Post şekli /api/v1/feed ile AYNI sözleşme
    (_attach_post_metrics/enrich_post_json) — burada da ayrı bir serialization
    YAZILMADI.
    """
    sb = get_sb()
    me = request.api_user["id"]

    # Sayfalama: discover()'daki ile BİREBİR aynı üst sınır (100_000) — aşırı
    # büyük bir page değeri offset'i Postgres int4 sınırına yaklaştırıp RPC'yi
    # "integer out of range" ile patlatabilir, bu da geniş except'in bunu
    # "migration uygulanmamış" sanıp pahalı tam-tablo fallback'ine düşmesine
    # yol açar (kaynak tüketimi/DoS vektörü).
    page = max(1, min(request.args.get("page", 1, type=int), 100_000))
    offset = (page - 1) * PAGE_SIZE

    try:
        posts = sb.rpc("discover_page_posts", {
            "p_me": me, "p_limit": PAGE_SIZE + 1, "p_offset": offset
        }).execute().data or []
    except Exception:
        posts = None

    if posts is not None:
        # RPC yolu: sayaçlar/anket RPC'den hazır gelir, _attach_post_metrics
        # ÇAĞRILMAZ (olmayan `likes` embed'inden yeniden hesaplayıp sıfırlardı).
        attach_repost_of(sb, posts)
    else:
        # Fallback: discover()'daki çok-sorgulu yolla BİREBİR aynı filtre sırası.
        exclude_ids = followed_and_self_ids(sb, me)
        blocked_ids = blocked_user_ids(sb, me)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url, is_private, is_deactivated), "
                       "likes(count), comments(count)")
        try:
            posts = sb.table("posts").select(select_cols).gte(
                "created_at", cutoff
            ).eq("visibility", "public").eq("is_draft", False).eq("is_archived", False).execute().data
        except Exception:
            posts = sb.table("posts").select(select_cols).gte("created_at", cutoff).execute().data

        posts = [p for p in posts if p["user_id"] not in exclude_ids]
        close_friend_ids = close_friend_author_ids(sb, me)
        posts = filter_visible(sb, posts, exclude_ids, close_friend_ids, me)
        posts = filter_not_blocked(posts, blocked_ids)

        if posts:
            author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
            is_private_map = {}
            is_deactivated_map = {}
            if author_ids:
                try:
                    profiles = sb.table("profiles").select(
                        "id, is_private, is_deactivated"
                    ).in_("id", list(author_ids)).execute().data
                    is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
                    is_deactivated_map = {p["id"]: p.get("is_deactivated", False) for p in profiles}
                except Exception:
                    pass
            posts = [p for p in posts if not (
                is_deactivated_map.get(p.get("user_id"), False) or
                (is_private_map.get(p.get("user_id"), False) and p.get("user_id") != me and p.get("user_id") not in exclude_ids)
            )]

        def _attach_metrics():
            _attach_post_metrics(sb, posts, me)

        def _attach_polls_fn():
            attach_polls(sb, posts, me)

        with ThreadPoolExecutor(max_workers=2) as executor:
            metrics_future = executor.submit(_attach_metrics)
            polls_future = executor.submit(_attach_polls_fn)
            metrics_future.result()
            polls_future.result()

        for p in posts:
            p["_score"] = (p.get("like_count") or 0) + (p.get("comment_count") or 0)
        posts.sort(key=lambda p: p["_score"], reverse=True)
        posts = posts[offset:offset + PAGE_SIZE + 1]

    # has_more: discover()'daki ile aynı desen — PAGE_SIZE+1 istenip fazlası
    # dönmüşse daha var demektir (tam katlarda yanlış sinyal veren
    # len(posts) == PAGE_SIZE off-by-one'ını önler).
    has_more = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]

    return jsonify(posts=posts, has_more=has_more, page=page)


def _api_recent_searches(sb, me: str) -> list:
    """Son aramalar — discovery.py _recent_searches ile AYNI mantık (ayrı bir
    davranış İCAT edilmedi, sadece bu modülde bağımsız bir kopya tutulur ki
    api_v1.py discovery.py'nin private/altçizgili yardımcılarına import ile
    bağımlı hale gelmesin — feed()'in posts.py'ye değil sadece paylaşılan
    genel yardımcılara bağımlı olma deseniyle tutarlı). search_history
    migration'ı henüz uygulanmamışsa boş liste döner, endpoint kırılmaz.
    """
    try:
        return sb.table("search_history").select("*").eq(
            "user_id", me
        ).order("created_at", desc=True).limit(10).execute().data
    except Exception:
        return []


def _api_saved_searches(sb, me: str) -> list:
    """Kayıtlı aramalar — discovery.py _saved_searches ile AYNI mantık (bkz.
    _api_recent_searches docstring'i — bağımsız kopya tutulma gerekçesi)."""
    try:
        return sb.table("saved_searches").select("*").eq(
            "user_id", me
        ).order("created_at", desc=True).limit(20).execute().data
    except Exception:
        return []


@bp.route("/search")
@api_login_required
def search():
    """Arama — app/routes/discovery.py search()'in AYNI mantığı, JSON döner.

    q 2 karakterden kısaysa asıl arama YAPILMAZ (web ile aynı davranış),
    sadece recent/saved searches döner — ama bunlar HER ZAMAN dahil edilir
    (q kısa olsa bile), native client arama geçmişini/kayıtlı aramaları tek
    istekle gösterebilsin diye.
    """
    q = request.args.get("q", "").strip()
    search_type = request.args.get("type", "all")
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    sb = get_sb()
    me = request.api_user["id"]

    if len(q) < 2:
        return jsonify(
            users=[], posts=[], hashtags=[],
            recent_searches=_api_recent_searches(sb, me),
            saved_searches=_api_saved_searches(sb, me),
        )

    blocked_ids = blocked_user_ids(sb, me)

    # type filtresine göre gereksiz sorgu atlanır (discovery.py search() ile aynı).
    users = []
    if search_type in ("all", "users"):
        q_escaped = q.replace(",", "").replace(")", "")
        users = sb.table("profiles").select(
            "id, username, full_name, avatar_url, is_deactivated"
        ).or_(
            f"username.ilike.%{q_escaped}%,full_name.ilike.%{q_escaped}%"
        ).limit(20).execute().data
        users = [u for u in users if u["id"] not in blocked_ids and not u.get("is_deactivated", False)]

    posts = []
    if search_type in ("all", "posts"):
        posts_query = sb.table("posts").select(
            "*, profiles!posts_user_id_fkey(username, avatar_url, is_deactivated), likes(count), comments(count)"
        ).ilike("content", f"%{q}%").eq("is_draft", False).eq("is_archived", False)
        if date_from:
            posts_query = posts_query.gte("created_at", date_from)
        if date_to:
            posts_query = posts_query.lte("created_at", f"{date_to}T23:59:59")
        posts = posts_query.order("created_at", desc=True).limit(50).execute().data
        posts = [p for p in posts if not p.get("is_draft")]
        posts = [p for p in posts if not (p.get("profiles") and p["profiles"].get("is_deactivated"))]
        posts = filter_visible(sb, posts, followed_and_self_ids(sb, me), close_friend_author_ids(sb, me), me)
        posts = filter_not_blocked(posts, blocked_ids)
        _attach_post_metrics(sb, posts, me)
        attach_polls(sb, posts, me)

    hashtags = []
    if search_type in ("all", "hashtags"):
        try:
            tag_q = q[1:] if q.startswith("#") else q
            tag_rows = sb.table("hashtags").select("id, tag").ilike(
                "tag", f"%{tag_q}%"
            ).limit(20).execute().data
            if tag_rows:
                tag_ids = [h["id"] for h in tag_rows]
                post_counts = sb.table("post_hashtags").select(
                    "hashtag_id"
                ).in_("hashtag_id", tag_ids).execute().data
                counts = {}
                for pc in post_counts:
                    counts[pc["hashtag_id"]] = counts.get(pc["hashtag_id"], 0) + 1
                for h in tag_rows:
                    hashtags.append({"tag": h["tag"], "count": counts.get(h["id"], 0)})
        except Exception:
            hashtags = []  # migration_hashtags.sql henüz uygulanmamışsa boş liste

    # Arama geçmişine kaydet — discovery.py search() ile aynı: aynı sorgu
    # varsa eskisi silinir (tekilleşip en üste taşınsın diye).
    try:
        sb.table("search_history").delete().eq("user_id", me).eq("query", q).execute()
        sb.table("search_history").insert({"user_id": me, "query": q}).execute()
    except Exception:
        pass

    return jsonify(
        users=users, posts=posts, hashtags=hashtags,
        recent_searches=_api_recent_searches(sb, me),
        saved_searches=_api_saved_searches(sb, me),
    )


@bp.route("/search/save", methods=["POST"])
@api_login_required
def save_search():
    """Kullanıcının aramasını saved_searches tablosuna kaydeder.

    Web save_search() (discovery.py) boş q'da sessizce redirect eder (form
    submit akışı); JSON API'de kaydedilecek bir şey olmadığını client'a AÇIKÇA
    bildirmek için 400 döner — bu bilinçli bir sapma, davranış farklı değil,
    sadece hata sinyalleme biçimi (redirect yerine status code).
    """
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    q = _str_field(data, "q")
    label = _str_field(data, "label") or None

    if not q:
        return jsonify(error="missing_query"), 400

    sb = get_sb()
    sb.table("saved_searches").insert({
        "user_id": me,
        "query": q,
        "label": label,
    }).execute()

    return jsonify(ok=True)


@bp.route("/search/history/clear", methods=["POST"])
@api_login_required
def clear_search_history():
    me = request.api_user["id"]
    get_sb().table("search_history").delete().eq("user_id", me).execute()
    return jsonify(ok=True)


@bp.route("/search/history/<item_id>/delete", methods=["POST"])
@api_login_required
def delete_search_history_item(item_id):
    me = request.api_user["id"]
    # Uygulama katmanı sahiplik kontrolü: sadece kendi geçmiş satırını sil
    get_sb().table("search_history").delete().eq("id", item_id).eq("user_id", me).execute()
    return jsonify(ok=True)


@bp.route("/search/saved/<item_id>/delete", methods=["POST"])
@api_login_required
def delete_saved_search_item(item_id):
    me = request.api_user["id"]
    # Uygulama katmanı sahiplik kontrolü: sadece kendi kayıtlı aramasını sil
    get_sb().table("saved_searches").delete().eq("id", item_id).eq("user_id", me).execute()
    return jsonify(ok=True)


# ----------------------- PROFİL (Faz 3, native Android profil ekranı) -----------------------
# app/routes/profile.py + app/social.py'nin AYNI iş mantığı JSON'a taşınır.
# BİLİNÇLİ kapsam dışı (ayrı bir sonraki iterasyon): highlights, bookmark_collections,
# media_posts (client-side filtrelenebilir, ayrı alan gerekmez), profil DÜZENLEME
# (bio/avatar/kullanıcı adı/2FA/bildirim tercihleri/deaktivasyon).

def _serialize_profile_for_api(prof: dict, is_self: bool) -> dict:
    """profiles satırından JSON'a güvenle konabilecek alanları seçer.

    Web tarafı `prof` sözlüğünü OLDUĞU GİBİ render_template()'e geçiriyor
    (Jinja sadece kullandığı alana erişir, `email` hiç render edilmez) — ama
    JSON API'de aynı sözlüğü doğrudan dönmek başka bir kullanıcının `email`/
    `is_banned` gibi alanlarını sızdırır. Bu yüzden burada CURATE edilir
    (web davranışından bilinçli bir sapma, güvenlik gerekçesiyle).
    """
    fields = {
        "id": prof.get("id"),
        "username": prof.get("username"),
        "full_name": prof.get("full_name"),
        "bio": prof.get("bio"),
        "avatar_url": prof.get("avatar_url"),
        "created_at": prof.get("created_at"),
        "is_private": prof.get("is_private", False),
        "is_deactivated": prof.get("is_deactivated", False),
        "pinned_post_id": prof.get("pinned_post_id"),
    }
    if is_self:
        # Sadece kendi profilini görüntülerken kendi email/admin/gizlilik
        # tercihini görmek anlamlı — başkasına asla dönmez.
        fields["email"] = prof.get("email")
        fields["is_admin"] = bool(prof.get("is_admin"))
        fields["hide_last_seen"] = bool(prof.get("hide_last_seen"))
    return fields


@bp.route("/profile/<username>")
@api_login_required
def api_profile(username):
    """Profil görüntüleme — profile.py profile()'ın AYNI RPC (profile_page_data)
    + fallback (ThreadPoolExecutor'lı çok-sorgulu Python) deseni. Enumeration
    önleme AYNEN korunur: onlar beni engellemişse 404; deaktif profil için
    (sahibi hariç) hata değil, ayrı bir 'deactivated' JSON durumu (web'deki
    ayrı template'in JSON karşılığı, 200 ile döner).
    """
    sb = get_sb()
    prof = sb.table("profiles").select("*").ilike("username", username).execute()
    if not prof.data:
        return jsonify(error="not_found"), 404
    prof = prof.data[0]

    me = request.api_user["id"]
    is_self = me == prof["id"]

    if prof.get("is_deactivated") and not is_self:
        return jsonify(
            deactivated=True,
            profile={"username": prof["username"], "avatar_url": prof.get("avatar_url")},
            posts=[], liked_posts=[], bookmarked_posts=[], archived_posts=[],
            stats={"posts": 0, "followers": 0, "following": 0, "likes": 0},
            is_self=False, is_following=False, is_pending_request=False,
            is_private=False, is_blocked_by_me=False, is_close_friend=False,
            is_online=False, is_muted=False,
        )

    # ONLAR beni engellemişse profil hiç yokmuş gibi davran (enumeration
    # önleme). BEN onları engellemişsem profili YİNE DE gösteririm (engeli
    # kaldırabilmek için gerekli) — is_blocked_by_me alanıyla client'a bildirilir.
    if not is_self and has_blocked(sb, prof["id"], me):
        return jsonify(error="not_found"), 404

    try:
        data = sb.rpc("profile_page_data", {
            "p_viewer": me, "p_owner": prof["id"], "p_include_bookmarks": is_self,
        }).execute().data
    except Exception:
        data = None

    if data is not None:
        posts = data.get("posts", [])
        liked_posts = data.get("liked_posts", [])
        bookmarked_posts = data.get("bookmarked_posts", [])
        attach_repost_of(sb, posts)
        attach_repost_of(sb, liked_posts)
        attach_repost_of(sb, bookmarked_posts)
        followers_count = data.get("followers_count", 0)
        following_count = data.get("following_count", 0)
        is_following = False if is_self else data.get("is_following", False)
        is_pending_request = False if is_self else data.get("is_pending_request", False)
        is_private = data.get("is_private", False)
        # is_blocked_by_me: BEN onu engelledim mi? (RPC'de bu bilgi yok, tek yönlü kontrol)
        is_blocked_by_me = False
        if not is_self:
            is_blocked_by_me = has_blocked(sb, me, prof["id"])
        # archived_posts: sadece is_self; RPC'de bu yok, ayrı sorgu
        archived_posts = []
        if is_self:
            try:
                archived_posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).eq("user_id", prof["id"]).eq("is_archived", True).order("archived_at", desc=True).execute().data
                _attach_post_metrics(sb, archived_posts, me)
                attach_polls(sb, archived_posts, me)
            except Exception:
                archived_posts = []
    else:
        # Fallback: profile.py profile()'daki çok-sorgulu yolla BİREBİR aynı
        # (RPC henüz uygulanmamış/başarısız olduğunda devreye girer).
        def _fetch_visible_author_ids():
            return followed_and_self_ids(sb, me)

        def _fetch_close_friend_ids():
            return close_friend_author_ids(sb, me)

        def _fetch_blocked_ids():
            return blocked_user_ids(sb, me)

        def _fetch_liked_rows():
            try:
                return sb.table("likes").select("post_id").eq(
                    "user_id", prof["id"]
                ).order("created_at", desc=True).execute().data
            except Exception:
                return []

        def _fetch_bookmarks_raw():
            if not is_self:
                return []
            try:
                return sb.table("bookmarks").select("post_id, collection_id").eq(
                    "user_id", me
                ).order("created_at", desc=True).execute().data
            except Exception:
                return []

        def _fetch_followers_count():
            try:
                return sb.table("follows").select(
                    "follower_id", count="exact", head=True
                ).eq("following_id", prof["id"]).eq("status", "accepted").execute().count or 0
            except Exception:
                return 0

        def _fetch_following_count():
            try:
                return sb.table("follows").select(
                    "following_id", count="exact", head=True
                ).eq("follower_id", prof["id"]).eq("status", "accepted").execute().count or 0
            except Exception:
                return 0

        def _fetch_is_following():
            if is_self:
                return False
            try:
                f = sb.table("follows").select("status").eq("follower_id", me).eq(
                    "following_id", prof["id"]
                ).execute()
                return bool(f.data and f.data[0].get("status") == "accepted")
            except Exception:
                return False

        def _fetch_is_pending_request():
            if is_self:
                return False
            try:
                f = sb.table("follows").select("status").eq("follower_id", me).eq(
                    "following_id", prof["id"]
                ).execute()
                return bool(f.data and f.data[0].get("status") == "pending")
            except Exception:
                return False

        def _fetch_is_private():
            try:
                p = sb.table("profiles").select("is_private").eq("id", prof["id"]).execute()
                return bool(p.data and p.data[0].get("is_private"))
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=10) as executor:
            # Level 1: Filtreleme işleri (posts'tan bağımsız başlanabilir)
            visible_fut = executor.submit(_fetch_visible_author_ids)
            close_friend_fut = executor.submit(_fetch_close_friend_ids)
            blocked_fut = executor.submit(_fetch_blocked_ids)

            # Level 1b: posts'tan bağımsız engagement işleri
            liked_rows_fut = executor.submit(_fetch_liked_rows)
            bookmarks_raw_fut = executor.submit(_fetch_bookmarks_raw)
            followers_fut = executor.submit(_fetch_followers_count)
            following_fut = executor.submit(_fetch_following_count)
            is_following_fut = executor.submit(_fetch_is_following)
            is_pending_request_fut = executor.submit(_fetch_is_pending_request)
            is_private_fut = executor.submit(_fetch_is_private)

            visible_author_ids = visible_fut.result()
            close_friend_ids = close_friend_fut.result()
            blocked_ids = blocked_fut.result()

            # Level 2 (filtreleri bekledikten sonra): posts sorgusu
            def _fetch_posts_filtered():
                posts_data = sb.table("posts").select(
                    "*, likes(count), comments(count)"
                ).eq("user_id", prof["id"]).eq("is_archived", False).order("created_at", desc=True).execute().data
                posts_data = [p for p in posts_data if not p.get("is_draft")]
                posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
                posts_data = filter_not_blocked(posts_data, blocked_ids)
                pinned_id = prof.get("pinned_post_id")
                if pinned_id:
                    posts_data.sort(key=lambda p: 0 if p["id"] == pinned_id else 1)
                return posts_data

            posts_fut = executor.submit(_fetch_posts_filtered)

            liked_rows = liked_rows_fut.result()
            liked_ids = [l["post_id"] for l in liked_rows]
            bookmarks_raw = bookmarks_raw_fut.result()
            followers_count = followers_fut.result()
            following_count = following_fut.result()
            is_following = is_following_fut.result()
            is_pending_request = is_pending_request_fut.result()
            is_private = is_private_fut.result()

            posts = posts_fut.result()

            # is_private filtering (RPC ile aynı: private ve viewer accepted değilse posts boş)
            if is_private and not is_self and not is_following:
                posts = []

            # Level 3 (posts bitince): metrics ve polls
            metrics_fut = executor.submit(_attach_post_metrics, sb, posts, me)
            polls_fut = executor.submit(attach_polls, sb, posts, me)

            def _fetch_liked_posts():
                if not liked_ids:
                    return []
                posts_data = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", liked_ids).eq("is_archived", False).execute().data
                posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
                posts_data = filter_not_blocked(posts_data, blocked_ids)
                _attach_post_metrics(sb, posts_data, me)
                attach_polls(sb, posts_data, me)
                order = {pid: i for i, pid in enumerate(liked_ids)}
                posts_data.sort(key=lambda p: order.get(p["id"], 0))
                return posts_data

            def _fetch_bookmarked_posts():
                if not bookmarks_raw:
                    return []
                bm_ids = [b["post_id"] for b in bookmarks_raw]
                collection_by_post = {b["post_id"]: b.get("collection_id") for b in bookmarks_raw}
                if not bm_ids:
                    return []
                posts_data = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", bm_ids).eq("is_archived", False).execute().data
                posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
                posts_data = filter_not_blocked(posts_data, blocked_ids)
                _attach_post_metrics(sb, posts_data, me)
                attach_polls(sb, posts_data, me)
                bm_order = {pid: i for i, pid in enumerate(bm_ids)}
                posts_data.sort(key=lambda p: bm_order.get(p["id"], 0))
                for p in posts_data:
                    p["bookmark_collection_id"] = collection_by_post.get(p["id"])
                return posts_data

            def _fetch_archived_posts():
                if not is_self:
                    return []
                posts_data = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).eq("user_id", prof["id"]).eq("is_archived", True).order("archived_at", desc=True).execute().data
                _attach_post_metrics(sb, posts_data, me)
                attach_polls(sb, posts_data, me)
                return posts_data

            liked_posts_fut = executor.submit(_fetch_liked_posts)
            bookmarked_posts_fut = executor.submit(_fetch_bookmarked_posts)
            archived_posts_fut = executor.submit(_fetch_archived_posts)

            metrics_fut.result()
            polls_fut.result()

            liked_posts = liked_posts_fut.result()
            bookmarked_posts = bookmarked_posts_fut.result()
            archived_posts = archived_posts_fut.result()

            if is_private and not is_self and not is_following:
                liked_posts = []

        is_blocked_by_me = False
        if not is_self:
            is_blocked_by_me = prof["id"] in blocked_ids

    total_likes = sum(p.get("like_count", 0) for p in posts)

    is_close_friend = False
    if not is_self and is_following:
        try:
            # DİKKAT: tabloda 'id' kolonu YOK (PK owner_id+friend_id) — bkz.
            # profile.py'deki aynı gerekçe.
            cf = sb.table("close_friends").select("owner_id", count="exact", head=True).eq(
                "owner_id", me).eq("friend_id", prof["id"]).execute()
            is_close_friend = bool(cf.count and cf.count > 0)
        except Exception:
            pass

    is_online_status = is_online(prof["id"])
    is_muted = not is_self and prof["id"] in muted_user_ids(sb, me)

    return jsonify(
        profile=_serialize_profile_for_api(prof, is_self),
        posts=posts,
        liked_posts=liked_posts,
        bookmarked_posts=bookmarked_posts,
        archived_posts=archived_posts,
        is_self=is_self,
        is_following=is_following,
        is_pending_request=is_pending_request,
        is_private=is_private,
        is_blocked_by_me=is_blocked_by_me,
        is_close_friend=is_close_friend,
        is_online=is_online_status,
        is_muted=is_muted,
        deactivated=False,
        stats={
            "posts": len(posts),
            "followers": followers_count,
            "following": following_count,
            "likes": total_likes,
        },
    )


def _api_follow_list(username: str, kind: str):
    """Takipçi ('followers') veya takip edilen ('following') listesi —
    profile.py _follow_list()'in AYNI mantığı (dikkat: `_profile()` TAM
    eşleşme arar, `ilike` DEĞİL — api_profile() ile bilerek tutarsız, web'deki
    mevcut davranış BİREBİR korunuyor)."""
    sb = get_sb()
    prof = _profile(username=username)
    if not prof:
        return None
    me = request.api_user["id"]

    if kind == "followers":
        rows = sb.table("follows").select(
            "profiles!follows_follower_id_fkey(id, username, avatar_url, full_name)"
        ).eq("following_id", prof["id"]).eq("status", "accepted").execute().data
        title = "Takipçiler"
    else:
        rows = sb.table("follows").select(
            "profiles!follows_following_id_fkey(id, username, avatar_url, full_name)"
        ).eq("follower_id", prof["id"]).eq("status", "accepted").execute().data
        title = "Takip Edilenler"

    users = [r["profiles"] for r in rows if r.get("profiles")]

    user_ids = [u["id"] for u in users]
    following_ids = set()
    if user_ids:
        following_ids = {
            f["following_id"] for f in sb.table("follows").select("following_id")
            .eq("follower_id", me).in_("following_id", user_ids).execute().data
        }
    for u in users:
        u["is_following"] = u["id"] in following_ids
        u["is_self"] = u["id"] == me

    return {"users": users, "title": title}


@bp.route("/profile/<username>/followers")
@api_login_required
def api_profile_followers(username):
    result = _api_follow_list(username, "followers")
    if result is None:
        return jsonify(error="not_found"), 404
    return jsonify(**result)


@bp.route("/profile/<username>/following")
@api_login_required
def api_profile_following(username):
    result = _api_follow_list(username, "following")
    if result is None:
        return jsonify(error="not_found"), 404
    return jsonify(**result)


def _api_daily_counts(rows: list, days: int) -> list[dict]:
    """profile.py _daily_counts() ile BİREBİR aynı mantık (bkz. o dosyadaki docstring)."""
    counts: dict = {}
    for r in rows:
        day = r["created_at"][:10]
        counts[day] = counts.get(day, 0) + 1
    today = datetime.now(timezone.utc).date()
    return [
        {"date": (today - timedelta(days=i)).isoformat(),
         "count": counts.get((today - timedelta(days=i)).isoformat(), 0)}
        for i in range(days - 1, -1, -1)
    ]


_API_GUN_ADLARI = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _api_day_of_week_counts(rows: list) -> list[dict]:
    """profile.py _day_of_week_counts() ile BİREBİR aynı mantık (bkz. o dosyadaki docstring)."""
    counts = [0] * 7
    for r in rows:
        wd = datetime.fromisoformat(r["created_at"]).weekday()
        counts[wd] += 1
    return [{"day": _API_GUN_ADLARI[i], "count": counts[i]} for i in range(7)]


@bp.route("/profile/insights")
@api_login_required
def api_insights():
    """Kendi profil istatistikleri — profile.py insights()'ın AYNI mantığı, JSON döner.

    `days` 7/14/30 dışında bir değerse (veya eksikse) sessizce 14'e düşülür —
    bu bir istatistik uç noktası, hata döndürmeye değmez (web'deki davranışın aynısı).
    """
    sb = get_sb()
    me = request.api_user["id"]

    days = request.args.get("days", 14, type=int)
    if days not in (7, 14, 30):
        days = 14

    posts = sb.table("posts").select(
        "id, content, created_at, likes(count), comments(count)"
    ).eq("user_id", me).order("created_at", desc=True).execute().data
    for p in posts:
        p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
        p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
        p["engagement"] = p["like_count"] + p["comment_count"]

    total_posts = len(posts)
    total_likes = sum(p["like_count"] for p in posts)
    total_comments = sum(p["comment_count"] for p in posts)

    post_ids = [p["id"] for p in posts]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).isoformat()
    likes_recent, comments_recent = [], []
    if post_ids:
        likes_recent = sb.table("likes").select("created_at").in_(
            "post_id", post_ids
        ).gte("created_at", cutoff).execute().data
        comments_recent = sb.table("comments").select("created_at").in_(
            "post_id", post_ids
        ).gte("created_at", cutoff).execute().data

    likes_by_day = _api_daily_counts(likes_recent, days)
    comments_by_day = _api_daily_counts(comments_recent, days)

    follows_recent = sb.table("follows").select("created_at").eq(
        "following_id", me
    ).gte("created_at", cutoff).execute().data
    followers_by_day = _api_daily_counts(follows_recent, days)

    total_followers = sb.table("follows").select(
        "follower_id", count="exact", head=True
    ).eq("following_id", me).eq("status", "accepted").execute().count or 0
    total_following = sb.table("follows").select(
        "following_id", count="exact", head=True
    ).eq("follower_id", me).eq("status", "accepted").execute().count or 0

    avg_engagement = round((total_likes + total_comments) / total_posts, 1) if total_posts else 0

    day_of_week_stats = _api_day_of_week_counts(posts)
    most_active_day = (
        max(day_of_week_stats, key=lambda d: d["count"])["day"]
        if any(d["count"] > 0 for d in day_of_week_stats) else None
    )

    top_posts = sorted(posts, key=lambda p: p["engagement"], reverse=True)[:5]

    return jsonify(
        days=days,
        total_posts=total_posts, total_likes=total_likes, total_comments=total_comments,
        likes_by_day=likes_by_day, comments_by_day=comments_by_day, followers_by_day=followers_by_day,
        top_posts=top_posts,
        total_followers=total_followers, total_following=total_following,
        avg_engagement=avg_engagement, day_of_week_stats=day_of_week_stats,
        most_active_day=most_active_day,
    )


@bp.route("/profile/<username>/follow", methods=["POST"])
@api_login_required
def api_toggle_follow(username):
    """Takip et/bırak toggle — social.py toggle_follow()'ın fetch/JSON dalıyla
    AYNI mantık (form/redirect dalı YOK, native client zaten JSON bekliyor).
    """
    sb = get_sb()
    me = request.api_user["id"]

    target = sb.table("profiles").select("id, is_private").eq("username", username).execute()
    if not target.data:
        return jsonify(error="not_found"), 404
    target_data = target.data[0]
    target_id = target_data["id"]
    is_private = target_data.get("is_private", False)

    if target_id == me:
        return jsonify(error="cannot_follow_self"), 400

    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="blocked"), 403

    existing = sb.table("follows").select("status").eq("follower_id", me).eq(
        "following_id", target_id
    ).execute()
    if existing.data:
        # Varsa sil (pending veya accepted, hangisi olursa olsun)
        sb.table("follows").delete().eq("follower_id", me).eq(
            "following_id", target_id
        ).execute()
        following = False
        is_pending = False
    else:
        if is_private:
            sb.table("follows").insert({
                "follower_id": me, "following_id": target_id, "status": "pending"
            }).execute()
            following = False
            is_pending = True
            notify(sb, recipient_id=target_id, actor_id=me, type_="follow_request")
        else:
            sb.table("follows").insert({
                "follower_id": me, "following_id": target_id, "status": "accepted"
            }).execute()
            following = True
            is_pending = False
            notify(sb, recipient_id=target_id, actor_id=me, type_="follow")

    # sidebar_stats RPC'si follows satırlarını status'e BAKMADAN sayıyor —
    # social.py toggle_follow()'daki AYNI gerekçe.
    invalidate(f"sidebar:{me}")
    invalidate(f"sidebar:{target_id}")

    followers_count = len(sb.table("follows").select("follower_id").eq(
        "following_id", target_id
    ).eq("status", "accepted").execute().data)
    return jsonify(following=following, followers_count=followers_count, is_pending=is_pending)


@bp.route("/follow-requests")
@api_login_required
def api_list_follow_requests():
    """Bana gelen bekleyen (pending) takip istekleri — social.py
    list_follow_requests()'in AYNI mantığı."""
    sb = get_sb()
    me = request.api_user["id"]

    requests_data = sb.table("follows").select(
        "follower_id, created_at, profiles!follows_follower_id_fkey(id, username, avatar_url, full_name)"
    ).eq("following_id", me).eq("status", "pending").order("created_at", desc=True).execute().data

    users = [r["profiles"] for r in requests_data if r.get("profiles")]
    for u in users:
        u["is_self"] = u["id"] == me

    return jsonify(users=users)


@bp.route("/follow-requests/<follower_id>/accept", methods=["POST"])
@api_login_required
def api_accept_follow_request(follower_id):
    """Pending takip isteğini kabul et — social.py accept_follow_request()'in
    AYNI mantığı (sadece ALICI kabul edebilir, aksi halde 404 — enumeration önleme)."""
    sb = get_sb()
    me = request.api_user["id"]

    follow_req = sb.table("follows").select("status").eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()
    if not follow_req.data or follow_req.data[0].get("status") != "pending":
        return jsonify(error="not_found"), 404

    sb.table("follows").update({"status": "accepted"}).eq(
        "follower_id", follower_id
    ).eq("following_id", me).execute()

    notify(sb, recipient_id=follower_id, actor_id=me, type_="follow_accept")

    return jsonify(ok=True)


@bp.route("/follow-requests/<follower_id>/reject", methods=["POST"])
@api_login_required
def api_reject_follow_request(follower_id):
    """Pending takip isteğini reddet — social.py reject_follow_request()'in
    AYNI mantığı (sadece ALICI reddedebilir, aksi halde 404 — enumeration önleme)."""
    sb = get_sb()
    me = request.api_user["id"]

    follow_req = sb.table("follows").select("status").eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()
    if not follow_req.data or follow_req.data[0].get("status") != "pending":
        return jsonify(error="not_found"), 404

    sb.table("follows").delete().eq("follower_id", follower_id).eq(
        "following_id", me
    ).execute()

    # toggle_follow'daki gerekçenin aynısı: satır silindi, sayaçlar değişti.
    invalidate(f"sidebar:{me}")
    invalidate(f"sidebar:{follower_id}")

    return jsonify(ok=True)


# ----------------------- REELS (Faz 3, native Android reels ekranı) -----------------------
# app/routes/reels.py reels()'in AYNI mantığı — RPC yok (basit sorgu), anket yok.

@bp.route("/reels")
@api_login_required
def api_reels():
    """Dikey video akışı — reels.py reels()'in AYNI filtre/sıralama mantığı,
    JSON döner. is_reel migration'ı henüz uygulanmamışsa boş liste döner
    (hata değil, web'deki AYNI graceful degradation)."""
    sb = get_sb()
    me = request.api_user["id"]
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url, is_private, is_deactivated), "
                   "likes(count), comments(count)")

    try:
        posts = sb.table("posts").select(select_cols).eq(
            "visibility", "public"
        ).eq("is_reel", True).not_.is_(
            "video_url", "null"
        ).eq("is_draft", False).eq("is_archived", False).order(
            "created_at", desc=True
        ).range(offset, offset + PAGE_SIZE).execute().data
    except Exception:
        posts = []

    visible_author_ids = followed_and_self_ids(sb, me)
    if posts:
        author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
        is_private_map = {}
        is_deactivated_map = {}
        if author_ids:
            try:
                profiles = sb.table("profiles").select("id, is_private, is_deactivated").in_(
                    "id", list(author_ids)
                ).execute().data
                is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
                is_deactivated_map = {p["id"]: p.get("is_deactivated", False) for p in profiles}
            except Exception:
                pass
        posts = [p for p in posts if not (
            is_deactivated_map.get(p.get("user_id"), False) or
            (is_private_map.get(p.get("user_id"), False) and
             p.get("user_id") != me and
             p.get("user_id") not in visible_author_ids)
        )]

    blocked_ids = blocked_user_ids(sb, me)
    posts = [p for p in posts if p.get("user_id") not in blocked_ids]

    has_more = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]

    _attach_post_metrics(sb, posts, me)

    return jsonify(posts=posts, has_more=has_more, page=page)


# ----------------------- MESAJLAŞMA (Faz 3, native Android mesajlaşma ekranı) -----------------------
# app/messaging/*.py'nin BİLİNÇLİ bir alt kümesi JSON'a taşınır: inbox +
# konuşma geçmişi (sayfalı) + metin/görsel mesajı gönderme (+reply_to) +
# get-or-create 1:1 konuşma başlatma + okundu işaretleme + grup oluşturma/
# yönetimi (Faz 4). KAPSAM DIŞI (hâlâ hiç dokunulmadı): mesaj düzenleme/
# silme/sabitleme/iletme/tepki, ses/sticker/GIF mesaj, WebRTC/LiveKit arama,
# Supabase Realtime (native taraf basit polling yapacak).

def _serialize_conversation_summary(c: dict) -> dict:
    """_common.py _build_convos() satırını JSON sözleşmesine çevirir.

    'unread' zaten _build_convos()'ta BOOL (mesaj değil SOHBET bazlı,
    gruplarda hep False — bkz. o dosyadaki unread_message_count docstring'i);
    burada ayrı bir sayaç İCAT edilmez, aynı anlam `has_unread` adıyla taşınır.
    """
    is_group = c["is_group"]
    other = c.get("other_user")
    last = c.get("last_message")
    return {
        "id": c["id"],
        "is_group": is_group,
        "name": c["name"] if is_group else (other.get("username") if other else None),
        "avatar_url": None if is_group else (other.get("avatar_url") if other else None),
        "last_message_preview": (last.get("content") or "")[:40] if last else None,
        "last_message_at": last.get("created_at") if last else None,
        "has_unread": c["unread"],
        # _build_convos() zaten hesaplıyor (grupta değilse None) — Faz 4 grup
        # yönetimi eklenirken inbox satırında "3 üye" gibi bir özet göstermek
        # için serileştirmeye eklendi, native eski sürümler bilinmeyen alanı
        # yok sayar (additive/geriye uyumlu).
        "member_count": c.get("member_count") if is_group else None,
    }


@bp.route("/messages/conversations")
@api_login_required
def api_message_conversations():
    """Gelen kutusu (inbox) — messaging/views.py inbox()'ın AYNI mantığı
    (paylaşılan _common.py _build_convos() üzerinden, ayrı bir sorgu/kural
    İCAT edilmedi), JSON döner. Sıralama zaten _build_convos() içinde son
    mesaj zamanına göre azalan yapılıyor.
    """
    sb = get_sb()
    me = request.api_user["id"]
    convos = _build_convos(sb, me)
    return jsonify(conversations=[_serialize_conversation_summary(c) for c in convos])


@bp.route("/messages/conversations/<conversation_id>")
@api_login_required
def api_message_conversation_detail(conversation_id):
    """Bir konuşmanın mesaj geçmişi — messaging/views.py conversation()'ın
    çekirdek mantığı (katılımcı doğrulama → 403; tepki/sticker embed +
    fallback; reply_to için self-join yerine TEK toplu IN sorgusu — hepsi
    o dosyadaki AYNI gerekçelerle).

    BİLİNÇLİ SAPMA: web'de numaralı sayfalama yok, tek bir "?all=1" bayrağı
    var (son MESSAGE_PAGE mesaj ya da TÜM geçmiş — DOM'da sonsuz kaydırma
    JS tarafında tutuluyor). Native tarafın sunucu-taraflı sayfalı sonsuz
    kaydırma yapması gerektiği için burada ?page= (1-tabanlı, en yeniden en
    eskiye offset) eklendi: page=1 = web'in varsayılan görünümüyle AYNI son
    MESSAGE_PAGE mesaj, page=2 bir önceki MESSAGE_PAGE, vb. has_more=True
    ise bir sonraki (daha eski) sayfa mevcuttur.

    Okundu işaretleme SADECE page=1 (en güncel sayfa) çekilince tetiklenir —
    "sohbeti açmak = okundu işaretlemek" web davranışıyla tutarlı; eski
    sayfaları (page>1) çekmek okundu durumunu DEĞİŞTİRMEZ.
    """
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        # conversation()'daki AYNI durum kodu (403) — abort() yerine jsonify
        # (JSON API'de HTML hata sayfası dönmesin diye)
        return jsonify(error="forbidden"), 403

    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * MESSAGE_PAGE

    try:
        conv_rows = sb.table("conversations").select("is_group, name").eq("id", conversation_id).execute().data
        is_group = bool(conv_rows[0].get("is_group")) if conv_rows else False
        group_name = conv_rows[0].get("name") if conv_rows else None
    except Exception:
        is_group, group_name = False, None

    other_user = None
    if not is_group:
        try:
            others_raw = sb.table("conversation_participants").select(
                "profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
            ).eq("conversation_id", conversation_id).neq("user_id", me).execute().data
            other_profiles = [o["profiles"] for o in others_raw if o.get("profiles")]
            other_user = other_profiles[0] if other_profiles else None
        except Exception:
            other_user = None

    # Mesajlar — views.py conversation()'daki _fetch_messages ile AYNI embed +
    # fallback deseni: embed'li select patlarsa sade select'e düşülür, sohbet
    # render'ı ASLA embed yüzünden kırılmaz.
    base = "*, profiles!messages_sender_id_fkey(username, avatar_url)"
    selects = (base + ", sticker:stickers(id, image_url), message_reactions(reaction, user_id)", base)
    rows = []
    for sel in selects:
        try:
            rows = sb.table("messages").select(sel).eq(
                "conversation_id", conversation_id
            ).order("created_at", desc=True).limit(MESSAGE_PAGE + 1).offset(offset).execute().data
            break
        except Exception:
            continue

    has_more = len(rows) > MESSAGE_PAGE
    rows = rows[:MESSAGE_PAGE]
    rows.reverse()  # istemci artan (eskiden yeniye) sırayla render etsin

    # Alıntı (reply_to) — TEK toplu IN sorgusu (self-join embed yön belirsizliği
    # yaratıyor, bkz. views.py conversation() yorumu — burada AYNI çözüm).
    reply_lookup = {}
    try:
        reply_ids = list({m["reply_to_id"] for m in rows if m.get("reply_to_id")})
        if reply_ids:
            quoted = sb.table("messages").select(
                "id, content, image_url, sender_id, profiles!messages_sender_id_fkey(username)"
            ).in_("id", reply_ids).execute().data
            reply_lookup = {q["id"]: q for q in quoted}
    except Exception:
        reply_lookup = {}

    for m in rows:
        m["reply_to"] = reply_lookup.get(m.get("reply_to_id"))
        raw_reactions = m.pop("message_reactions", None) or []
        by_react = {}
        for r in raw_reactions:
            by_react.setdefault(r["reaction"], []).append(r["user_id"])
        m["reactions"] = [
            {"reaction": react, "count": len(uids), "mine": me in uids}
            for react, uids in by_react.items()
        ]
        if "sticker" not in m:
            m["sticker"] = None

    if page == 1:
        # views.py conversation()'daki _write_reads() ile AYNI arka plan
        # deseni — yanıt bu yazmaları BEKLEMEZ (first_unread hesaplama gibi
        # bir ekran kaygısı JSON API'de yok, doğrudan arka plana atılır).
        if not is_group:
            _write_pool.submit(lambda: _mark_read(sb, conversation_id, me, rows))
        _write_pool.submit(lambda: _mark_message_notifications_read(sb, me, conversation_id))

    return jsonify(
        messages=rows,
        has_more=has_more,
        conversation={
            "id": conversation_id,
            "is_group": is_group,
            "name": group_name if is_group else (other_user.get("username") if other_user else None),
            "avatar_url": None if is_group else (other_user.get("avatar_url") if other_user else None),
        },
    )


@bp.route("/messages/conversations/<conversation_id>/send", methods=["POST"])
@api_login_required
def api_send_message(conversation_id):
    """Mesaj gönder — messaging/sending.py send_message()'ın metin+reply_to_id+
    GÖRSEL dalları port edildi (ses/sticker/GIF dalları BİLİNÇLİ olarak
    atlandı — ses RECORD_AUDIO izni+MediaRecorder gerektiriyor, sticker yeni
    bir katalog endpoint'i, GIF üçüncü-parti Klipy entegrasyonu istiyor, hepsi
    ayrı birer iterasyon).

    BİLİNÇLİ SAPMA: JSON'dan multipart/form-data'ya geçildi (api_create_post()
    ile AYNI kodlama deseni) — native henüz gerçek kullanıcıya çıkmadığı için
    (bkz. proje yol haritası) bu, geriye dönük uyumluluk kaygısı olmadan
    yapılabilecek bir sözleşme değişikliği.
    """
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    # send_message()'daki AYNI limit: kullanıcı bazlı (IP değil — aynı ev/ofisteki
    # birden fazla kişi birbirini kilitlemesin), dakikada 30 mesaj.
    if is_rate_limited(f"send_message:{me}", 30, 60):
        return jsonify(error="rate_limited"), 429

    # send_message()'daki AYNI engelleme kontrolü: konuşma bir engellemeden
    # ÖNCE başlamış olabilir, her gönderimde tekrar kontrol edilir.
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        return jsonify(error="blocked"), 403

    content = (request.form.get("content") or "").strip()
    reply_to_id = (request.form.get("reply_to_id") or "").strip() or None
    image_file = request.files.get("image")
    has_image = bool(image_file and image_file.filename)

    if not content and not has_image:
        return jsonify(error="empty"), 400

    image_url = None
    if has_image:
        image_url = upload_image(image_file, folder="messages")
        if not image_url:
            return jsonify(error="upload_failed"), 400

    if reply_to_id:
        # send_message()'daki AYNI doğrulama: farklı konuşmadansa/mevcut
        # değilse reply'siz gönder (sessizce, hata değil).
        reply_msg = sb.table("messages").select("id").eq("id", reply_to_id).eq(
            "conversation_id", conversation_id
        ).execute().data
        if not reply_msg:
            reply_to_id = None

    insert_data = {"conversation_id": conversation_id, "sender_id": me, "content": content, "image_url": image_url}
    try:
        # reply_to_id opsiyonel kolon — migration_message_reply.sql henüz
        # uygulanmamışsa kolonsuz insert'e düş (send_message()'daki AYNI desen).
        data_full = dict(insert_data)
        if reply_to_id:
            data_full["reply_to_id"] = reply_to_id
        inserted = sb.table("messages").insert(data_full).execute()
    except Exception:
        inserted = sb.table("messages").insert(insert_data).execute()

    # Bildirim + @etiketleme yanıtı BEKLETMESİN — send_message()'daki AYNI
    # arka plan deseni (test_request_context: url_for kullanan yardımcılar
    # bağlamsız thread'de RuntimeError fırlatmasın diye).
    app = current_app._get_current_object()
    allowed_ids = {o["user_id"] for o in others}

    def _bg_notify():
        try:
            with app.test_request_context():
                _notify_conversation(sb, conversation_id, me)
                notify_mentions(sb, actor_id=me, content=content,
                                 conversation_id=conversation_id, allowed_ids=allowed_ids)
        except Exception:
            pass

    _write_pool.submit(_bg_notify)

    return jsonify(message=inserted.data[0])


@bp.route("/messages/start/<username>", methods=["POST"])
@api_login_required
def api_start_conversation(username):
    """Yeni 1:1 konuşma başlat (get-or-create) — messaging/creation.py
    start_conversation()'ın AYNI mantığı (_get_or_create_conversation() zaten
    grup konuşmalarını kesişimden eleyip SADECE 1:1 döndürüyor)."""
    sb = get_sb()
    me = request.api_user["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute()
    if not target.data:
        return jsonify(error="not_found"), 404
    target_id = target.data[0]["id"]

    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="blocked"), 403

    cid = _get_or_create_conversation(me, target_id)
    return jsonify(conversation_id=cid)


@bp.route("/messages/conversations/<conversation_id>/mark-read", methods=["POST"])
@api_login_required
def api_mark_conversation_read(conversation_id):
    """Konuşmayı okundu işaretle — messaging/views.py
    mark_conversation_read()'in AYNI mantığı (katılımcı değilse 404 —
    kaynak fonksiyonla BİREBİR aynı durum kodu; conversation() endpoint'inin
    403'ünden BİLEREK farklı, ikisi de kendi kaynağıyla tutarlı)."""
    sb = get_sb()
    me = request.api_user["id"]
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="not_found"), 404

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        sb.table("messages").update({"read_at": now_iso}).eq(
            "conversation_id", conversation_id
        ).neq("sender_id", me).is_("read_at", "null").execute()
        invalidate(f"unread_msgs:{me}")
    except Exception:
        pass  # read_at migration'ı yoksa sessizce atla (mark_conversation_read ile aynı tavır)

    _mark_message_notifications_read(sb, me, conversation_id)
    return jsonify(ok=True)


# --- Grup sohbeti yönetimi (Faz 4, native Android) ---
# app/messaging/creation.py create_group() + app/messaging/group_admin.py'nin
# TAMAMI (rename/üye ekle-çıkar/admin toggle/ayrılma) BİREBİR aynı iş mantığıyla
# JSON'a taşınır — yeni bir kural İCAT edilmez, sadece jsonify/durum kodu
# sözleşmesi native'e uygun hale getirilir.

def _api_is_group_admin(sb, conversation_id: str, user_id: str) -> bool:
    """group_admin.py _is_admin()'in kopyası (import değil — o dosya bir web
    Blueprint'i, route-seviyesi yardımcılar burada mevcut desene uyup
    kopyalanır, sadece _common.py'deki paylaşılan yardımcılar import edilir).

    Service-role RLS'i bypass ettiğinden bu kontrol DB'den her admin-gated
    route'ta TAZE yapılır — token'ın kime ait olduğu bilgisine güvenilmez.
    """
    row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    return bool(row) and bool(row[0].get("is_admin"))


@bp.route("/messages/group/new", methods=["POST"])
@api_login_required
def api_create_group():
    """Yeni grup sohbeti — creation.py create_group()'ın AYNI mantığı
    (isim + kendisi hariç en az 2 üye, engellenen biri varsa 403,
    migration uygulanmamışsa insert patlar -> 503)."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    name = _str_field(data, "name")
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))

    if not name:
        return jsonify(error="missing_name"), 400
    if len(user_ids) < 2:
        return jsonify(error="too_few_members"), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="blocked"), 403

    try:
        conv = sb.table("conversations").insert({
            "is_group": True, "name": name, "created_by": me,
        }).execute()
    except Exception:
        return jsonify(error="groups_not_available"), 503
    cid = conv.data[0]["id"]

    # Grubu oluşturan otomatik yönetici olur, davet edilenler değil
    # (create_group()'daki AYNI kural, created_by ile tutarlı).
    sb.table("conversation_participants").insert(
        [{"conversation_id": cid, "user_id": me, "is_admin": True}]
        + [{"conversation_id": cid, "user_id": uid, "is_admin": False} for uid in user_ids]
    ).execute()

    for uid in user_ids:
        notify(sb, recipient_id=uid, actor_id=me, type_="message", conversation_id=cid)

    return jsonify(conversation_id=cid)


@bp.route("/messages/group/<conversation_id>/rename", methods=["POST"])
@api_login_required
def api_rename_group(conversation_id):
    """Grup adını değiştirir — group_admin.py rename_group() ile AYNI mantık.

    Çağıran gruba hiç katılımcı değilse de _api_is_group_admin() False döner
    (satır yok) — outsider ve "admin olmayan üye" AYNI 403 not_admin'i alır,
    detail/members/send'deki forbidden davranışıyla tutarlı.
    """
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    name = _str_field(data, "name")
    if not name:
        return jsonify(error="missing_name"), 400

    sb.table("conversations").update({"name": name}).eq("id", conversation_id).execute()
    return jsonify(ok=True, name=name)


def _serialize_group_member(row: dict) -> dict:
    """conversation_participants + profiles join satırını JSON üye sözleşmesine çevirir."""
    p = row.get("profiles") or {}
    return {
        "id": p.get("id"),
        "username": p.get("username"),
        "avatar_url": p.get("avatar_url"),
        "is_admin": bool(row.get("is_admin")),
    }


@bp.route("/messages/group/<conversation_id>/members")
@api_login_required
def api_group_members(conversation_id):
    """Grup üye listesi + is_admin bayrağı — web'de doğrudan karşılığı yok,
    native'in "Grubu Yönet" ekranı için EKLENDİ (bkz. görev tarifi). Çağıran
    katılımcı değilse diğer endpoint'lerle (detail/send) AYNI 403 forbidden."""
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    rows = sb.table("conversation_participants").select(
        "is_admin, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).eq("conversation_id", conversation_id).execute().data
    members = [_serialize_group_member(r) for r in rows]
    members.sort(key=lambda m: (not m["is_admin"], (m["username"] or "").lower()))
    return jsonify(members=members)


@bp.route("/messages/group/<conversation_id>/members/add", methods=["POST"])
@api_login_required
def api_add_group_members(conversation_id):
    """Gruba üye ekler — group_admin.py add_group_members() ile AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))
    if not user_ids:
        return jsonify(error="missing_user_ids"), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="blocked"), 403

    # Zaten üye olanları tekrar eklemek primary key ihlaline yol açar
    # (add_group_members()'daki AYNI ön-filtre, grup üye sayısı küçük).
    existing = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).execute().data
    existing_ids = {r["user_id"] for r in existing}
    new_ids = [uid for uid in user_ids if uid not in existing_ids]
    if not new_ids:
        return jsonify(ok=True, added=[])

    sb.table("conversation_participants").insert([
        {"conversation_id": conversation_id, "user_id": uid, "is_admin": False} for uid in new_ids
    ]).execute()

    for uid in new_ids:
        notify(sb, recipient_id=uid, actor_id=me, type_="message", conversation_id=conversation_id)

    profiles = sb.table("profiles").select("id, username, avatar_url").in_("id", new_ids).execute().data
    added = [{**p, "is_admin": False} for p in profiles]
    return jsonify(ok=True, added=added)


@bp.route("/messages/group/<conversation_id>/members/<user_id>/remove", methods=["POST"])
@api_login_required
def api_remove_group_member(conversation_id, user_id):
    """Üyeyi gruptan çıkarır — group_admin.py remove_group_member() ile AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403
    if user_id == me:
        return jsonify(error="cannot_remove_self"), 400

    target = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="not_a_member"), 404

    sb.table("conversation_participants").delete().eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True)


@bp.route("/messages/group/<conversation_id>/members/<user_id>/toggle-admin", methods=["POST"])
@api_login_required
def api_toggle_group_admin(conversation_id, user_id):
    """Üyenin yöneticilik durumunu tersine çevirir — group_admin.py
    toggle_group_admin() ile AYNI mantık (tek admin kendini düşüremez)."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    target = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="not_a_member"), 404

    new_value = not bool(target[0].get("is_admin"))

    if user_id == me and not new_value:
        # Grup admin'siz kalmasın diye kendini düşürmeden önce başka bir
        # admin olduğundan emin olunur (toggle_group_admin()'daki AYNI kontrol).
        other_admins = sb.table("conversation_participants").select("user_id").eq(
            "conversation_id", conversation_id
        ).eq("is_admin", True).neq("user_id", me).execute().data
        if not other_admins:
            return jsonify(error="last_admin"), 400

    sb.table("conversation_participants").update({"is_admin": new_value}).eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True, is_admin=new_value)


@bp.route("/messages/group/<conversation_id>/leave", methods=["POST"])
@api_login_required
def api_leave_group(conversation_id):
    """Gruptan ayrılır — group_admin.py leave_group() ile AYNI mantık
    (atomik RPC: ayrılma + boş grup temizliği + tek adminse admin devri,
    hepsi tek transaction'da — bkz. sql/migration_leave_group_atomic_rpc.sql)."""
    sb = get_sb()
    me = request.api_user["id"]

    me_row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not me_row:
        return jsonify(error="not_a_member"), 404

    sb.rpc("leave_group_and_reassign_admin", {
        "p_conversation_id": conversation_id, "p_user_id": me
    }).execute()

    return jsonify(ok=True)


# ----------------------- ETKİLEŞİMLER: BEĞENİ + YORUM (Faz 4, native Android) -----------------------
# app/social.py toggle_like()/add_comment()/reply_comment() ve app/routes/posts.py
# post_detail()'in BİLİNÇLİ bir alt kümesi JSON'a taşınır. KAPSAM DIŞI (bu turda
# hiç dokunulmadı): sticker/gif yorum, yorum düzenleme/silme/tepki (comment_reactions),
# yorum beğenme (comment_likes) — SADECE post beğenme + metin yorumu (+yanıt) var.

@bp.route("/posts/<post_id>/like", methods=["POST"])
@api_login_required
def api_toggle_like(post_id):
    """Post beğen/beğenmekten vazgeç — social.py toggle_like()'ın AYNI mantığı
    (reaksiyon değiştirme dahil), sadece JSON döner (redirect dalı yok)."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        return jsonify(error="not_found"), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    reaction = _str_field(data, "reaction") or "like"
    if reaction not in REACTIONS:
        reaction = "like"

    existing = sb.table("likes").select("reaction_type").eq("post_id", post_id).eq("user_id", me).execute()
    if existing.data:
        current = existing.data[0].get("reaction_type") or "like"
        if current == reaction:
            sb.table("likes").delete().eq("post_id", post_id).eq("user_id", me).execute()
            liked = False
            reaction = None
        else:
            sb.table("likes").update({"reaction_type": reaction}).eq(
                "post_id", post_id
            ).eq("user_id", me).execute()
            liked = True
    else:
        sb.table("likes").insert({
            "post_id": post_id, "user_id": me, "reaction_type": reaction
        }).execute()
        liked = True
        notify(sb, recipient_id=post[0]["user_id"], actor_id=me, type_="like", post_id=post_id)

    count = len(sb.table("likes").select("post_id").eq("post_id", post_id).execute().data)
    return jsonify(liked=liked, reaction=reaction, count=count)


def _serialize_comment(c: dict) -> dict:
    """post_detail()'daki yorum enrichment'ının (like_count/liked_by_me/
    sticker/reactions/replies) AYNI şeklini JSON'a taşır — burada ayrı bir
    serialization İCAT edilmedi, sadece Python dict'i olduğu gibi jsonify'a girer."""
    return c


@bp.route("/posts/<post_id>")
@api_login_required
def api_post_detail(post_id):
    """Tek bir post + hiyerarşik yorum listesi — posts.py post_detail()'in
    AYNI okuma mantığı (yazma/form işleme YOK, o ayrı endpoint'lerde).
    Sticker/gif YORUM oluşturma bu iterasyonda kapsam dışı ama VAR OLAN
    sticker/gif'li yorumlar görüntüleme için JSON'da GÖSTERİLİR (veri
    kaybı olmasın, native şimdilik render etmeyecek olsa da)."""
    sb = get_sb()
    me = request.api_user["id"]

    res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).eq("id", post_id).execute()
    if not res.data:
        return jsonify(error="not_found"), 404
    post = res.data[0]

    if post.get("is_draft") and post["user_id"] != me:
        return jsonify(error="not_found"), 404
    if not _can_view_post(sb, post, me):
        return jsonify(error="not_found"), 404

    record_view(sb, post_id, me, post["user_id"])

    _attach_post_metrics(sb, [post], me)
    attach_polls(sb, [post], me)

    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url), comment_likes(count)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

    comment_ids = [c["id"] for c in comments]
    my_comment_likes = set()
    if comment_ids:
        my_comment_likes = {
            l["comment_id"] for l in sb.table("comment_likes").select("comment_id")
            .eq("user_id", me).in_("comment_id", comment_ids).execute().data
        }
    for c in comments:
        c["like_count"] = c["comment_likes"][0]["count"] if c.get("comment_likes") else 0
        c["liked_by_me"] = c["id"] in my_comment_likes

    try:
        sticker_ids = [c["sticker_id"] for c in comments if c.get("sticker_id")]
        stickers_by_id = {}
        if sticker_ids:
            for s in sb.table("stickers").select("id, image_url").in_("id", sticker_ids).execute().data:
                stickers_by_id[s["id"]] = {"id": s["id"], "image_url": s["image_url"]}
        for c in comments:
            c["sticker"] = stickers_by_id.get(c.get("sticker_id"))
    except Exception:
        for c in comments:
            c["sticker"] = None

    try:
        reactions_by_comment = {}
        if comment_ids:
            rows = sb.table("comment_reactions").select("comment_id, user_id, reaction") \
                .in_("comment_id", comment_ids).execute().data
            for r in rows:
                bucket = reactions_by_comment.setdefault(r["comment_id"], {})
                bucket.setdefault(r["reaction"], []).append(r["user_id"])
        for c in comments:
            grouped = reactions_by_comment.get(c["id"], {})
            c["reactions"] = [
                {"reaction": emoji, "count": len(users), "mine": me in users}
                for emoji, users in grouped.items()
            ]
    except Exception:
        for c in comments:
            c["reactions"] = []

    top_comments = [c for c in comments if not c.get("parent_comment_id")]
    for tc in top_comments:
        tc["replies"] = [_serialize_comment(c) for c in comments if c.get("parent_comment_id") == tc["id"]]

    return jsonify(post=post, comments=[_serialize_comment(c) for c in top_comments])


@bp.route("/posts/<post_id>/comments", methods=["POST"])
@api_login_required
def api_add_comment(post_id):
    """Yorum ekle (+ isteğe bağlı yanıt) — social.py add_comment()/
    reply_comment()'in SADECE metin dalı (sticker/gif İCAT edilmedi, bu
    iterasyonun kapsamı dışı). body'de `parent_comment_id` varsa yanıt,
    yoksa ana yorum — hangi bildirim türünün ('comment' vs 'reply')
    gönderileceği buna göre seçilir, kaynak fonksiyonlarla AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or not _can_view_post(sb, post[0], me):
        return jsonify(error="not_found"), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    content = _str_field(data, "content")
    parent_comment_id = _str_field(data, "parent_comment_id") or None

    if not content:
        return jsonify(error="empty"), 400

    if parent_comment_id:
        # reply_comment()'daki AYNI doğrulama: farklı posttansa/mevcut değilse
        # yanıtsız (ana yorum gibi) gönder, hata değil.
        parent = sb.table("comments").select("id").eq("id", parent_comment_id).eq(
            "post_id", post_id
        ).execute().data
        if not parent:
            parent_comment_id = None

    dup = _find_recent_duplicate_comment(sb, post_id, me, content, None, None, parent_id=parent_comment_id)
    if dup:
        comment_row = dup
    else:
        insert_data = {"post_id": post_id, "user_id": me, "content": content}
        if parent_comment_id:
            insert_data["parent_comment_id"] = parent_comment_id
        try:
            res = sb.table("comments").insert(insert_data).execute()
        except _CONNECTION_ERRORS:
            raise  # dıştaki katman yeniden dener, dup kontrolü idempotent devam ettirir
        except Exception:
            # parent_comment_id kolonu henüz yoksa (migration öncesi) onsuz dene
            res = sb.table("comments").insert({
                "post_id": post_id, "user_id": me, "content": content
            }).execute()
        comment_row = res.data[0] if res.data else {}

    comment_id = comment_row.get("id")

    def _bg_notify():
        try:
            if parent_comment_id:
                parent_row = sb.table("comments").select("user_id").eq("id", parent_comment_id).execute().data
                if parent_row:
                    notify(sb, recipient_id=parent_row[0]["user_id"], actor_id=me,
                           type_="reply", post_id=post_id, comment_id=comment_id)
            else:
                notify(sb, recipient_id=post[0]["user_id"], actor_id=me,
                       type_="comment", post_id=post_id, comment_id=comment_id)
            notify_mentions(sb, actor_id=me, content=content, post_id=post_id, comment_id=comment_id)
        except Exception:
            pass

    _notify_pool.submit(_bg_notify)

    prof = sb.table("profiles").select("username, avatar_url").eq("id", me).execute()
    prof_data = prof.data[0] if prof.data else {}

    return jsonify(comment={
        "id": comment_id,
        "content": content,
        "parent_comment_id": parent_comment_id,
        "user_id": me,
        "created_at": comment_row.get("created_at"),
        "profiles": {"username": prof_data.get("username"), "avatar_url": prof_data.get("avatar_url")},
        "like_count": 0,
        "liked_by_me": False,
        "reactions": [],
        "sticker": None,
    })


# ----------------------- POST OLUŞTURMA (Faz 4, native Android) -----------------------
# app/routes/posts.py create_post()'un BİLİNÇLİ bir alt kümesi: metin + TEK
# görsel + görünürlük. KAPSAM DIŞI (bu turda hiç dokunulmadı): çoklu görsel
# (max 4), video/reel oluşturma, GIF, anket, taslak/planlama, konum.

@bp.route("/posts", methods=["POST"])
@api_login_required
def api_create_post():
    """Yeni post oluştur — multipart/form-data (web formuyla AYNI kodlama,
    JSON DEĞİL, çünkü görsel dosyası içeriyor). Alanlar: `content` (metin),
    `visibility` (public/followers/close_friends, varsayılan public),
    `image` (opsiyonel, TEK dosya — create_post()'daki `images` çoklu
    alanının BİLİNÇLİ olarak tekil hali)."""
    sb = get_sb()
    me = request.api_user["id"]

    content = (request.form.get("content") or "").strip()
    image_file = request.files.get("image")
    has_image = bool(image_file and image_file.filename)

    if not content and not has_image:
        return jsonify(error="empty"), 400

    visibility = request.form.get("visibility", "public")
    if visibility not in ("public", "followers", "close_friends"):
        visibility = "public"

    image_urls = []
    if has_image:
        image_url = upload_image(image_file, folder="posts")
        if not image_url:
            return jsonify(error="upload_failed"), 400
        image_urls = [image_url]

    insert_data = {"user_id": me, "content": content, "image_urls": image_urls}
    if image_urls:
        insert_data["image_url"] = image_urls[0]

    try:
        full_data = {**insert_data, "visibility": visibility, "is_draft": False}
        inserted = sb.table("posts").insert(full_data).execute()
    except Exception:
        inserted = sb.table("posts").insert(insert_data).execute()

    post_id = inserted.data[0]["id"] if inserted.data else None
    if not post_id:
        return jsonify(error="create_failed"), 500

    # create_post()'daki AYNI yayın-sonrası işler — sadece BAŞARIYLA
    # yayınlanan (taslak olmayan) bir post için (bu endpoint hiç taslak
    # üretmiyor, HER ZAMAN yayınlanır).
    invalidate(f"sidebar:{me}")
    if content:
        sync_post_hashtags(sb, post_id, content)
        invalidate("trending:")
        notify_mentions(sb, actor_id=me, content=content, post_id=post_id)
        notify_hashtag_followers(sb, actor_id=me, post_id=post_id, tags=extract_hashtags(content))

    post_res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).eq("id", post_id).execute()
    post = post_res.data[0] if post_res.data else {"id": post_id, **insert_data}
    _attach_post_metrics(sb, [post], me)

    return jsonify(post=post)


# ----------------------- PROFİL AYARLARI (Faz 4, native Android) -----------------------
# app/routes/profile.py profile_edit()/deactivate_account(), app/notifications.py
# preferences() ve app/close_friends.py'nin BİLİNÇLİ bir alt kümesi JSON'a
# taşınır: temel profil düzenleme (bio/tam ad/kullanıcı adı/avatar/gizlilik-
# son-görülme), bildirim tercihleri, yakın arkadaşlar, hesap deaktivasyonu.
# KAPSAM DIŞI (ayrı, gelecek bir iterasyon): 2FA enroll/verify/disable (QR kod
# akışı ayrı ve güvenlik açısından daha dikkatli ele alınacak), aktif oturum
# listesi/uzaktan-çıkış yönetimi, şifre değiştirme/sıfırlama.

def _bool_form_field(value: str | None) -> bool:
    """Native toggle'lardan gelen "true"/"false" string'ini bool'a çevirir.

    Web'deki HTML checkbox davranışından (yokluk == False, varlık == "on")
    farklı olarak native her zaman açık bir "true"/"false" gönderiyor — ama
    eksik/beklenmedik bir değer yine de False sayılır (fail-closed, web'in
    "yoksa kapalı" sonucuyla tutarlı).
    """
    return value == "true"


@bp.route("/profile/edit", methods=["POST"])
@api_login_required
def api_profile_edit():
    """Profil düzenle — profile.py profile_edit()'in POST dalının AYNI mantığı
    (migration-yoksa-fallback dahil). multipart/form-data (JSON DEĞİL) —
    avatar dosyası içerebiliyor, api_create_post()'daki AYNI kodlama deseni.
    Session senkronu YOK: native token-based, Flask session'a hiç dokunmuyor
    (web'deki navbar/session güncellemesi burada anlamsız).
    """
    sb = get_sb()
    me = request.api_user["id"]

    full_name = (request.form.get("full_name") or "").strip()
    bio = (request.form.get("bio") or "").strip()
    username = (request.form.get("username") or "").strip()
    avatar_file = request.files.get("avatar")

    if not username or len(username) < 3:
        return jsonify(error="short_username"), 400

    # Kullanıcı adı başkası tarafından kullanılıyor mu?
    if username != request.api_user.get("username", ""):
        taken = sb.table("profiles").select("id").eq("username", username).neq(
            "id", me
        ).execute()
        if taken.data:
            return jsonify(error="username_taken"), 400

    is_private = _bool_form_field(request.form.get("is_private"))
    hide_last_seen = _bool_form_field(request.form.get("hide_last_seen"))
    update_data = {"full_name": full_name or None, "bio": bio or None, "username": username,
                   "is_private": is_private, "hide_last_seen": hide_last_seen}

    if avatar_file and avatar_file.filename:
        avatar_url = upload_image(avatar_file, folder="avatars")
        if not avatar_url:
            return jsonify(error="upload_failed"), 400
        update_data["avatar_url"] = avatar_url

    # profile_edit()'deki AYNI fallback: is_private/hide_last_seen kolonları
    # yoksa (migration henüz uygulanmamışsa) onlarsız tekrar dene.
    try:
        sb.table("profiles").update(update_data).eq("id", me).execute()
    except Exception:
        core_data = {"full_name": update_data["full_name"], "bio": update_data["bio"],
                     "username": update_data["username"]}
        if "avatar_url" in update_data:
            core_data["avatar_url"] = update_data["avatar_url"]
        sb.table("profiles").update(core_data).eq("id", me).execute()

    invalidate("valid_usernames")
    invalidate(f"sidebar:{me}")  # bio değişti, sidebar_stats cache'i bayat kaldı

    prof_rows = sb.table("profiles").select("*").eq("id", me).execute().data
    prof_data = prof_rows[0] if prof_rows else {}
    return jsonify(ok=True, profile={
        "id": me,
        "username": prof_data.get("username", username),
        "full_name": prof_data.get("full_name"),
        "bio": prof_data.get("bio"),
        "avatar_url": prof_data.get("avatar_url"),
        "is_private": prof_data.get("is_private", False),
        "hide_last_seen": prof_data.get("hide_last_seen", False),
    })


@bp.route("/notifications/preferences", methods=["GET", "POST"])
@api_login_required
def api_notification_preferences():
    """Bildirim türü bazlı opt-out ayarları — notifications.py preferences()'ın
    AYNI mantığı (fail-open: satır yoksa hepsi True), JSON döner.

    POST'ta web'deki HTML checkbox kodlamasından (yokluk == kapalı) FARKLI
    olarak native JSON'da her alan için gerçek bool taşır — eksik/yok alan
    yine de False sayılır (AYNI "yoksa kapalı" sonucu, farklı kodlama biçimi).
    """
    sb = get_sb()
    me = request.api_user["id"]
    columns = [t[1] for t in NOTIFICATION_TYPES]

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        try:
            payload = {col: bool(data.get(col, False)) for col in columns}
            payload["user_id"] = me
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            sb.table("notification_preferences").upsert(
                payload, on_conflict="user_id"
            ).execute()
        except Exception:
            # Kolon eksikse (migration uygulanmamışsa) veya başka DB hatası —
            # preferences()'daki AYNI graceful degradation, sayfa/istek kırılmaz.
            return jsonify(error="unavailable"), 503
        return jsonify(ok=True)

    try:
        rows = sb.table("notification_preferences").select("*").eq(
            "user_id", me
        ).execute().data
        prefs = rows[0] if rows else {col: True for col in columns}
    except Exception:
        prefs = {col: True for col in columns}

    return jsonify(preferences={col: bool(prefs.get(col, True)) for col in columns})


@bp.route("/close-friends")
@api_login_required
def api_close_friends_list():
    """Yakın arkadaşlar listesi — close_friends.py close_friends_list()'in
    AYNI mantığı (migration yoksa boş liste, sayfa/istek kırılmaz)."""
    sb = get_sb()
    me = request.api_user["id"]
    users = []
    try:
        rows = sb.table("close_friends").select(
            "profiles!close_friends_friend_id_fkey(id, username, avatar_url, full_name)"
        ).eq("owner_id", me).order("created_at", desc=True).execute().data
        users = [r["profiles"] for r in rows if r.get("profiles")]
    except Exception:
        users = []
    return jsonify(users=users)


@bp.route("/close-friends/add", methods=["POST"])
@api_login_required
def api_add_close_friend():
    """Yakın arkadaş ekle — close_friends.py add_close_friend()'in AYNI
    mantığı (kendini ekleme/engelli kontrolü dahil)."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    target_id = _str_field(data, "user_id")

    if not target_id:
        return jsonify(error="missing_user"), 400
    if target_id == me:
        return jsonify(error="cannot_add_self"), 400
    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="blocked"), 403

    try:
        sb.table("close_friends").upsert({"owner_id": me, "friend_id": target_id}).execute()
    except Exception:
        return jsonify(error="unavailable"), 503  # migration henüz uygulanmamış

    return jsonify(ok=True)


@bp.route("/close-friends/<user_id>/remove", methods=["POST"])
@api_login_required
def api_remove_close_friend(user_id):
    """Yakın arkadaşlardan çıkar — close_friends.py remove_close_friend()'in
    AYNI mantığı (satır yoksa delete no-op, yine de ok=True — idempotent)."""
    sb = get_sb()
    me = request.api_user["id"]
    sb.table("close_friends").delete().eq("owner_id", me).eq("friend_id", user_id).execute()
    return jsonify(ok=True)


def _api_user_has_password_identity(sb, user_id: str) -> bool:
    """auth.py _user_has_password_identity()'in native karşılığı.

    Web tarafı tarayıcıda saklı bir Supabase Auth access_token'ıyla
    auth.get_user() çağırıp identities okur. Native tarafta böyle bir token
    HİÇ YOK — api_tokens tamamen ayrı, kendi ürettiğimiz opak bir bearer
    sistemi (bkz. dosya başındaki modül docstring'i), native login() bir
    Supabase Auth session'ı hiç saklamıyor. Bu yüzden burada admin API
    (get_sb() zaten service-role) ile DOĞRUDAN user_id üzerinden identities
    okunur — sonuç (email/şifre identity'si var mı) AYNI, sadece erişim yolu
    farklı. Belirsiz durumda (admin API başarısız) fail-closed: şifre
    isteniyor kabul edilir — kaynak fonksiyonla AYNI güvenlik tavrı.
    """
    try:
        user_res = sb.auth.admin.get_user_by_id(user_id)
        user = getattr(user_res, "user", None)
        identities = getattr(user, "identities", None) or []
        return any(getattr(i, "provider", None) == "email" for i in identities)
    except Exception:
        return True


@bp.route("/profile/deactivate", methods=["POST"])
@api_login_required
def api_deactivate_account():
    """Hesabı deaktive et — profile.py deactivate_account()'ın AYNI mantığı
    (şifre doğrulaması koşullu + rate limit + is_deactivated=true).

    DİKKAT (native'e özgü sapma, kaynak fonksiyonda YOK): web session.clear()
    + user_sessions satırı silinerek TARAYICI oturumu kapatılır; native'de
    Flask session hiç yok. Bunun yerine bu isteğin taşıdığı Bearer token'ın
    KENDİSİ iptal edilir (logout()'daki AYNI desen) — deaktivasyon sonrası
    aynı token'la native tarafta hiçbir şey yapılamaz. Supabase Auth
    sign-out adımı da native'de YOK çünkü native login() zaten bir Supabase
    Auth session'ı hiç saklamıyor (kapatılacak bir tarayıcı session'ı yok).
    """
    sb = get_sb()
    me = request.api_user["id"]
    user_email = request.api_user.get("email")

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")

    # Google-only hesaplarda (hiç şifre yok) reverifikasyon adımı atlanır —
    # deactivate_account()'daki AYNI gerekçe (bkz. _api_user_has_password_identity).
    if _api_user_has_password_identity(sb, me):
        if not password:
            return jsonify(error="password_required"), 400

        # Rate limit — deactivate_account()'daki AYNI anahtar+limit (web ile
        # PAYLAŞILIR: aynı kullanıcı web+native'den art arda denerse ikisi
        # birlikte sayılır — login()'deki paylaşılan anahtar gerekçesiyle tutarlı).
        if is_rate_limited(f"deactivate:{me}", 5, 300):
            return jsonify(error="rate_limited"), 429

        try:
            tmp_auth = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            call_with_ssl_retry(
                lambda: tmp_auth.auth.sign_in_with_password({
                    "email": user_email, "password": password,
                })
            )
        except Exception:
            return jsonify(error="invalid_password"), 401

    try:
        sb.table("profiles").update({"is_deactivated": True}).eq("id", me).execute()
    except Exception:
        return jsonify(error="deactivation_failed"), 500

    # Bu isteğin token'ını iptal et — logout()'daki AYNI desen (yukarıdaki
    # docstring'deki gerekçe: native'de kapatılacak bir Flask session yok).
    try:
        sb.table("api_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat()
        }).eq("token_hash", request.api_token_hash).execute()
    except Exception:
        pass

    return jsonify(ok=True)


# ----------------------- 2FA (TOTP) -----------------------
#
# MİMARİ FARK (auth.py'deki web mfa_enroll()/mfa_disable()'dan): web, Flask
# session'da CANLI bir Supabase Auth access_token/refresh_token tutar ve
# mfa_enroll()/mfa_disable() bu STOK session'ı `set_session()` ile yeniden
# kullanır. Native'in request'ler arasında böyle bir session'ı YOK (login()
# sadece user.id/email almak için sign_in_with_password'ü GEÇİCİ kullanır,
# sonucu hiç saklamaz — bkz. modül docstring'i). Bu yüzden aşağıdaki HER
# endpoint, gereken Supabase Auth session'ı KENDİ İÇİNDE taze bir
# sign_in_with_password ile kurar ve bu session'ı response'a KAYDETMEDEN
# sadece o isteğin ömrü boyunca kullanır (bkz. _api_fresh_mfa_client).
#
# BİLİNÇLİ SAPMA: web'in "Google-only hesapta şifre adımı atlanır" davranışı
# (_user_has_password_identity kontrolü) BURADA UYGULANMAZ — native login()
# SADECE email+password ile çalışır (Google OAuth native'de yok), yani bir
# native api_token'ı olan HER kullanıcının zaten bir şifre identity'si var.
# Bu yüzden native 2FA endpoint'lerinde şifre HER ZAMAN zorunludur.


def _api_fresh_mfa_client(email: str, password: str):
    """Şifreyi taze `sign_in_with_password` ile doğrulayıp session'ı set
    edilmiş YENİ bir temp client döndürür (başarısızsa None).

    Sign-in için kullanılan client'ı DOĞRUDAN reuse etmek yerine set_session
    için AYRI bir client kurulur (login()'deki tek-client desenden bilinçli
    bir sapma) — bu fonksiyon üç farklı endpoint'ten çağrıldığı için taze bir
    client örneği her seferinde daha güvenli/öngörülebilir.
    """
    try:
        tmp_auth = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        res = call_with_ssl_retry(
            lambda: tmp_auth.auth.sign_in_with_password({
                "email": email, "password": password,
            })
        )
    except Exception:
        return None

    sess = getattr(res, "session", None)
    if not sess or not getattr(sess, "access_token", None):
        return None

    tmp = create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
    )
    tmp.auth.set_session(sess.access_token, getattr(sess, "refresh_token", None))
    return tmp


@bp.route("/2fa/status")
@api_login_required
def api_2fa_status():
    """2FA (TOTP) durumu — şifre GEREKMEZ (read-only).

    Tasarım kararı: `sb.auth.admin.get_user_by_id()` (service-role) döndürdüğü
    User nesnesindeki `factors` alanı CANLI durumu yansıdığı gerçek Supabase'e
    karşı DOĞRULANDI (enroll sonrası "unverified", verify sonrası anında
    "verified" olarak görünüyor) — bu yüzden taze bir Auth session kurmaya
    (sign_in_with_password) hiç gerek yok, admin API tek başına yeterli.
    """
    sb = get_sb()
    me = request.api_user["id"]
    enabled = False
    try:
        user_res = sb.auth.admin.get_user_by_id(me)
        user = getattr(user_res, "user", None)
        factors = getattr(user, "factors", None) or []
        enabled = any(
            f.factor_type == "totp" and f.status == "verified" for f in factors
        )
    except Exception:
        enabled = False
    return jsonify(enabled=enabled)


@bp.route("/2fa/enroll", methods=["POST"])
@api_login_required
def api_2fa_enroll():
    """2FA (TOTP) kurulumunu BAŞLAT — auth.py mfa_enroll() GET+QR adımının
    native karşılığı.

    Native'de saklanacak bir Flask session yok (bkz. modül başındaki bölüm
    docstring'i) — bu yüzden web'in aksine factor_id/secret/qr_code DOĞRUDAN
    client'a döner; client bunları bir sonraki /2fa/enroll/verify çağrısına
    kadar kendi hafızasında tutar.
    """
    me = request.api_user["id"]
    email = request.api_user.get("email")

    # Rate limit — web ile PAYLAŞILAN aynı anahtar (aynı kullanıcı web+native'den
    # art arda denerse ikisi birlikte sayılır, login()'deki paylaşılan-limit
    # gerekçesiyle tutarlı).
    if is_rate_limited(f"2fa_enroll:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    if not password:
        return jsonify(error="password_required"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    # Zaten etkin mi kontrolü — auth.py GET akışındaki AYNI fail-open davranış:
    # bu kontrol başarısız olursa (örn. geçici ağ hatası) enroll'a normal
    # devam edilir, istek engellenmez.
    try:
        factors = tmp.auth.mfa.list_factors()
        if any(f.factor_type == "totp" and f.status == "verified" for f in (factors.totp or [])):
            return jsonify(error="already_enabled"), 409
    except Exception:
        pass

    try:
        enrollment = tmp.auth.mfa.enroll({"factor_type": "totp", "issuer": "Sosyal Medya"})
    except Exception:
        return jsonify(error="enroll_failed"), 500

    return jsonify(
        factor_id=enrollment.id,
        secret=getattr(enrollment.totp, "secret", ""),
        qr_code=getattr(enrollment.totp, "qr_code", ""),
    )


@bp.route("/2fa/enroll/verify", methods=["POST"])
@api_login_required
def api_2fa_enroll_verify():
    """2FA (TOTP) kurulumunu TAMAMLA — kimlik doğrulayıcı uygulamadan alınan
    6 haneli kodu doğrular ve factor'ü "verified" durumuna geçirir."""
    me = request.api_user["id"]
    email = request.api_user.get("email")

    # AYNI `2fa_enroll:{me}` anahtarı — enroll başlangıç+bitiş aynı 5/300
    # bütçesinden düşer, ayrı bir sayaç AÇILMAZ.
    if is_rate_limited(f"2fa_enroll:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    factor_id = _str_field(data, "factor_id")
    code = _str_field(data, "code")

    # Format kontrolü — auth.py mfa_enroll()'daki AYNI kontrol
    # (len(code)==6 and code.isdigit()), sign-in denemeden ÖNCE yapılır.
    if not code or len(code) != 6 or not code.isdigit():
        return jsonify(error="invalid_code_format"), 400
    if not password:
        return jsonify(error="password_required"), 400
    if not factor_id:
        return jsonify(error="missing_factor_id"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    try:
        challenge_resp = tmp.auth.mfa.challenge({"factor_id": factor_id})
        tmp.auth.mfa.verify({
            "factor_id": factor_id,
            "challenge_id": challenge_resp.id,
            "code": code,
        })
    except Exception as e:
        msg = str(e)
        if "Invalid" in msg:
            return jsonify(error="invalid_code"), 401
        return jsonify(error="verify_failed"), 500

    return jsonify(ok=True)


@bp.route("/2fa/disable", methods=["POST"])
@api_login_required
def api_2fa_disable():
    """2FA (TOTP) devre dışı bırak — auth.py mfa_disable()'ın native karşılığı,
    ANCAK ekstra bir `code` adımıyla (bkz. aşağıdaki DİKKAT notu — spesifikasyondan
    BİLİNÇLİ bir sapma, gerçek Supabase'e karşı test edilerek bulundu).

    DİKKAT (spesifikasyondan sapma — gerçek Supabase'e karşı test sırasında
    keşfedildi): kurulu `supabase-auth` kütüphanesi `mfa.unenroll()`'ı şöyle
    belgeliyor: "Unenrolling a verified MFA factor cannot be done from a
    session with an aal1 authenticator level." Sadece şifreyle (sign_in_with_
    password) elde edilen bir session AAL1'dir — gerçek bir hesaba karşı
    denendi, `AAL2 required to unenroll verified factor` hatasıyla KESİN
    olarak doğrulandı. Bu yüzden password-only tasarım aktif bir 2FA'yı ASLA
    kapatamaz (sürekli 500/disable_failed döner). Çözüm: verified factor
    bulunduktan sonra, kullanıcıdan bir TOTP `code` da istenir; bu kodla
    challenge+verify yapılıp session AAL2'ye yükseltilir (login()'deki AYNI
    challenge/verify deseni, ama zaten verified bir factor üzerinde) — ANCAK
    o zaman unenroll çağrılabilir. auth.py'deki web mfa_disable() da AYNI
    kodu (sadece şifre) kullanıyor — muhtemelen AYNI bug orada da var, bu
    web tarafı için AYRI bir security/backend task'ı gerektirir (bu görevin
    kapsamı dışında, app/auth.py'ye dokunulmadı).

    Google-only kısayolu YOK (bkz. bölüm başındaki BİLİNÇLİ SAPMA notu) —
    şifre her zaman zorunlu.
    """
    me = request.api_user["id"]
    email = request.api_user.get("email")

    if is_rate_limited(f"2fa_disable:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    code = _str_field(data, "code")
    if not password:
        return jsonify(error="password_required"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    try:
        factors = tmp.auth.mfa.list_factors()
    except Exception:
        return jsonify(error="disable_failed"), 500

    totp_factor = None
    for f in (factors.totp or []):
        if f.status == "verified":
            totp_factor = f
            break
    if not totp_factor:
        return jsonify(error="no_active_2fa"), 404

    # AAL2'ye yükseltme adımı — yukarıdaki DİKKAT notu: unenroll'dan ÖNCE
    # zorunlu. `code` yoksa client'a bunu AÇIKÇA bildir (login()'deki
    # mfa_required/tekrar-dene deseniyle tutarlı — client aynı isteği `code`
    # ekleyerek tekrar atar).
    if not code:
        return jsonify(error="code_required"), 400
    if len(code) != 6 or not code.isdigit():
        return jsonify(error="invalid_code_format"), 400

    try:
        challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
        tmp.auth.mfa.verify({
            "factor_id": totp_factor.id,
            "challenge_id": challenge_resp.id,
            "code": code,
        })
    except Exception as e:
        msg = str(e)
        if "Invalid" in msg:
            return jsonify(error="invalid_code"), 401
        return jsonify(error="verify_failed"), 500

    try:
        tmp.auth.mfa.unenroll({"factor_id": totp_factor.id})
    except Exception:
        return jsonify(error="disable_failed"), 500

    return jsonify(ok=True)
