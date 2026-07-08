"""Uygulama fabrikası."""
import secrets
from datetime import datetime, timedelta, timezone
from flask import Flask, session, request, abort, send_from_directory
from .config import Config

# Sabit UTC+3 — Türkiye 2016'dan beri yaz saati uygulamıyor, bu yüzden
# zoneinfo/tzdata bağımlılığı (Windows'ta IANA veritabanı gelmiyor, ayrıca
# kurulum gerektirir) yerine sabit offset kullanmak hem yeterli hem daha
# güvenilir (dağıtım ortamında eksik paket riski yok).
_LOCAL_TZ = timezone(timedelta(hours=3))


def local_time(value, fmt="%Y-%m-%d %H:%M"):
    """Supabase'in döndürdüğü UTC ISO timestamp'i yerel saate (UTC+3) çevirir.
    Önceden şablonlar ham UTC string'i `[:16]` ile kesip doğrudan gösteriyordu
    (kullanıcı raporu: 19:45'te paylaşılan post 16:44 gösteriyordu — tam 3
    saatlik UTC farkı, hiç dönüşüm yapılmıyordu)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_LOCAL_TZ).strftime(fmt)
    except Exception:
        return value[:16].replace("T", " ")  # eski davranışa (ham UTC) düş


def _csrf_token() -> str:
    """Session'a bağlı CSRF token'ı döndürür (yoksa üretir)."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # --- CSRF koruması ---
    # Tüm POST istekleri form alanı (csrf_token) veya header (X-CSRF-Token)
    # üzerinden session token'ı ile doğrulanır.
    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            expected = session.get("_csrf_token")
            sent = (request.form.get("csrf_token")
                    or request.headers.get("X-CSRF-Token"))
            if not expected or not sent or not secrets.compare_digest(expected, sent):
                abort(400, description="CSRF doğrulaması başarısız.")

    # Şablonlarda {{ csrf_token() }} olarak kullanılabilir
    app.jinja_env.globals["csrf_token"] = _csrf_token

    # PWA: servis çalışanı KÖK dizinden (/sw.js) sunulur — /static/sw.js olsaydı
    # varsayılan scope'u sadece /static/ ile sınırlı kalır, tüm siteyi kontrol edemezdi.
    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Cache-Control"] = "no-cache"
        return response

    # Navbar zil rozeti: her sayfada okunmamış bildirim sayısını enjekte eder.
    # Supabase geçici olarak erişilemezse rozet sessizce 0 gösterir — sayfa
    # render'ı asla bu yüzden kırılmaz. 20 saniye TTL per-user cache ile
    # optimize edilir (navbar.js 25 saniyede bir fetch ile tazeler).
    @app.context_processor
    def inject_unread_notifications():
        if not session.get("user"):
            return {}
        try:
            from .supabase_client import get_sb
            from .cache import get_cached
            user_id = session["user"]["id"]
            def _fetch():
                return get_sb().table("notifications").select(
                    "id", count="exact", head=True
                ).eq("recipient_id", user_id).eq("is_read", False).execute().count or 0
            count = get_cached(f"unread:{user_id}", 20, _fetch)
        except Exception:
            count = 0
        return {"unread_notifications": count}

    # Blueprint kayıtları (döngüsel import'u önlemek için fonksiyon içinde)
    from .auth import bp as auth_bp
    from .routes import bp as routes_bp
    from .social import bp as social_bp, REACTIONS
    from .messaging import bp as messaging_bp
    from .notifications import bp as notifications_bp
    from .hashtags import bp as hashtags_bp, linkify_hashtags
    from .mentions import linkify_mentions
    from .blocks import bp as blocks_bp
    from .polls import bp as polls_bp
    from .reports import bp as reports_bp
    from .admin import bp as admin_bp
    from .stories import bp as stories_bp
    from .close_friends import bp as close_friends_bp
    from .gifs import bp as gifs_bp
    from .stickers import bp as stickers_bp

    # Emoji reaksiyon ikonları şablonlarda {{ REACTIONS['love'] }} olarak kullanılabilir
    app.jinja_env.globals["REACTIONS"] = REACTIONS
    # Post içeriğini XSS-güvenli şekilde render eder + #hashtag'leri linkler
    app.jinja_env.filters["linkify_hashtags"] = linkify_hashtags
    # @kullanıcı etiketlerini (sadece gerçekten var olan kullanıcı adlarını) linkler
    app.jinja_env.filters["linkify_mentions"] = linkify_mentions
    # Ham UTC timestamp'leri yerel saate (Europe/Istanbul) çevirir
    app.jinja_env.filters["local_time"] = local_time

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(social_bp, url_prefix="/social")
    app.register_blueprint(messaging_bp, url_prefix="/messages")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(hashtags_bp)
    app.register_blueprint(blocks_bp, url_prefix="/social")
    app.register_blueprint(polls_bp)
    app.register_blueprint(reports_bp, url_prefix="/social")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(stories_bp)
    app.register_blueprint(close_friends_bp)
    app.register_blueprint(gifs_bp)
    app.register_blueprint(stickers_bp)

    return app
