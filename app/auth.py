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
from .rate_limit import is_rate_limited

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


def _check_banned(user_id: str) -> bool:
    """Kullanıcı hesabı askıya alınmış mı kontrol et.

    True döndürürse hesap banned, Supabase oturumu temizlenir.
    """
    try:
        prof = get_sb().table("profiles").select(
            "is_banned"
        ).eq("id", user_id).execute()
        prof_data = prof.data[0] if prof.data else {}
        if prof_data.get("is_banned"):
            try:
                get_auth().auth.sign_out()
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _finalize_login(user_id: str, email: str, access_token: str, refresh_token: str = None) -> bool:
    """Session'ı tam kur: profile verisi + token'lar.

    Ban kontrolü yapıldıktan SONRA çağrılmalı. True döndürürse başarılı.
    """
    try:
        prof = get_sb().table("profiles").select(
            "avatar_url, username, is_admin"
        ).eq("id", user_id).execute()
        prof_data = prof.data[0] if prof.data else {}
    except Exception:
        prof_data = {}

    session["user"] = {"id": user_id, "email": email}
    session["access_token"] = access_token
    session["refresh_token"] = refresh_token
    session.permanent = True
    session["user"]["avatar_url"] = prof_data.get("avatar_url")
    session["user"]["username"] = prof_data.get("username")
    session["user"]["is_admin"] = bool(prof_data.get("is_admin"))
    return True


def _save_session(res):
    """sign_in cevabindan session bilgisini sakla (avatar_url, is_admin dahil).

    Döndürülen değer: True (başarılı), False (session alınamadı) veya
    "banned" (kimlik bilgileri doğru ama hesap askıya alınmış — bu durumda
    session HİÇ kurulmaz, Supabase oturumu da hemen kapatılır).
    """
    user = getattr(res, "user", None)
    s = getattr(res, "session", None)
    if user and s and getattr(s, "access_token", None):
        # Ban kontrolü — session kurmadan önce
        if _check_banned(user.id):
            return "banned"

        _finalize_login(user.id, user.email, s.access_token, getattr(s, "refresh_token", None))
        return True
    return False


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        username = request.form.get("username", "").strip()

        # Spam hesap oluşturmayı yavaşlatır (IP başına) — gerçek kullanıcı
        # trafiği gelmeden önce eklenmesi gereken bir güvenlik önlemi.
        if is_rate_limited(f"register:{request.remote_addr or 'unknown'}", 5, 600):
            flash("Çok fazla kayıt denemesi yaptın. Lütfen biraz sonra tekrar dene.", "error")
            return redirect(url_for("auth.register"))

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
                from .cache import invalidate
                invalidate("valid_usernames")
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

        # Kaba kuvvet (brute force) denemelerini yavaşlatır — IP başına.
        if is_rate_limited(f"login:{request.remote_addr or 'unknown'}", 10, 300):
            flash("Çok fazla giriş denemesi yaptın. Lütfen biraz sonra tekrar dene.", "error")
            return redirect(url_for("auth.login"))

        try:
            res = call_with_ssl_retry(
                lambda: get_auth().auth.sign_in_with_password({
                    "email": email,
                    "password": password,
                })
            )

            # Ban kontrolü önce
            if _check_banned(res.user.id):
                flash("Hesabın askıya alınmış. Bir yöneticiyle iletişime geç.", "error")
                return redirect(url_for("auth.login"))

            # MFA kontrolü: sign_in başarılı ve ban değil, ama session HENÜZ kurmamışız
            has_totp = False
            try:
                tmp = create_client(
                    current_app.config["SUPABASE_URL"],
                    current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                )
                tmp.auth.set_session(res.session.access_token, res.session.refresh_token)
                factors = tmp.auth.mfa.list_factors()

                # Aktif TOTP factor var mı?
                has_totp = any(
                    f.factor_type == "totp" and f.status == "verified"
                    for f in (factors.totp or [])
                )
            except Exception:
                # MFA kontrolü başarısız — 2FA yok sayılır, normal giriş devam et
                pass

            if has_totp:
                # 2FA gerekli — session KURMADAN pending state al
                session["mfa_pending_user_id"] = res.user.id
                session["mfa_pending_email"] = res.user.email
                session["mfa_pending_access_token"] = res.session.access_token
                session["mfa_pending_refresh_token"] = res.session.refresh_token
                flash("2FA kodu gir.", "info")
                return redirect(url_for("auth.mfa_verify"))

            # 2FA yok — normal session kurulumu
            _finalize_login(res.user.id, res.user.email, res.session.access_token, getattr(res.session, "refresh_token", None))
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


@bp.route("/2fa/verify", methods=["GET", "POST"])
def mfa_verify():
    """Login sırasında 2FA (TOTP) kodu doğrulaması.

    GET: Kod girme formunu göster.
    POST: Kodu doğrula, başarılıysa tam session kurulup feed'e yönlendir.
    """
    # MFA beklemede mi?
    if "mfa_pending_user_id" not in session:
        flash("2FA süreci başlatılmamış.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()

        if not code or len(code) != 6 or not code.isdigit():
            flash("Lütfen 6 haneli kodu gir.", "error")
            return redirect(url_for("auth.mfa_verify"))

        try:
            # Geçici client: MFA doğrulama için
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(
                session["mfa_pending_access_token"],
                session["mfa_pending_refresh_token"],
            )

            # Aktif TOTP factor'ü bul
            factors = tmp.auth.mfa.list_factors()
            totp_factor = None
            for f in (factors.totp or []):
                if f.status == "verified":
                    totp_factor = f
                    break

            if not totp_factor:
                flash("Aktif 2FA bulunamadı.", "error")
                session.pop("mfa_pending_user_id", None)
                session.pop("mfa_pending_email", None)
                session.pop("mfa_pending_access_token", None)
                session.pop("mfa_pending_refresh_token", None)
                return redirect(url_for("auth.login"))

            # TOTP kodunu doğrula (login sırasında challenge+verify birlikte)
            tmp.auth.mfa.challenge_and_verify({
                "factor_id": totp_factor.id,
                "code": code,
            })

            # Başarılı — geçici state'ten veriyi al
            user_id = session.pop("mfa_pending_user_id")
            email = session.pop("mfa_pending_email")
            access_token = session.pop("mfa_pending_access_token")
            refresh_token = session.pop("mfa_pending_refresh_token")

            # Ban kontrolü (double check)
            if _check_banned(user_id):
                flash("Hesabın askıya alınmış. Bir yöneticiyle iletişime geç.", "error")
                return redirect(url_for("auth.login"))

            # Tam session kurulumu (profile verisi dahil)
            _finalize_login(user_id, email, access_token, refresh_token)

            flash("2FA doğrulandı, hoş geldin!", "success")
            return redirect(url_for("routes.feed"))

        except Exception as e:
            msg = str(e)
            if "Invalid verification code" in msg or "invalid_code" in msg:
                flash("Geçersiz 2FA kodu.", "error")
            else:
                flash(f"2FA hatası: {msg}", "error")
            return redirect(url_for("auth.mfa_verify"))

    return render_template("auth/mfa_verify.html", me=session.get("user"))


@bp.route("/2fa/enroll", methods=["GET", "POST"])
def mfa_enroll():
    """2FA (TOTP) kaydı: QR kodu göster ve doğrula.

    GET: QR kodu + secret'ı göster.
    POST: Doğrulama kodunu al, kurulumu tamamla.
    """
    # Kullanıcı oturum açmış mı?
    if "user" not in session:
        flash("Giriş yap.", "error")
        return redirect(url_for("auth.login"))

    user_id = session["user"]["id"]

    # GET: QR kod + secret göster
    if request.method == "GET":
        # Zaten kurulu mu kontrol et (geçici client ile list_factors)
        try:
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(session["access_token"], session["refresh_token"])
            factors = tmp.auth.mfa.list_factors()

            if any(f.factor_type == "totp" and f.status == "verified" for f in (factors.totp or [])):
                flash("2FA zaten etkinleştirilmiş. Devre dışı bırak ve tekrar denemeyi dene.", "warning")
                return redirect(url_for("routes.profile_edit"))
        except Exception:
            pass  # MFA kontrolü başarısız — devam et

        # Geçici client ile enrollment başlat
        try:
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(session["access_token"], session["refresh_token"])

            # TOTP enrollment: QR kod + secret dön
            enrollment = tmp.auth.mfa.enroll({"factor_type": "totp"})

            # Session'a geçici olarak kaydet (POST'ta kullanacağız)
            session["mfa_enrollment_id"] = enrollment.id
            session["mfa_enrollment_secret"] = getattr(enrollment.totp, "secret", "")

            return render_template(
                "auth/mfa_enroll.html",
                me=session.get("user"),
                qr_code=getattr(enrollment.totp, "qr_code", ""),
                secret=session["mfa_enrollment_secret"],
            )
        except Exception as e:
            flash(f"2FA kurulum hatası: {e}", "error")
            return redirect(url_for("routes.profile_edit"))

    # POST: Verification kodunu doğrula
    if request.method == "POST":
        code = request.form.get("code", "").strip()

        if not code or len(code) != 6 or not code.isdigit():
            flash("Lütfen 6 haneli kodu gir.", "error")
            return redirect(url_for("auth.mfa_enroll"))

        enrollment_id = session.pop("mfa_enrollment_id", None)
        if not enrollment_id:
            flash("Geçersiz 2FA kurulum oturumu.", "error")
            return redirect(url_for("routes.profile_edit"))

        try:
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(session["access_token"], session["refresh_token"])

            # TOTP kodunu doğrula: önce challenge oluştur, sonra verify et
            challenge_resp = tmp.auth.mfa.challenge({"factor_id": enrollment_id})
            tmp.auth.mfa.verify({
                "factor_id": enrollment_id,
                "challenge_id": challenge_resp.id,
                "code": code,
            })

            session.pop("mfa_enrollment_secret", None)
            flash("2FA başarıyla etkinleştirildi!", "success")
            return redirect(url_for("routes.profile_edit"))

        except Exception as e:
            msg = str(e)
            if "Invalid verification code" in msg or "invalid_code" in msg:
                flash("Geçersiz doğrulama kodu. Tekrar dene.", "error")
            else:
                flash(f"2FA doğrulama hatası: {e}", "error")
            return redirect(url_for("auth.mfa_enroll"))

    return render_template("auth/mfa_enroll.html", me=session.get("user"))


@bp.route("/2fa/disable", methods=["POST"])
def mfa_disable():
    """2FA (TOTP) devre dışı bırakma.

    CSRF korumalı POST isteği — geçerli oturum gerekli.
    """
    if "user" not in session:
        flash("Giriş yap.", "error")
        return redirect(url_for("auth.login"))

    user_id = session["user"]["id"]

    try:
        # Kurulu TOTP factor'ü bul ve kaldır
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        tmp.auth.set_session(session["access_token"], session["refresh_token"])

        factors = tmp.auth.mfa.list_factors()
        totp_factor = None
        for f in (factors.totp or []):
            if f.status == "verified":
                totp_factor = f
                break

        if not totp_factor:
            flash("Etkin 2FA bulunamadı.", "warning")
            return redirect(url_for("routes.profile_edit"))

        # TOTP factor'ü unenroll et
        tmp.auth.mfa.unenroll({"factor_id": totp_factor.id})

        flash("2FA devre dışı bırakıldı.", "success")
        return redirect(url_for("routes.profile_edit"))

    except Exception as e:
        flash(f"2FA devre dışı bırakma hatası: {e}", "error")
        return redirect(url_for("routes.profile_edit"))


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


@bp.route("/auth/sync-tokens", methods=["POST"])
def sync_tokens():
    """Tarayıcıdaki supabase-js token'ı YENİLEDİĞİNDE (rotasyon) yeni çifti
    Flask session'ına yazar.

    Bu olmadan: refresh token TEK KULLANIMLIK — tarayıcı ilk yenilemede
    session'daki çifti tüketiyor, ama Flask session'da ESKİ çift kalıyordu.
    Sonraki her sayfa yüklemesi Realtime'ı geçersiz token'la kuruyor ve
    postgres_changes olayları RLS'e takılıp SESSİZCE gelmiyordu (kullanıcı
    raporu: "mesajlar bana realtime düşmüyor, F5 gerekiyor; yeni giriş yapan
    arkadaşlarımda çalışıyor").
    """
    from flask import jsonify
    if "user" not in session:
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    access = (data.get("access_token") or "").strip()
    refresh = (data.get("refresh_token") or "").strip()
    if not access or not refresh:
        return jsonify(error="missing_tokens"), 400
    session["access_token"] = access
    session["refresh_token"] = refresh
    return jsonify(ok=True)


def _access_token_exp(token: str) -> float:
    """JWT'nin exp claim'ini İMZA DOĞRULAMADAN okur (sadece 'yenileme zamanı
    geldi mi' kararı için — yetki kontrolü değil)."""
    import base64
    import json as _json
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(_json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def refresh_session_tokens(force: bool = False) -> str | None:
    """Session'daki Supabase access token'ının süresi yaklaştıysa SUNUCU
    TARAFINDA yeniler ve yeni çifti session'a yazar.

    Token yenileme TEK yazarlıdır (yalnızca burası) — önceden tarayıcıdaki
    supabase-js de yeniliyordu ve tek kullanımlık refresh token iki taraf
    arasında tüketilip 400 (Bad Request) üretiyordu; Realtime kimliksiz
    kalıp mesaj olayları sessizce kesiliyordu (kullanıcı konsol raporu).
    """
    import requests as _rq
    access = session.get("access_token")
    refresh = session.get("refresh_token")
    if not access or not refresh:
        return None
    if not force and _access_token_exp(access) - time.time() > 300:
        return access  # hâlâ 5+ dk geçerli
    try:
        r = _rq.post(
            current_app.config["SUPABASE_URL"] + "/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                     "Content-Type": "application/json"},
            json={"refresh_token": refresh},
            timeout=6,
        )
        if r.status_code == 200:
            d = r.json()
            if d.get("access_token"):
                session["access_token"] = d["access_token"]
                if d.get("refresh_token"):
                    session["refresh_token"] = d["refresh_token"]
                return session["access_token"]
        if r.status_code in (400, 401, 403):
            # Refresh token KESİN reddedildi (süresi dolmuş / iptal / başka
            # projeden kalma — Tokyo→Frankfurt taşımasında eski oturumlar
            # realtime'ı CHANNEL_ERROR ile SESSİZCE bozuk bırakmıştı).
            # Yalnızca jetonlar düşürülür (session["user"] KALIR — render
            # yolları refresh'ten sonra session["user"] okuyor, kırılmasın);
            # /auth/realtime-token bunu görüp istemciye relogin işareti
            # döner, istemci /logout'a gider → temiz giriş ekranı. Ağ/5xx
            # gibi GEÇİCİ hatalar bu yola girmez (except'e düşer).
            session.pop("access_token", None)
            session.pop("refresh_token", None)
            return None
    except Exception:
        pass
    return access  # yenilenemedi (geçici hata) — eski token'la devam


@bp.route("/auth/realtime-token")
def realtime_token():
    """Uzun süre açık kalan sekmeler için taze access token (chat.js/init
    periyodik çağırır, realtime.setAuth ile uygular)."""
    from flask import jsonify
    if "user" not in session:
        return jsonify(error="unauthorized"), 401
    token = refresh_session_tokens()
    if not token and "access_token" not in session:
        # Jetonlar kesin ret sonrası düşürüldü — oturum canlı özellikler
        # için ölü; istemci bu işaretle /logout'a gidip temiz giriş ister
        return jsonify(access_token="", relogin=True)
    return jsonify(access_token=token or "")
