"""Kimlik doğrulama: kayıt / giriş / çıkış.

Supabase Auth kullanır. Kayıt akışı:
1) admin.create_user ile auth.users'a kayıt (email_confirm=True -> onay bypass)
2) profiles tablosunu garantiye al (upsert)
3) sign_in ile session al -> otomatik giriş

Arkadaşlar arası test için email confirmation BYPASS ediliyor.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from .supabase_client import get_sb, get_auth

bp = Blueprint("auth", __name__)


def _save_session(res) -> bool:
    """sign_in cevabindan session bilgisini sakla."""
    user = getattr(res, "user", None)
    s = getattr(res, "session", None)
    if user and s and getattr(s, "access_token", None):
        session["user"] = {"id": user.id, "email": user.email}
        session["access_token"] = s.access_token
        session["refresh_token"] = getattr(s, "refresh_token", None)
        session.permanent = True
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
                login_res = get_auth().auth.sign_in_with_password({
                    "email": email,
                    "password": password,
                })
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
            res = get_auth().auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
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
