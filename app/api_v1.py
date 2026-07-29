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

2FA: aktif TOTP'si olan bir hesap API üzerinden login olmaya çalışırsa şifre
doğru olsa bile ham token ÜRETİLMEZ (403 mfa_required) — Faz 1'de API
üzerinden 2FA doğrulama akışı yok, bu SESSİZCE bypass edilmez, client'a
açıkça bildirilir. Faz 2'de TOTP kodu ikinci bir API adımıyla eklenecek.
"""
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, current_app
from supabase import create_client

from .supabase_client import get_sb, call_with_ssl_retry
from .rate_limit import is_rate_limited
from .routes._common import _attach_post_metrics, attach_repost_of, PAGE_SIZE, _profile
from .visibility import visible_or_filter, followed_and_self_ids, close_friend_author_ids, filter_visible
from .blocks import blocked_user_ids, filter_not_blocked, has_blocked, is_blocked_either_way
from .mutes import muted_user_ids
from .polls import attach_polls
from .presence import is_online
from .notifications import notify
from .cache import invalidate

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
    # TOTP factor varsa, ŞİFRE DOĞRU OLSA BİLE token üretilmez.
    has_totp = False
    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        tmp.auth.set_session(sess.access_token, getattr(sess, "refresh_token", None))
        factors = tmp.auth.mfa.list_factors()
        has_totp = any(
            f.factor_type == "totp" and f.status == "verified"
            for f in (factors.totp or [])
        )
    except Exception:
        # MFA kontrolü başarısız — 2FA yok sayılır (auth.py login()'deki fail-open
        # davranışın aynısı; kontrol servisi geçiciyse giriş engellenmemeli)
        pass

    if has_totp:
        return jsonify(
            error="mfa_required",
            message="Bu hesapta 2FA aktif. Faz 1 API'si TOTP doğrulama akışını henüz desteklemiyor.",
        ), 403

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
