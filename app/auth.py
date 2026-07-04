"""Kimlik doğrulama: kayıt / giriş / çıkış.

Supabase Auth kullanır. Kayıt akışı:
1) admin.create_user ile auth.users'a kayıt (email_confirm=True -> onay bypass)
2) profiles tablosunu garantiye al (upsert)
3) sign_in ile session al -> otomatik giriş

Arkadaşlar arası test için email confirmation BYPASS ediliyor.
"""
import time
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from supabase import create_client
from .supabase_client import get_sb, get_auth, call_with_ssl_retry

bp = Blueprint("auth", __name__)

# Basit bellek-içi rate limit (IP başına) — şifre sıfırlama isteklerinin
# kötüye kullanımını (spam e-posta, enumeration denemesi) yavaşlatır.
# NOT: tek process varsayımıyla çalışır; çoklu worker'a geçilirse Redis gerekir.
_reset_attempts: dict[str, list[float]] = {}
_RESET_MAX_ATTEMPTS = 3
_RESET_WINDOW_SECONDS = 600


def _reset_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _reset_attempts.get(ip, []) if now - t < _RESET_WINDOW_SECONDS]
    attempts.append(now)
    _reset_attempts[ip] = attempts
    return len(attempts) > _RESET_MAX_ATTEMPTS


def _save_session(res) -> bool:
    """sign_in cevabindan session bilgisini sakla (avatar_url dahil)."""
    user = getattr(res, "user", None)
    s = getattr(res, "session", None)
    if user and s and getattr(s, "access_token", None):
        session["user"] = {"id": user.id, "email": user.email}
        session["access_token"] = s.access_token
        session["refresh_token"] = getattr(s, "refresh_token", None)
        session.permanent = True

        # Profile'dan avatar_url + username'i de session'a al (navbar için)
        try:
            prof = get_sb().table("profiles").select("avatar_url, username").eq(
                "id", user.id
            ).execute()
            if prof.data:
                session["user"]["avatar_url"] = prof.data[0].get("avatar_url")
                session["user"]["username"] = prof.data[0].get("username")
        except Exception:
            pass

        return True
    return False


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        username = request.form.get("username", "").strip()

        if not email or not password or not username:
            flash("Tüm alanları doldur.", "error")
            return redirect(url_for("auth.register"))

        try:
            # 1) Admin API ile kullanıcı oluştur (email onayını atla)
            admin = get_sb()
            res = admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"username": username},
            })
            user = res.user
            if not user:
                flash("Kayıt başarısız: " + str(res), "error")
                return redirect(url_for("auth.register"))

            # 2) profiles tablosunu garantiye al
            try:
                admin.table("profiles").upsert({
                    "id": user.id,
                    "username": username,
                    "email": email,
                }, on_conflict="id").execute()
            except Exception:
                pass  # trigger zaten oluşturmuş olabilir

            # 3) Otomatik giriş (sign_in ile geçerli session al)
            try:
                login_res = call_with_ssl_retry(
                    lambda: get_auth().auth.sign_in_with_password({
                        "email": email,
                        "password": password,
                    })
                )
                _save_session(login_res)
                flash(f"Hoş geldin {username}!", "success")
                return redirect(url_for("routes.feed"))
            except Exception:
                # Giriş başarısız -> login sayfasına yönlendir
                flash("Hesap oluşturuldu! Giriş yap.", "success")
                return redirect(url_for("auth.login"))
        except Exception as e:
            flash(f"Hata: {e}", "error")
            return redirect(url_for("auth.register"))

    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        try:
            res = call_with_ssl_retry(
                lambda: get_auth().auth.sign_in_with_password({
                    "email": email,
                    "password": password,
                })
            )
            if not _save_session(res):
                flash("E-posta veya şifre hatalı.", "error")
                return redirect(url_for("auth.login"))
            return redirect(url_for("routes.feed"))
        except Exception as e:
            msg = str(e)
            if "Invalid login credentials" in msg:
                flash("E-posta veya şifre hatalı.", "error")
            elif "Email not confirmed" in msg:
                flash("E-posta adresin onaylanmamış.", "error")
            else:
                flash(f"Giriş hatası: {msg}", "error")
            return redirect(url_for("auth.login"))

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    try:
        get_auth().auth.sign_out()
    except Exception:
        pass
    session.clear()
    flash("Çıkış yapıldı.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()

        if _reset_rate_limited(request.remote_addr or "unknown"):
            flash("Çok fazla deneme yaptın. Lütfen biraz sonra tekrar dene.", "error")
            return redirect(url_for("auth.login"))

        if email:
            try:
                redirect_url = url_for("auth.reset_password", _external=True)
                call_with_ssl_retry(
                    lambda: get_auth().auth.reset_password_for_email(
                        email, {"redirect_to": redirect_url}
                    )
                )
            except Exception:
                pass  # kullanıcı enumeration'ı önlemek için hata da yutulur

        # E-posta sistemde kayıtlı olsun/olmasın AYNI jenerik mesaj — enumeration önleme
        flash("Eğer bu e-posta adresi kayıtlıysa, bir şifre sıfırlama linki gönderildi.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Supabase'in gönderdiği recovery linki buraya redirect eder.

    Token'lar implicit flow ile URL fragment'ında (#access_token=...) gelir;
    fragment sunucuya hiç gönderilmez, bu yüzden reset_password.html
    içindeki script window.location.hash'i okuyup token'ları gizli form
    alanlarına yazar, form normal POST ile sunucuya iletir.
    """
    if request.method == "POST":
        access_token = request.form.get("access_token", "")
        refresh_token = request.form.get("refresh_token", "")
        password = request.form.get("password", "").strip()

        if not access_token or len(password) < 6:
            flash("Geçersiz istek veya şifre çok kısa (en az 6 karakter).", "error")
            return redirect(url_for("auth.reset_password"))

        try:
            # Paylaşılan get_auth() singleton'ı yerine tek kullanımlık client:
            # set_session çağrısı client durumunu mutasyona uğratır, paylaşılan
            # client'ta bu başka isteklerle yarışa (race condition) girer.
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(access_token, refresh_token)
            tmp.auth.update_user({"password": password})
        except Exception:
            flash("Link süresi dolmuş veya geçersiz. Yeniden şifre sıfırlama isteği gönder.", "error")
            return redirect(url_for("auth.forgot_password"))

        flash("Şifren güncellendi. Şimdi giriş yapabilirsin.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html")
