"""Uygulama fabrikası."""
from flask import Flask
from .config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Blueprint kayıtları (döngüsel import'u önlemek için fonksiyon içinde)
    from .auth import bp as auth_bp
    from .routes import bp as routes_bp
    from .social import bp as social_bp
    from .messaging import bp as messaging_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(social_bp, url_prefix="/social")
    app.register_blueprint(messaging_bp, url_prefix="/messages")

    return app
