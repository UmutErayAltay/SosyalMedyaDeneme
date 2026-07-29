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
from .routes._common import _attach_post_metrics, attach_repost_of, PAGE_SIZE
from .visibility import visible_or_filter, followed_and_self_ids, close_friend_author_ids, filter_visible
from .blocks import blocked_user_ids, filter_not_blocked
from .mutes import muted_user_ids
from .polls import attach_polls

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
