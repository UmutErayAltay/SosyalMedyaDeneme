"""Kimlik doğrulama: kayıt / giriş / çıkış.

Supabase Auth kullanır. Kayıt akışı:
1) admin.create_user ile auth.users'a kayıt (email_confirm=True -> onay bypass)
2) profiles tablosunu garantiye al (upsert)
3) sign_in ile session al -> otomatik giriş

Arkadaşlar arası test için email confirmation BYPASS ediliyor.
"""
import re
import secrets as _secrets
import time
from urllib.parse import quote
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, current_app, jsonify)
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


def _check_banned(user_id: str, access_token: str = None, refresh_token: str = None) -> bool:
    """Kullanıcı hesabı askıya alınmış mı kontrol et.

    True döndürürse hesap banned, bu SPESİFİK oturum (access_token/refresh_token
    verildiyse) tek kullanımlık bir client ile kapatılır. Paylaşılan get_auth()
    singleton'ı KULLANILMAZ — o client'ın iç oturum durumu, süreç genelinde AYNI
    ANDA başka bir cihazın login/logout işlemiyle üzerine yazılabilir (bkz.
    login()/register() içindeki tek-kullanımlık client deseni, ve bu dosyanın
    reset_password()'daki aynı riske dair notu).
    """
    try:
        prof = get_sb().table("profiles").select(
            "is_banned"
        ).eq("id", user_id).execute()
        prof_data = prof.data[0] if prof.data else {}
        if prof_data.get("is_banned"):
            if access_token:
                try:
                    tmp = create_client(
                        current_app.config["SUPABASE_URL"],
                        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                    )
                    tmp.auth.set_session(access_token, refresh_token)
                    tmp.auth.sign_out()
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

    # Yeni oturum kaydı oluştur (aktif oturumlar takibi için)
    from .user_sessions import create_session_record
    session_record_id = create_session_record(get_sb(), user_id)
    if session_record_id:
        session["session_record_id"] = session_record_id

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
        refresh_token = getattr(s, "refresh_token", None)
        # Ban kontrolü — session kurmadan önce
        if _check_banned(user.id, s.access_token, refresh_token):
            return "banned"

        _finalize_login(user.id, user.email, s.access_token, refresh_token)
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
                    "is_private": True,  # yeni hesaplar varsayılan gizli (ürün kararı)
                }, on_conflict="id").execute()
            except Exception:
                pass  # trigger zaten oluşturmuş olabilir

            # 3) Otomatik giriş (sign_in ile geçerli session al) — tek
            # kullanımlık client: paylaşılan get_auth() singleton'ı başka bir
            # cihazın AYNI ANDA login/logout işlemiyle çakışabilir (bkz.
            # _check_banned üzerindeki not).
            try:
                tmp_auth = create_client(
                    current_app.config["SUPABASE_URL"],
                    current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                )
                login_res = call_with_ssl_retry(
                    lambda: tmp_auth.auth.sign_in_with_password({
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
            # Tek kullanımlık client: paylaşılan get_auth() singleton'ı, AYNI
            # ANDA başka bir cihazın login/logout işlemiyle çakışabilir (o
            # client'ın iç oturum durumu süreç genelinde tek/paylaşılan —
            # bkz. reset_password()'daki aynı riske dair not ve _check_banned).
            tmp_auth = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            res = call_with_ssl_retry(
                lambda: tmp_auth.auth.sign_in_with_password({
                    "email": email,
                    "password": password,
                })
            )

            # Ban kontrolü önce
            if _check_banned(res.user.id, res.session.access_token, getattr(res.session, "refresh_token", None)):
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


# --- Google ile giriş (OAuth) ---
# Akış: /auth/google → Supabase'in authorize URL'i → Google onayı → Supabase
# → /auth/google/callback. Tokenlar URL FRAGMENT'ında (#access_token=...)
# döner — fragment sunucuya hiç ulaşmaz, o yüzden callback sayfasındaki
# küçük JS fragment'ı okuyup /auth/google/complete'e POST'lar; sunucu token'ı
# Supabase'e DOĞRULATIP (get_user) session'ı kurar. İstemciden gelen token'a
# asla doğrulamadan güvenilmez.


def _unique_username(sb, email: str) -> str:
    """İlk Google girişinde profiles için benzersiz kullanıcı adı türetir.

    Önce e-posta prefix'i (temizlenmiş), çakışırsa rastgele sonek; hepsi
    dolarsa rastgele hex. Register'daki 3-karakter minimumuna uyar.
    """
    base = (email or "").split("@")[0]
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", base)[:20]
    if len(base) < 3:
        base = "kullanici"
    candidates = [base] + [f"{base}{_secrets.randbelow(9000) + 1000}" for _ in range(4)]
    for cand in candidates:
        try:
            taken = sb.table("profiles").select("id").eq("username", cand).execute().data
            if not taken:
                return cand
        except Exception:
            break
    return f"u_{_secrets.token_hex(4)}"


def _has_verified_totp(access_token: str, refresh_token: str | None) -> bool:
    """Kullanıcının doğrulanmış TOTP factor'u var mı (login()'deki desenle aynı).

    Kontrol başarısızsa False — 2FA yok sayılır, normal giriş devam eder
    (login()'deki mevcut davranışın aynısı).
    """
    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        tmp.auth.set_session(access_token, refresh_token)
        factors = tmp.auth.mfa.list_factors()
        return any(
            f.factor_type == "totp" and f.status == "verified"
            for f in (factors.totp or [])
        )
    except Exception:
        return False


@bp.route("/auth/google")
def google_login():
    """Kullanıcıyı Supabase'in Google authorize URL'ine yönlendirir."""
    redirect_to = url_for("auth.google_callback", _external=True)
    authorize_url = (
        current_app.config["SUPABASE_URL"]
        + "/auth/v1/authorize?provider=google&redirect_to="
        + quote(redirect_to, safe="")
    )
    return redirect(authorize_url)


@bp.route("/auth/google/callback")
def google_callback():
    """Tokenları fragment'tan okuyup complete'e POST'layan köprü sayfası."""
    return render_template("auth/google_callback.html")


@bp.route("/auth/google/complete", methods=["POST"])
def google_complete():
    """Google dönüşünde istemcinin gönderdiği token'ı doğrulayıp session kurar."""
    # Şifreli girişle aynı IP bazlı sınır — token deneme taşkınını yavaşlatır
    if is_rate_limited(f"login:{request.remote_addr or 'unknown'}", 10, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    access_token = (data.get("access_token") or "").strip()
    refresh_token = (data.get("refresh_token") or "").strip() or None
    if not access_token:
        return jsonify(error="missing_token"), 400

    try:
        user_res = call_with_ssl_retry(lambda: get_auth().auth.get_user(access_token))
        user = getattr(user_res, "user", None)
    except Exception:
        user = None
    if not user:
        return jsonify(error="invalid_token"), 401

    if _check_banned(user.id, access_token, refresh_token):
        return jsonify(error="banned"), 403

    # İlk Google girişi: profiles satırı yoksa oluştur (register'daki
    # "trigger'a güvenme, kendin garantiye al" deseninin aynısı)
    sb = get_sb()
    try:
        prof = sb.table("profiles").select("id").eq("id", user.id).execute().data
    except Exception:
        prof = [{"id": user.id}]  # kontrol edilemedi — insert deneyip çakışırsa upsert zaten no-op
    if not prof:
        meta = getattr(user, "user_metadata", None) or {}
        sb.table("profiles").upsert({
            "id": user.id,
            "username": _unique_username(sb, user.email),
            "email": user.email,
            "full_name": meta.get("full_name") or meta.get("name"),
            "avatar_url": meta.get("avatar_url") or meta.get("picture"),
            "is_private": True,  # yeni hesaplar varsayılan gizli (ürün kararı) — sadece ilk Google girişi, var olanları etkilemez
        }, on_conflict="id").execute()
        from .cache import invalidate
        invalidate("valid_usernames")

    # 2FA paritesi: TOTP kurmuş kullanıcı Google ile girse de kod sorulur —
    # aksi hâlde Google girişi 2FA'yı tamamen bypass ederdi
    if _has_verified_totp(access_token, refresh_token):
        session["mfa_pending_user_id"] = user.id
        session["mfa_pending_email"] = user.email
        session["mfa_pending_access_token"] = access_token
        session["mfa_pending_refresh_token"] = refresh_token
        return jsonify(ok=True, redirect=url_for("auth.mfa_verify"))

    _finalize_login(user.id, user.email, access_token, refresh_token)
    return jsonify(ok=True, redirect=url_for("routes.feed"))


@bp.route("/logout")
def logout():
    # Oturum kaydını sil (varsa)
    session_record_id = session.get("session_record_id")
    if session_record_id:
        from .user_sessions import delete_session_record
        delete_session_record(session_record_id)

    # Tek kullanımlık client, BU cihazın kendi token'larıyla scope'lanır —
    # paylaşılan get_auth() singleton'ı ile sign_out() çağırmak, o an
    # başka bir cihazın login'iyle üzerine yazılmış YANLIŞ oturumu kapatabilir
    # (bkz. _check_banned/login() üzerindeki aynı riske dair not).
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    if access_token:
        try:
            tmp = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            tmp.auth.set_session(access_token, refresh_token)
            tmp.auth.sign_out()
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
        # 2FA brute force denemelerini yavaşlatır (IP başına)
        if is_rate_limited(f"2fa_verify:{request.remote_addr or 'unknown'}", 8, 300):
            flash("Çok fazla 2FA denemesi yaptın. Lütfen biraz sonra tekrar dene.", "error")
            return redirect(url_for("auth.mfa_verify"))

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
            if _check_banned(user_id, access_token, refresh_token):
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


def _user_has_password_identity(access_token: str) -> bool:
    """Kullanıcının Supabase Auth'ta bir 'email' (şifre) identity'si var mı.

    Sadece Google ile kayıt olmuş hesaplarda (google_complete()) hiçbir zaman
    şifre belirlenmez — bu kullanıcılar için sign_in_with_password HER ZAMAN
    "Invalid login credentials" ile başarısız olur. 2FA enroll/disable
    reverifikasyonu bu yüzden koşullu: şifresi olmayan hesaplarda atlanır
    (aksi halde bu kullanıcı segmenti 2FA'yı hiç açıp kapatamaz — kalıcı
    kilitlenme, bkz. code-reviewer bulgusu). Belirsiz durumda (get_user
    başarısız) fail-closed davranılır: şifre isteniyor kabul edilir.
    """
    try:
        user_res = call_with_ssl_retry(lambda: get_auth().auth.get_user(access_token))
        user = getattr(user_res, "user", None)
        identities = getattr(user, "identities", None) or []
        return any(getattr(i, "provider", None) == "email" for i in identities)
    except Exception:
        return True


@bp.route("/2fa/enroll", methods=["GET", "POST"])
def mfa_enroll():
    """2FA (TOTP) kaydı: şifre doğrulama → QR kodu göster → doğrulama.

    GET: Şifre doğrulanmadıysa form, doğrulanmışsa QR kodu göster.
    POST: Şifre doğrula (flag set) veya TOTP kodu doğrula (kurulum tamamla).
    """
    # Kullanıcı oturum açmış mı?
    if "user" not in session:
        flash("Giriş yap.", "error")
        return redirect(url_for("auth.login"))

    user_id = session["user"]["id"]
    email = session["user"]["email"]

    # GET: Şifre doğrulanmadıysa form göster, doğrulanmışsa QR göster
    if request.method == "GET":
        # Zaten kurulu mu kontrol et
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

        # Google-only hesaplarda şifre YOK (sign_in_with_password her zaman
        # başarısız olur) — bu kullanıcılar için şifre adımı atlanır, önceki
        # (reverifikasyonsuz) davranış korunur, aksi halde bu segment 2FA'yı
        # hiç açamazdı (bkz. _user_has_password_identity docstring'i).
        no_password_account = not _user_has_password_identity(session["access_token"])

        # Şifre doğrulanmış mı? Flag'e zaman damgası eklendi (satır ~606) —
        # süresiz kalırsa yarıda kalan bir akışta (örn. şifre doğrulandı ama
        # sekme kapandı) paylaşılan/çalınmış bir cihazda session çerezi
        # günlerce geçerli kaldığı sürece QR'a şifresiz erişim açık kalırdı.
        # 120 saniyelik dar bir pencereyle sınırlanıyor.
        verified_at = session.get("mfa_verified_for_enroll")
        if no_password_account or (verified_at and time.time() - verified_at < 120):
            # Flag tüket (tek kullanımlık)
            session.pop("mfa_verified_for_enroll", None)

            # QR/secret üret
            try:
                tmp = create_client(
                    current_app.config["SUPABASE_URL"],
                    current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                )
                tmp.auth.set_session(session["access_token"], session["refresh_token"])
                enrollment = tmp.auth.mfa.enroll({"factor_type": "totp", "issuer": "Sosyal Medya"})

                session["mfa_enrollment_id"] = enrollment.id
                session["mfa_enrollment_secret"] = getattr(enrollment.totp, "secret", "")

                return render_template(
                    "auth/mfa_enroll.html",
                    me=session.get("user"),
                    qr_code=getattr(enrollment.totp, "qr_code", ""),
                    secret=session["mfa_enrollment_secret"],
                    show_qr=True,
                )
            except Exception as e:
                flash(f"2FA kurulum hatası: {e}", "error")
                return redirect(url_for("routes.profile_edit"))
        else:
            # Şifre formu göster
            return render_template(
                "auth/mfa_enroll.html",
                me=session.get("user"),
                show_qr=False,
            )

    # POST: Şifre veya kod doğrula
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        code = request.form.get("code", "").strip()

        # Şifre POST'u (2FA'nın başında)
        if password and not code:
            if not password:
                flash("2FA'yı etkinleştirmek için şifreni gir.", "error")
                return redirect(url_for("auth.mfa_enroll"))

            # Rate limit — 5 deneme / 300 saniye
            if is_rate_limited(f"2fa_enroll:{user_id}", 5, 300):
                flash("Çok fazla 2FA kurulum denemesi yaptın. Lütfen biraz sonra tekrar dene.", "error")
                return redirect(url_for("auth.mfa_enroll"))

            try:
                # Şifreyi doğrula
                tmp_auth = create_client(
                    current_app.config["SUPABASE_URL"],
                    current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                )
                call_with_ssl_retry(
                    lambda: tmp_auth.auth.sign_in_with_password({
                        "email": email,
                        "password": password,
                    })
                )

                # Şifre doğru — flag'i zaman damgasıyla set et (TTL kontrolü
                # için, bkz. GET akışındaki 120sn penceresi) ve QR sayfasına yönlendir
                session["mfa_verified_for_enroll"] = time.time()
                return redirect(url_for("auth.mfa_enroll"))

            except Exception as e:
                msg = str(e)
                if "Invalid login credentials" in msg:
                    flash("Şifre yanlış.", "error")
                else:
                    flash(f"Şifre doğrulama hatası: {msg}", "error")
                return redirect(url_for("auth.mfa_enroll"))

        # Kod POST'u (2FA kurulumunun tamamlanması)
        elif code and not password:
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

                # TOTP kodunu doğrula
                challenge_resp = tmp.auth.mfa.challenge({"factor_id": enrollment_id})
                tmp.auth.mfa.verify({
                    "factor_id": enrollment_id,
                    "challenge_id": challenge_resp.id,
                    "code": code,
                })

                session.pop("mfa_enrollment_secret", None)
                session.pop("mfa_verified_for_enroll", None)  # Güvenlik: flag'i temizle
                flash("2FA başarıyla etkinleştirildi!", "success")
                return redirect(url_for("routes.profile_edit"))

            except Exception as e:
                msg = str(e)
                if "Invalid verification code" in msg or "invalid_code" in msg:
                    flash("Geçersiz doğrulama kodu. Tekrar dene.", "error")
                else:
                    flash(f"2FA doğrulama hatası: {e}", "error")
                return redirect(url_for("auth.mfa_enroll"))
        else:
            flash("Geçersiz istek.", "error")
            return redirect(url_for("auth.mfa_enroll"))

    return render_template("auth/mfa_enroll.html", me=session.get("user"), show_qr=False)


@bp.route("/2fa/disable", methods=["POST"])
def mfa_disable():
    """2FA (TOTP) devre dışı bırakma — şifresi olan hesaplarda şifre doğrulaması ile korunur.

    CSRF korumalı POST isteği — geçerli oturum gerekli. Google-only hesaplarda
    (hiç şifre yok) reverifikasyon adımı atlanır, bkz. _user_has_password_identity.
    """
    if "user" not in session:
        flash("Giriş yap.", "error")
        return redirect(url_for("auth.login"))

    user_id = session["user"]["id"]
    email = session["user"]["email"]
    password = request.form.get("password", "").strip()

    if _user_has_password_identity(session["access_token"]):
        # Şifre alanı boş mu kontrol et
        if not password:
            flash("2FA'yı kapatmak için şifreni gir.", "error")
            return redirect(url_for("routes.profile_edit"))

        # Kullanıcı bazlı rate limit — 5 deneme / 300 saniye
        if is_rate_limited(f"2fa_disable:{user_id}", 5, 300):
            flash("Çok fazla 2FA kapatma denemesi yaptın. Lütfen biraz sonra tekrar dene.", "error")
            return redirect(url_for("routes.profile_edit"))

        try:
            # Şifreyi doğrula — başarısız olursa exception fırlar
            tmp_auth = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            call_with_ssl_retry(
                lambda: tmp_auth.auth.sign_in_with_password({
                    "email": email,
                    "password": password,
                })
            )
        except Exception as e:
            msg = str(e)
            if "Invalid login credentials" in msg:
                flash("Şifre yanlış.", "error")
            else:
                flash(f"2FA devre dışı bırakma hatası: {msg}", "error")
            return redirect(url_for("routes.profile_edit"))

    try:
        # Şifre doğru (veya hesap zaten şifresiz) — TOTP factor'ü bul ve kaldır
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
