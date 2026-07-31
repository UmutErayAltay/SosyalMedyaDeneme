from flask import request, jsonify, current_app
from supabase import create_client

from . import bp
from ._common import _str_field, api_login_required
from ..supabase_client import get_sb, call_with_ssl_retry
from ..rate_limit import is_rate_limited


# ----------------------- 2FA (TOTP) -----------------------
#
# MİMARİ FARK (auth.py'deki web mfa_enroll()/mfa_disable()'dan): web, Flask
# session'da CANLI bir Supabase Auth access_token/refresh_token tutar ve
# mfa_enroll()/mfa_disable() bu STOK session'ı `set_session()` ile yeniden
# kullanır. Native'in request'ler arasında böyle bir session'ı YOK (login()
# sadece user.id/email almak için sign_in_with_password'ü GEÇİCİ kullanır,
# sonucu hiç saklamaz — bkz. modül docstring'i). Bu yüzden aşağıdaki HER
# endpoint, gereken Supabase Auth session'ı KENDİ İÇİNDE taze bir
# sign_in_with_password ile kurar ve bu session'ı response'a KAYDETMEDEN
# sadece o isteğin ömrü boyunca kullanır (bkz. _api_fresh_mfa_client).
#
# BİLİNÇLİ SAPMA: web'in "Google-only hesapta şifre adımı atlanır" davranışı
# (_user_has_password_identity kontrolü) BURADA UYGULANMAZ — native login()
# SADECE email+password ile çalışır (Google OAuth native'de yok), yani bir
# native api_token'ı olan HER kullanıcının zaten bir şifre identity'si var.
# Bu yüzden native 2FA endpoint'lerinde şifre HER ZAMAN zorunludur.


def _api_fresh_mfa_client(email: str, password: str):
    """Şifreyi taze `sign_in_with_password` ile doğrulayıp session'ı set
    edilmiş YENİ bir temp client döndürür (başarısızsa None).

    Sign-in için kullanılan client'ı DOĞRUDAN reuse etmek yerine set_session
    için AYRI bir client kurulur (login()'deki tek-client desenden bilinçli
    bir sapma) — bu fonksiyon üç farklı endpoint'ten çağrıldığı için taze bir
    client örneği her seferinde daha güvenli/öngörülebilir.
    """
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
        return None

    sess = getattr(res, "session", None)
    if not sess or not getattr(sess, "access_token", None):
        return None

    tmp = create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_PUBLISHABLE_KEY"],
    )
    tmp.auth.set_session(sess.access_token, getattr(sess, "refresh_token", None))
    return tmp


@bp.route("/2fa/status")
@api_login_required
def api_2fa_status():
    """2FA (TOTP) durumu — şifre GEREKMEZ (read-only).

    Tasarım kararı: `sb.auth.admin.get_user_by_id()` (service-role) döndürdüğü
    User nesnesindeki `factors` alanı CANLI durumu yansıdığı gerçek Supabase'e
    karşı DOĞRULANDI (enroll sonrası "unverified", verify sonrası anında
    "verified" olarak görünüyor) — bu yüzden taze bir Auth session kurmaya
    (sign_in_with_password) hiç gerek yok, admin API tek başına yeterli.
    """
    sb = get_sb()
    me = request.api_user["id"]
    enabled = False
    try:
        user_res = sb.auth.admin.get_user_by_id(me)
        user = getattr(user_res, "user", None)
        factors = getattr(user, "factors", None) or []
        enabled = any(
            f.factor_type == "totp" and f.status == "verified" for f in factors
        )
    except Exception:
        enabled = False
    return jsonify(enabled=enabled)


@bp.route("/2fa/enroll", methods=["POST"])
@api_login_required
def api_2fa_enroll():
    """2FA (TOTP) kurulumunu BAŞLAT — auth.py mfa_enroll() GET+QR adımının
    native karşılığı.

    Native'de saklanacak bir Flask session yok (bkz. modül başındaki bölüm
    docstring'i) — bu yüzden web'in aksine factor_id/secret/qr_code DOĞRUDAN
    client'a döner; client bunları bir sonraki /2fa/enroll/verify çağrısına
    kadar kendi hafızasında tutar.
    """
    me = request.api_user["id"]
    email = request.api_user.get("email")

    # Rate limit — web ile PAYLAŞILAN aynı anahtar (aynı kullanıcı web+native'den
    # art arda denerse ikisi birlikte sayılır, login()'deki paylaşılan-limit
    # gerekçesiyle tutarlı).
    if is_rate_limited(f"2fa_enroll:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    if not password:
        return jsonify(error="password_required"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    # Zaten etkin mi kontrolü — auth.py GET akışındaki AYNI fail-open davranış:
    # bu kontrol başarısız olursa (örn. geçici ağ hatası) enroll'a normal
    # devam edilir, istek engellenmez.
    try:
        factors = tmp.auth.mfa.list_factors()
        if any(f.factor_type == "totp" and f.status == "verified" for f in (factors.totp or [])):
            return jsonify(error="already_enabled"), 409
    except Exception:
        pass

    try:
        enrollment = tmp.auth.mfa.enroll({"factor_type": "totp", "issuer": "Sosyal Medya"})
    except Exception:
        return jsonify(error="enroll_failed"), 500

    return jsonify(
        factor_id=enrollment.id,
        secret=getattr(enrollment.totp, "secret", ""),
        qr_code=getattr(enrollment.totp, "qr_code", ""),
    )


@bp.route("/2fa/enroll/verify", methods=["POST"])
@api_login_required
def api_2fa_enroll_verify():
    """2FA (TOTP) kurulumunu TAMAMLA — kimlik doğrulayıcı uygulamadan alınan
    6 haneli kodu doğrular ve factor'ü "verified" durumuna geçirir."""
    me = request.api_user["id"]
    email = request.api_user.get("email")

    # AYNI `2fa_enroll:{me}` anahtarı — enroll başlangıç+bitiş aynı 5/300
    # bütçesinden düşer, ayrı bir sayaç AÇILMAZ.
    if is_rate_limited(f"2fa_enroll:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    factor_id = _str_field(data, "factor_id")
    code = _str_field(data, "code")

    # Format kontrolü — auth.py mfa_enroll()'daki AYNI kontrol
    # (len(code)==6 and code.isdigit()), sign-in denemeden ÖNCE yapılır.
    if not code or len(code) != 6 or not code.isdigit():
        return jsonify(error="invalid_code_format"), 400
    if not password:
        return jsonify(error="password_required"), 400
    if not factor_id:
        return jsonify(error="missing_factor_id"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    try:
        challenge_resp = tmp.auth.mfa.challenge({"factor_id": factor_id})
        tmp.auth.mfa.verify({
            "factor_id": factor_id,
            "challenge_id": challenge_resp.id,
            "code": code,
        })
    except Exception as e:
        msg = str(e)
        if "Invalid" in msg:
            return jsonify(error="invalid_code"), 401
        return jsonify(error="verify_failed"), 500

    return jsonify(ok=True)


@bp.route("/2fa/disable", methods=["POST"])
@api_login_required
def api_2fa_disable():
    """2FA (TOTP) devre dışı bırak — auth.py mfa_disable()'ın native karşılığı,
    ANCAK ekstra bir `code` adımıyla (bkz. aşağıdaki DİKKAT notu — spesifikasyondan
    BİLİNÇLİ bir sapma, gerçek Supabase'e karşı test edilerek bulundu).

    DİKKAT (spesifikasyondan sapma — gerçek Supabase'e karşı test sırasında
    keşfedildi): kurulu `supabase-auth` kütüphanesi `mfa.unenroll()`'ı şöyle
    belgeliyor: "Unenrolling a verified MFA factor cannot be done from a
    session with an aal1 authenticator level." Sadece şifreyle (sign_in_with_
    password) elde edilen bir session AAL1'dir — gerçek bir hesaba karşı
    denendi, `AAL2 required to unenroll verified factor` hatasıyla KESİN
    olarak doğrulandı. Bu yüzden password-only tasarım aktif bir 2FA'yı ASLA
    kapatamaz (sürekli 500/disable_failed döner). Çözüm: verified factor
    bulunduktan sonra, kullanıcıdan bir TOTP `code` da istenir; bu kodla
    challenge+verify yapılıp session AAL2'ye yükseltilir (login()'deki AYNI
    challenge/verify deseni, ama zaten verified bir factor üzerinde) — ANCAK
    o zaman unenroll çağrılabilir. auth.py'deki web mfa_disable() da AYNI
    kodu (sadece şifre) kullanıyor — muhtemelen AYNI bug orada da var, bu
    web tarafı için AYRI bir security/backend task'ı gerektirir (bu görevin
    kapsamı dışında, app/auth.py'ye dokunulmadı).

    Google-only kısayolu YOK (bkz. bölüm başındaki BİLİNÇLİ SAPMA notu) —
    şifre her zaman zorunlu.
    """
    me = request.api_user["id"]
    email = request.api_user.get("email")

    if is_rate_limited(f"2fa_disable:{me}", 5, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")
    code = _str_field(data, "code")
    if not password:
        return jsonify(error="password_required"), 400

    tmp = _api_fresh_mfa_client(email, password)
    if tmp is None:
        return jsonify(error="invalid_password"), 401

    try:
        factors = tmp.auth.mfa.list_factors()
    except Exception:
        return jsonify(error="disable_failed"), 500

    totp_factor = None
    for f in (factors.totp or []):
        if f.status == "verified":
            totp_factor = f
            break
    if not totp_factor:
        return jsonify(error="no_active_2fa"), 404

    # AAL2'ye yükseltme adımı — yukarıdaki DİKKAT notu: unenroll'dan ÖNCE
    # zorunlu. `code` yoksa client'a bunu AÇIKÇA bildir (login()'deki
    # mfa_required/tekrar-dene deseniyle tutarlı — client aynı isteği `code`
    # ekleyerek tekrar atar).
    if not code:
        return jsonify(error="code_required"), 400
    if len(code) != 6 or not code.isdigit():
        return jsonify(error="invalid_code_format"), 400

    try:
        challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
        tmp.auth.mfa.verify({
            "factor_id": totp_factor.id,
            "challenge_id": challenge_resp.id,
            "code": code,
        })
    except Exception as e:
        msg = str(e)
        if "Invalid" in msg:
            return jsonify(error="invalid_code"), 401
        return jsonify(error="verify_failed"), 500

    try:
        tmp.auth.mfa.unenroll({"factor_id": totp_factor.id})
    except Exception:
        return jsonify(error="disable_failed"), 500

    return jsonify(ok=True)
