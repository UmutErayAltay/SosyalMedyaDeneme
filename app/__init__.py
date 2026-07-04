"""Uygulama fabrikası."""
import secrets
from flask import Flask, session, request, abort
from .config import Config


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

    # Navbar zil rozeti: her sayfada okunmamış bildirim sayısını enjekte eder.
    # Supabase geçici olarak erişilemezse rozet sessizce 0 gösterir — sayfa
    # render'ı asla bu yüzden kırılmaz.
    @app.context_processor
    def inject_unread_notifications():
        if not session.get("user"):
            return {}
        try:
            from .supabase_client import get_sb
            count = get_sb().table("notifications").select(
                "id", count="exact", head=True
            ).eq("recipient_id", session["user"]["id"]).eq("is_read", False).execute().count or 0
        except Exception:
            count = 0
        return {"unread_notifications": count}

    # Blueprint kayıtları (döngüsel import'u önlemek için fonksiyon içinde)
    from .auth import bp as auth_bp
    from .routes import bp as routes_bp
    from .social import bp as social_bp
    from .messaging import bp as messaging_bp
    from .notifications import bp as notifications_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(social_bp, url_prefix="/social")
    app.register_blueprint(messaging_bp, url_prefix="/messages")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")

    return app
