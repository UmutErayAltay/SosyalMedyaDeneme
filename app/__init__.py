"""Uygulama fabrikası."""
import os
import secrets
from datetime import datetime, timedelta, timezone
from flask import Flask, session, request, abort, send_from_directory, redirect, url_for
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


def relative_time(value):
    """UTC ISO timestamp'i göreceli zamana (Türkçe) çevirir: 'az önce', 'N dakika önce', vs.
    Çevrimiçi durumu göstermek için (last_seen_at) — 1:1 sohbetlerde fallback olur."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Yerel saate çevir (UTC+3)
        local_dt = dt.astimezone(_LOCAL_TZ)
        now = datetime.now(_LOCAL_TZ)
        delta = now - local_dt

        total_seconds = int(delta.total_seconds())

        # Gelecekte varsa "şimdi" olarak göster
        if total_seconds < 0:
            return "şu anda"

        # Az önce (1 dakikadan az)
        if total_seconds < 60:
            return "az önce"

        # Dakika
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes} dakika önce"

        # Saat
        hours = total_seconds // 3600
        if hours < 24:
            return f"{hours} saat önce"

        # Gün
        days = total_seconds // 86400
        if days < 7:
            return f"{days} gün önce"

        # Çok eski — tarih olarak göster
        return local_dt.strftime("%d.%m.%Y")
    except Exception:
        return ""


def _csrf_token() -> str:
    """Session'a bağlı CSRF token'ı döndürür (yoksa üretir)."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Render (ve benzeri PaaS'ler) TLS'i kendi kenarında sonlandırıp uygulamaya
    # DÜZ HTTP ile bağlanır — bu middleware olmadan url_for(..., _external=True)
    # (Google OAuth redirect_to, şifre sıfırlama e-postası linki) canlıda
    # "http://" üretir, gerçek site "https://" olduğu için Supabase'in
    # redirect_to eşleşmesi/kullanıcı deneyimi bozulur. ProxyFix, Render'ın
    # eklediği X-Forwarded-Proto (ve Host) başlığına GÜVENİLİR TEK atlama
    # için bakar; header yoksa (yerel geliştirme) hiçbir şeyi değiştirmez.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # --- Presence (last-seen) & Session validation ---
    # Her HTTP request'te, oturum açmış kullanıcı "son görülme" zamanını güncelle.
    # Profil/mesaj listesinde online status göstermek için kullanılır.
    # Aynı zamanda aktif oturumun geçerli olup olmadığını kontrol et (uzaktan
    # sonlandırılmışsa oturumu düşür).
    @app.before_request
    def update_presence():
        if session.get("user"):
            from .presence import mark_seen
            mark_seen(session["user"]["id"])

            # Oturum kaydını doğrula (uzaktan sonlandırılmışsa logout yap)
            session_record_id = session.get("session_record_id")
            if session_record_id:
                from .user_sessions import touch_session
                # Auth routeları oturum doğrulamasından muaf (giriş döngüsü olmasın)
                if request.endpoint and not request.endpoint.startswith("auth."):
                    if not touch_session(session_record_id):
                        # Oturum uzaktan sonlandırılmış
                        session.clear()
                        return redirect(url_for("auth.login"))

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

    # --- Statik varlık sürümleme (cache-busting) ---
    # Her url_for('static', ...) çıktısına dosyanın mtime'ı ?v= olarak eklenir:
    # dosya her değiştiğinde URL değişir, tarayıcı/servis-çalışanı önbelleği ne
    # kadar agresif olursa olsun YENİ dosyayı çekmek zorunda kalır. Kullanıcı
    # artık her deploy'dan sonra Ctrl+F5 / "clear site data" yapmak zorunda
    # değil (Brave'de bayat call.js eski arama arayüzünü diriltip butonları
    # ölü bırakıyordu — kullanıcı raporu). os.stat çağrısı mikrosaniyeler
    # mertebesinde; sayfa başına ~10 varlık için cache'e gerek yok.
    @app.url_defaults
    def _static_cache_bust(endpoint, values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            try:
                fp = os.path.join(app.static_folder, values["filename"])
                values["v"] = int(os.stat(fp).st_mtime)
            except OSError:
                pass  # dosya yoksa sürümsüz URL (404 zaten görünür olur)

    # --- Dinamik yanıtlar (HTML/JSON) ASLA önbelleklenmez ---
    # Cache-Control başlığı olmayan sayfaları tarayıcı, geri/ileri gezinmede
    # diskteki eski kopyadan YENİDEN DOĞRULAMADAN gösterebiliyor: eski HTML →
    # içindeki eski ?v= URL'leri → 1 gün taze sayılan eski JS → eski arama
    # arayüzü hortluyor, kullanıcı "clear site data" yapmak zorunda kalıyordu
    # (Brave'de canlı rapor). no-store bunu kökten keser; statik dosyalar bu
    # bloktan ETKİLENMEZ (Flask static endpoint'i kendi max-age başlığını
    # koyar, ?v=mtime sürümlemesiyle uzun cache güvenli). İçerik zaten
    # kullanıcıya özel/sürekli değişken olduğundan no-store ayrıca paylaşılan
    # cihazda veri sızıntısını da önler.
    @app.after_request
    def _no_store_dynamic(response):
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

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

    # Navbar "Mesajlar" rozeti — bildirim ziliyle aynı desen (20sn TTL cache,
    # messagesBadge.js 25sn polling ile senkron). SADECE 1:1 konuşmalar
    # sayılır — bkz. unread_message_count() docstring'i (grup mesajlarında
    # read_at hiç set edilmiyor).
    @app.context_processor
    def inject_unread_messages():
        if not session.get("user"):
            return {}
        try:
            from .supabase_client import get_sb
            from .cache import get_cached
            from .messaging._common import unread_message_count
            user_id = session["user"]["id"]
            count = get_cached(f"unread_msgs:{user_id}", 20, lambda: unread_message_count(get_sb(), user_id))
        except Exception:
            count = 0
        return {"unread_messages": count}

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
    from .mutes import bp as mutes_bp
    from .gifs import bp as gifs_bp
    from .stickers import bp as stickers_bp
    from .push import bp as push_bp

    # Emoji reaksiyon ikonları şablonlarda {{ REACTIONS['love'] }} olarak kullanılabilir
    app.jinja_env.globals["REACTIONS"] = REACTIONS
    # Post içeriğini XSS-güvenli şekilde render eder + #hashtag'leri linkler
    app.jinja_env.filters["linkify_hashtags"] = linkify_hashtags
    # @kullanıcı etiketlerini (sadece gerçekten var olan kullanıcı adlarını) linkler
    app.jinja_env.filters["linkify_mentions"] = linkify_mentions
    # Ham UTC timestamp'leri yerel saate (Europe/Istanbul) çevirir
    app.jinja_env.filters["local_time"] = local_time
    # Ham UTC timestamp'leri göreceli zamana (Türkçe) çevirir: az önce, N dakika önce, vs.
    app.jinja_env.filters["relative_time"] = relative_time

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
    app.register_blueprint(mutes_bp)
    app.register_blueprint(gifs_bp)
    app.register_blueprint(stickers_bp)
    app.register_blueprint(push_bp)

    # GEÇİCİ teşhis endpoint'i — Render/Cloudflare proxy zincirinin gerçek
    # X-Forwarded-* başlıklarını görmek için (ProxyFix hop sayısı ayarı).
    # Google OAuth redirect_to şeması sorunu çözülünce KALDIRILACAK.
    @app.route("/_debug_headers")
    def _debug_headers():
        from flask import jsonify
        return jsonify(
            all_headers=dict(request.headers),
            resolved_scheme=request.scheme,
            resolved_host=request.host,
            wsgi_url_scheme=request.environ.get("wsgi.url_scheme"),
        )

    return app
