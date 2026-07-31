"""/auth/* + /realtime-token — kayıt, giriş (2FA dahil), Google girişi, çıkış,
/auth/me, Realtime için taze Supabase Auth access token'ı.
"""
import secrets
from datetime import datetime, timezone

from flask import request, jsonify, current_app
from supabase import create_client

from . import bp
from ._common import _str_field, _hash_token, api_login_required
from ..supabase_client import get_sb, call_with_ssl_retry
from ..rate_limit import is_rate_limited
from ..cache import invalidate
from ..auth import _unique_username
from ..realtime_session import _store_realtime_session, _decrypt_token


@bp.route("/auth/register", methods=["POST"])
def api_register():
    """Yeni hesap oluştur — auth.py register()'ın AYNI çekirdek mantığı
    (admin.create_user + profiles upsert), ama register()'dan farklı olarak:
    1) SESSİZCE yutulan username-çakışması yerine önden bir uniqueness
       kontrolü yapılır (api_profile_edit()'teki AYNI desen — register()'ın
       "trigger zaten oluşturmuş olabilir" try/except'i, username zaten
       alınmışsa upsert'in username constraint'ine çarpıp sessizce
       yutulmasına açık; native'de bunu ÖNDEN engellemek daha doğru).
    2) Otomatik giriş için AYRI bir sign_in_with_password YAPILMAZ — admin.
       create_user() zaten user.id döndürüyor, native token'ı DOĞRUDAN bu
       id ile üretilir (login()'in aksine burada bir Supabase Auth session'ı
       hiç gerekmiyor, api_tokens tablosu sadece user_id ister).
    """
    if is_rate_limited(f"register:{request.remote_addr or 'unknown'}", 5, 600):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    email = _str_field(data, "email")
    password = _str_field(data, "password")
    username = _str_field(data, "username")
    device_name = _str_field(data, "device_name") or None

    if not email or not password or not username:
        return jsonify(error="missing_fields"), 400
    if len(username) < 3:
        return jsonify(error="short_username"), 400

    sb = get_sb()
    try:
        taken = sb.table("profiles").select("id").eq("username", username).execute()
        if taken.data:
            return jsonify(error="username_taken"), 400
    except Exception:
        pass

    try:
        res = sb.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"username": username},
        })
        user = getattr(res, "user", None)
    except Exception as e:
        msg = str(e)
        if "already" in msg.lower() or "registered" in msg.lower():
            return jsonify(error="email_taken"), 400
        return jsonify(error="register_failed"), 500

    if not user:
        return jsonify(error="register_failed"), 500

    try:
        sb.table("profiles").upsert({
            "id": user.id,
            "username": username,
            "email": email,
            "is_private": True,
        }, on_conflict="id").execute()
    except Exception:
        return jsonify(error="register_failed"), 500

    invalidate("valid_usernames")

    # Realtime için gerçek bir Supabase Auth session gerekiyor — normalde bu
    # akışta HİÇ yok (admin.create_user() zaten user.id döndürüyor, token
    # bununla üretiliyor, bkz. fonksiyon docstring'i). auth.py register()'daki
    # (web) "otomatik giriş" adımının AYNISI burada Realtime AMAÇLI eklendi.
    # BAŞARISIZ olursa (ör. az önce açılan hesaba karşı geçici ağ hatası) kayıt
    # akışının GERİ KALANI (token issuance) YİNE DE devam eder — sess=None
    # kalır, _store_realtime_session bunu no-op sayar (register() 500 dönmez).
    sess = None
    try:
        tmp_auth = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        auth_res = call_with_ssl_retry(
            lambda: tmp_auth.auth.sign_in_with_password({
                "email": email, "password": password,
            })
        )
        sess = getattr(auth_res, "session", None)
    except Exception:
        sess = None

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

    _store_realtime_session(
        sb, token_hash,
        getattr(sess, "access_token", None) if sess else None,
        getattr(sess, "refresh_token", None) if sess else None,
    )

    return jsonify(
        token=raw_token,
        user={
            "id": user.id,
            "email": email,
            "username": username,
            "avatar_url": None,
            "is_admin": False,
        },
    )


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
    code = _str_field(data, "code")

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
    # TOTP factor varsa, ŞİFRE DOĞRU OLSA BİLE `code` gelmediği sürece token
    # üretilmez. `tmp` (session set edilmiş client) burada AÇIK bırakılır —
    # aşağıda code varsa AYNI client challenge/verify için reuse edilir (yeni
    # bir sign_in_with_password GEREKMEZ, bu noktada zaten canlı bir Auth
    # session'ımız var).
    has_totp = False
    totp_factor = None
    tmp = None
    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        tmp.auth.set_session(sess.access_token, getattr(sess, "refresh_token", None))
        factors = tmp.auth.mfa.list_factors()
        for f in (factors.totp or []):
            if f.factor_type == "totp" and f.status == "verified":
                totp_factor = f
                has_totp = True
                break
    except Exception:
        # MFA kontrolü başarısız — 2FA yok sayılır (auth.py login()'deki fail-open
        # davranışın aynısı; kontrol servisi geçiciyse giriş engellenmemeli)
        pass

    if has_totp and not code:
        return jsonify(
            error="mfa_required",
            message="Bu hesapta 2FA aktif. Aynı isteği `code` alanıyla tekrar gönder.",
        ), 403

    if has_totp and code:
        try:
            challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
            tmp.auth.mfa.verify({
                "factor_id": totp_factor.id,
                "challenge_id": challenge_resp.id,
                "code": code,
            })
        except Exception:
            # Ekstra rate limit gerekmez — üstteki login:{ip} limiter (satır ~148)
            # zaten her denemeyi (kod yanlış olsa bile) sayıyor.
            return jsonify(error="invalid_code"), 401

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

    # Realtime oturumu — ORİJİNAL `sess` kullanılır (yukarıdaki 2FA kontrolü
    # için set_session yapılan `tmp` client'ın DEĞİL): sess, satır ~209'daki
    # ilk sign_in_with_password sonucudur ve access_token/refresh_token'ı
    # tmp'nin sonraki mfa çağrılarından ETKİLENMEZ (ayrı client örneği).
    _store_realtime_session(sb, token_hash, sess.access_token, getattr(sess, "refresh_token", None))

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


@bp.route("/auth/google", methods=["POST"])
def api_google_login():
    """Google ile giriş — web'in tarayıcı-yönlendirmeli auth.py google_complete()
    akışından MİMARİ OLARAK FARKLI: web bir Supabase OAuth authorize-redirect
    akışıyla (tarayıcı) ELDE EDİLMİŞ bir access_token'ı doğrular; native ise
    Android Credential Manager'ın DOĞRUDAN ürettiği bir Google ID token'ı
    (OIDC JWT) `sign_in_with_id_token()` ile Supabase'e DEĞİŞTİRİR (tarayıcı/
    redirect adımı hiç yok). Supabase'in Google provider ayarı (Client ID)
    web ile PAYLAŞILIR — native için ayrı bir OAuth client kaydı YOK.

    `nonce` opsiyonel: Android Credential Manager `GetSignInWithGoogleOption`
    ile hash'lenmiş bir nonce gönderip ID token'a gömebilir; client bunu ham
    haliyle buraya da yollarsa Supabase token'daki hash'lenmiş nonce'la
    karşılaştırıp replay saldırısına karşı ekstra doğrulama yapar. Native
    Kotlin tarafı bunu henüz göndermeyebilir — bu durumda Supabase nonce
    kontrolünü atlar (dict'te hiç anahtar yoksa sorun çıkmaz).
    """
    if is_rate_limited(f"login:{request.remote_addr or 'unknown'}", 10, 300):
        return jsonify(error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    id_token = _str_field(data, "id_token")
    nonce = _str_field(data, "nonce")
    code = _str_field(data, "code")
    device_name = _str_field(data, "device_name") or None

    if not id_token:
        return jsonify(error="missing_token"), 400

    credentials = {"provider": "google", "token": id_token}
    if nonce:
        credentials["nonce"] = nonce

    try:
        tmp = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        res = call_with_ssl_retry(lambda: tmp.auth.sign_in_with_id_token(credentials))
    except Exception as e:
        # Gerçek red nedenini (nonce uyuşmazlığı/audience/expired vb.) logla —
        # önceden çıplak "invalid_token" dönüp sebep tamamen kayboluyordu,
        # native'de "hesap seçtim ama giriş olmadı" raporu teşhis edilemiyordu.
        current_app.logger.error(f"Google id_token girişi reddedildi: {e}")
        return jsonify(error="invalid_token"), 401

    user = getattr(res, "user", None)
    sess = getattr(res, "session", None)
    if not user or not sess or not getattr(sess, "access_token", None):
        return jsonify(error="invalid_token"), 401

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

    # İlk Google girişi: profiles satırı yoksa oluştur — auth.py
    # google_complete()'teki (satır 368-386) AYNI mantık, AYNI yardımcı
    # (_unique_username) reuse edilir.
    if not prof:
        meta = getattr(user, "user_metadata", None) or {}
        try:
            sb.table("profiles").upsert({
                "id": user.id,
                "username": _unique_username(sb, user.email),
                "email": user.email,
                "full_name": meta.get("full_name") or meta.get("name"),
                "avatar_url": meta.get("avatar_url") or meta.get("picture"),
                "is_private": True,
            }, on_conflict="id").execute()
            invalidate("valid_usernames")
        except Exception:
            pass
        try:
            prof = sb.table("profiles").select(
                "is_banned, username, avatar_url, is_admin"
            ).eq("id", user.id).execute().data
            prof_data = prof[0] if prof else {}
        except Exception:
            prof_data = {}

    # 2FA paritesi — login()'deki AYNI desen: ZATEN canlı olan bu session
    # (sign_in_with_id_token'dan) reuse edilir, ayrı bir sign-in gerekmez.
    has_totp = False
    totp_factor = None
    try:
        factors = tmp.auth.mfa.list_factors()
        for f in (factors.totp or []):
            if f.factor_type == "totp" and f.status == "verified":
                totp_factor = f
                has_totp = True
                break
    except Exception:
        pass

    if has_totp and not code:
        return jsonify(
            error="mfa_required",
            message="Bu hesapta 2FA aktif. Aynı isteği `code` alanıyla tekrar gönder.",
        ), 403

    if has_totp and code:
        try:
            challenge_resp = tmp.auth.mfa.challenge({"factor_id": totp_factor.id})
            tmp.auth.mfa.verify({
                "factor_id": totp_factor.id,
                "challenge_id": challenge_resp.id,
                "code": code,
            })
        except Exception:
            return jsonify(error="invalid_code"), 401

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

    # Realtime oturumu — sess, sign_in_with_id_token()'dan (satır ~409 ETRAFI)
    # ZATEN doğrulanmış (access_token/refresh_token var), login()'deki AYNI desen.
    _store_realtime_session(sb, token_hash, sess.access_token, getattr(sess, "refresh_token", None))

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
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            # Defense-in-depth: asıl koruma zaten revoked_at (api_login_required
            # bunu kontrol ediyor) ama satırda ölü bir Supabase refresh token
            # bırakmamak daha temiz — iptal edilmiş bir token'ın Realtime
            # oturumu da ölü sayılmalı.
            "sb_access_token_enc": None,
            "sb_refresh_token_enc": None,
            "sb_token_expires_at": None,
        }).eq("token_hash", request.api_token_hash).execute()
    except Exception:
        return jsonify(error="logout_failed"), 500
    return jsonify(ok=True)


@bp.route("/auth/me")
@api_login_required
def me():
    return jsonify(user=request.api_user)


@bp.route("/realtime-token")
@api_login_required
def api_realtime_token():
    """Native için taze Supabase Auth access token'ı — auth.py realtime_token()
    (web)'in AYNI mantığı, ama Flask session yerine bu isteğin taşıdığı bearer
    token'a karşılık gelen api_tokens satırından (şifreli) okunur/yazılır.

    Realtime hiçbir zaman ÇEKİRDEK bir özellik değildir (bkz. app/
    realtime_session.py docstring'i, fail-open) — bu yüzden burada dönen
    HER hata kodu (503/401) native tarafın Realtime'ı sessizce kapatıp ana
    bearer-token akışına DOKUNMADAN devam edebileceği şekilde tasarlandı.
    """
    import requests as _rq

    sb = get_sb()
    token_hash = request.api_token_hash

    try:
        rows = sb.table("api_tokens").select(
            "sb_access_token_enc, sb_refresh_token_enc, sb_token_expires_at"
        ).eq("token_hash", token_hash).execute().data
    except Exception:
        return jsonify(error="unavailable"), 503
    if not rows:
        return jsonify(error="unavailable"), 503
    row = rows[0]

    refresh_token = _decrypt_token(row.get("sb_refresh_token_enc"))
    if not refresh_token:
        # Fernet yok / bu satır hiç enroll edilmemiş / anahtar rotasyonu —
        # HANGİSİ olursa olsun client için AYNI sonuç: Realtime kullanılamaz.
        return jsonify(error="unavailable"), 503
    access_token = _decrypt_token(row.get("sb_access_token_enc"))

    needs_refresh = not access_token
    expires_at = row.get("sb_token_expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            needs_refresh = needs_refresh or (exp_dt - datetime.now(timezone.utc)).total_seconds() <= 300
        except Exception:
            needs_refresh = True
    else:
        needs_refresh = True

    if needs_refresh and is_rate_limited(f"realtime_refresh:{token_hash}", 12, 60):
        # Supabase'in refresh_token grant endpoint'ini flood etmeyi önler (aynı
        # cihaz/oturum art arda çok sık çağırırsa) — security incelemesinde
        # bulundu. Per-token_hash (cihaz başına) ve gerçek kullanımın (~1
        # yenileme/saat) çok üzerinde bir bütçe: limit aşılırsa Supabase'e HİÇ
        # gidilmez, mevcut (belki süresi yaklaşmış ama henüz geçmemiş)
        # access_token'la devam edilir — fail-open, hard error YOK.
        needs_refresh = False

    if needs_refresh:
        try:
            r = _rq.post(
                current_app.config["SUPABASE_URL"] + "/auth/v1/token?grant_type=refresh_token",
                headers={"apikey": current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                         "Content-Type": "application/json"},
                json={"refresh_token": refresh_token},
                timeout=6,
            )
            if r.status_code == 200:
                d = r.json()
                new_access = d.get("access_token")
                new_refresh = d.get("refresh_token") or refresh_token
                if new_access:
                    _store_realtime_session(sb, token_hash, new_access, new_refresh)
                    access_token = new_access
            elif r.status_code in (400, 401, 403):
                # Refresh token KESİN reddedildi — auth.py refresh_session_tokens()'daki
                # AYNI gerekçe (süresi dolmuş/iptal/başka projeden kalma). Satırdaki
                # ölü Realtime oturumunu temizle; client relogin_required görüp
                # Realtime'ı kapatır, ANA bearer token'a (bu isteği DOĞRULAYAN token)
                # DOKUNULMAZ — kullanıcı native'de login kalmaya devam eder.
                #
                # CAS (compare-and-swap) ile null'lanır: sadece satırdaki
                # sb_refresh_token_enc HÂLÂ az önce reddedilen (eski) değerse
                # temizlenir. Security incelemesinde bulundu — aynı cihazdan
                # eşzamanlı iki /realtime-token isteği aynı tek-kullanımlık
                # refresh_token'ı yarışarak tüketirse, biri Supabase'den YENİ
                # bir çift alıp satırı GÜNCELLERKEN diğeri (ESKİ refresh_token'la
                # denediği için) 400 alır; şartsız bir null'lama bu durumda
                # kazananın az önce yazdığı GEÇERLİ oturumu silerdi. eq ile
                # eşleşmezse (biri zaten satırı yeni değerle değiştirmişse)
                # update SIFIR satırı etkiler, taze oturum korunur.
                try:
                    sb.table("api_tokens").update({
                        "sb_access_token_enc": None,
                        "sb_refresh_token_enc": None,
                        "sb_token_expires_at": None,
                    }).eq("token_hash", token_hash).eq(
                        "sb_refresh_token_enc", row.get("sb_refresh_token_enc")
                    ).execute()
                except Exception:
                    pass
                return jsonify(error="relogin_required"), 401
            # Ağ hatası/5xx: mevcut (henüz süresi geçmemiş olabilecek) access_token'la devam
        except Exception:
            pass

    if not access_token:
        return jsonify(error="unavailable"), 503

    return jsonify(
        access_token=access_token,
        supabase_url=current_app.config["SUPABASE_URL"],
        supabase_publishable_key=current_app.config["SUPABASE_PUBLISHABLE_KEY"],
    )
