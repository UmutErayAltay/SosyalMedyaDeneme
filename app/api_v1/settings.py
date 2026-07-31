from datetime import datetime, timezone

from flask import request, jsonify, current_app
from supabase import create_client

from . import bp
from ._common import _str_field, api_login_required
from ..supabase_client import get_sb, call_with_ssl_retry
from ..rate_limit import is_rate_limited
from ..cache import invalidate
from ..storage_helper import upload_image
from ..notifications import NOTIFICATION_TYPES
from ..blocks import is_blocked_either_way


# ----------------------- PROFİL AYARLARI (Faz 4, native Android) -----------------------
# app/routes/profile.py profile_edit()/deactivate_account(), app/notifications.py
# preferences() ve app/close_friends.py'nin BİLİNÇLİ bir alt kümesi JSON'a
# taşınır: temel profil düzenleme (bio/tam ad/kullanıcı adı/avatar/gizlilik-
# son-görülme), bildirim tercihleri, yakın arkadaşlar, hesap deaktivasyonu.
# KAPSAM DIŞI (ayrı, gelecek bir iterasyon): 2FA enroll/verify/disable (QR kod
# akışı ayrı ve güvenlik açısından daha dikkatli ele alınacak), aktif oturum
# listesi/uzaktan-çıkış yönetimi, şifre değiştirme/sıfırlama.

def _bool_form_field(value: str | None) -> bool:
    """Native toggle'lardan gelen "true"/"false" string'ini bool'a çevirir.

    Web'deki HTML checkbox davranışından (yokluk == False, varlık == "on")
    farklı olarak native her zaman açık bir "true"/"false" gönderiyor — ama
    eksik/beklenmedik bir değer yine de False sayılır (fail-closed, web'in
    "yoksa kapalı" sonucuyla tutarlı).
    """
    return value == "true"


@bp.route("/profile/edit", methods=["POST"])
@api_login_required
def api_profile_edit():
    """Profil düzenle — profile.py profile_edit()'in POST dalının AYNI mantığı
    (migration-yoksa-fallback dahil). multipart/form-data (JSON DEĞİL) —
    avatar dosyası içerebiliyor, api_create_post()'daki AYNI kodlama deseni.
    Session senkronu YOK: native token-based, Flask session'a hiç dokunmuyor
    (web'deki navbar/session güncellemesi burada anlamsız).
    """
    sb = get_sb()
    me = request.api_user["id"]

    full_name = (request.form.get("full_name") or "").strip()
    bio = (request.form.get("bio") or "").strip()
    username = (request.form.get("username") or "").strip()
    avatar_file = request.files.get("avatar")

    if not username or len(username) < 3:
        return jsonify(error="short_username"), 400

    # Kullanıcı adı başkası tarafından kullanılıyor mu?
    if username != request.api_user.get("username", ""):
        taken = sb.table("profiles").select("id").eq("username", username).neq(
            "id", me
        ).execute()
        if taken.data:
            return jsonify(error="username_taken"), 400

    is_private = _bool_form_field(request.form.get("is_private"))
    hide_last_seen = _bool_form_field(request.form.get("hide_last_seen"))
    update_data = {"full_name": full_name or None, "bio": bio or None, "username": username,
                   "is_private": is_private, "hide_last_seen": hide_last_seen}

    if avatar_file and avatar_file.filename:
        avatar_url = upload_image(avatar_file, folder="avatars")
        if not avatar_url:
            return jsonify(error="upload_failed"), 400
        update_data["avatar_url"] = avatar_url

    # profile_edit()'deki AYNI fallback: is_private/hide_last_seen kolonları
    # yoksa (migration henüz uygulanmamışsa) onlarsız tekrar dene.
    try:
        sb.table("profiles").update(update_data).eq("id", me).execute()
    except Exception:
        core_data = {"full_name": update_data["full_name"], "bio": update_data["bio"],
                     "username": update_data["username"]}
        if "avatar_url" in update_data:
            core_data["avatar_url"] = update_data["avatar_url"]
        sb.table("profiles").update(core_data).eq("id", me).execute()

    invalidate("valid_usernames")
    invalidate(f"sidebar:{me}")  # bio değişti, sidebar_stats cache'i bayat kaldı

    prof_rows = sb.table("profiles").select("*").eq("id", me).execute().data
    prof_data = prof_rows[0] if prof_rows else {}
    return jsonify(ok=True, profile={
        "id": me,
        "username": prof_data.get("username", username),
        "full_name": prof_data.get("full_name"),
        "bio": prof_data.get("bio"),
        "avatar_url": prof_data.get("avatar_url"),
        "is_private": prof_data.get("is_private", False),
        "hide_last_seen": prof_data.get("hide_last_seen", False),
    })


@bp.route("/notifications/preferences", methods=["GET", "POST"])
@api_login_required
def api_notification_preferences():
    """Bildirim türü bazlı opt-out ayarları — notifications.py preferences()'ın
    AYNI mantığı (fail-open: satır yoksa hepsi True), JSON döner.

    POST'ta web'deki HTML checkbox kodlamasından (yokluk == kapalı) FARKLI
    olarak native JSON'da her alan için gerçek bool taşır — eksik/yok alan
    yine de False sayılır (AYNI "yoksa kapalı" sonucu, farklı kodlama biçimi).
    """
    sb = get_sb()
    me = request.api_user["id"]
    columns = [t[1] for t in NOTIFICATION_TYPES]

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        try:
            payload = {col: bool(data.get(col, False)) for col in columns}
            payload["user_id"] = me
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            sb.table("notification_preferences").upsert(
                payload, on_conflict="user_id"
            ).execute()
        except Exception:
            # Kolon eksikse (migration uygulanmamışsa) veya başka DB hatası —
            # preferences()'daki AYNI graceful degradation, sayfa/istek kırılmaz.
            return jsonify(error="unavailable"), 503
        return jsonify(ok=True)

    try:
        rows = sb.table("notification_preferences").select("*").eq(
            "user_id", me
        ).execute().data
        prefs = rows[0] if rows else {col: True for col in columns}
    except Exception:
        prefs = {col: True for col in columns}

    return jsonify(preferences={col: bool(prefs.get(col, True)) for col in columns})


@bp.route("/close-friends")
@api_login_required
def api_close_friends_list():
    """Yakın arkadaşlar listesi — close_friends.py close_friends_list()'in
    AYNI mantığı (migration yoksa boş liste, sayfa/istek kırılmaz)."""
    sb = get_sb()
    me = request.api_user["id"]
    users = []
    try:
        rows = sb.table("close_friends").select(
            "profiles!close_friends_friend_id_fkey(id, username, avatar_url, full_name)"
        ).eq("owner_id", me).order("created_at", desc=True).execute().data
        users = [r["profiles"] for r in rows if r.get("profiles")]
    except Exception:
        users = []
    return jsonify(users=users)


@bp.route("/close-friends/add", methods=["POST"])
@api_login_required
def api_add_close_friend():
    """Yakın arkadaş ekle — close_friends.py add_close_friend()'in AYNI
    mantığı (kendini ekleme/engelli kontrolü dahil)."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    target_id = _str_field(data, "user_id")

    if not target_id:
        return jsonify(error="missing_user"), 400
    if target_id == me:
        return jsonify(error="cannot_add_self"), 400
    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="blocked"), 403

    try:
        sb.table("close_friends").upsert({"owner_id": me, "friend_id": target_id}).execute()
    except Exception:
        return jsonify(error="unavailable"), 503  # migration henüz uygulanmamış

    return jsonify(ok=True)


@bp.route("/close-friends/<user_id>/remove", methods=["POST"])
@api_login_required
def api_remove_close_friend(user_id):
    """Yakın arkadaşlardan çıkar — close_friends.py remove_close_friend()'in
    AYNI mantığı (satır yoksa delete no-op, yine de ok=True — idempotent)."""
    sb = get_sb()
    me = request.api_user["id"]
    sb.table("close_friends").delete().eq("owner_id", me).eq("friend_id", user_id).execute()
    return jsonify(ok=True)


def _api_user_has_password_identity(sb, user_id: str) -> bool:
    """auth.py _user_has_password_identity()'in native karşılığı.

    Web tarafı tarayıcıda saklı bir Supabase Auth access_token'ıyla
    auth.get_user() çağırıp identities okur. Native tarafta böyle bir token
    HİÇ YOK — api_tokens tamamen ayrı, kendi ürettiğimiz opak bir bearer
    sistemi (bkz. dosya başındaki modül docstring'i), native login() bir
    Supabase Auth session'ı hiç saklamıyor. Bu yüzden burada admin API
    (get_sb() zaten service-role) ile DOĞRUDAN user_id üzerinden identities
    okunur — sonuç (email/şifre identity'si var mı) AYNI, sadece erişim yolu
    farklı. Belirsiz durumda (admin API başarısız) fail-closed: şifre
    isteniyor kabul edilir — kaynak fonksiyonla AYNI güvenlik tavrı.
    """
    try:
        user_res = sb.auth.admin.get_user_by_id(user_id)
        user = getattr(user_res, "user", None)
        identities = getattr(user, "identities", None) or []
        return any(getattr(i, "provider", None) == "email" for i in identities)
    except Exception:
        return True


@bp.route("/profile/deactivate", methods=["POST"])
@api_login_required
def api_deactivate_account():
    """Hesabı deaktive et — profile.py deactivate_account()'ın AYNI mantığı
    (şifre doğrulaması koşullu + rate limit + is_deactivated=true).

    DİKKAT (native'e özgü sapma, kaynak fonksiyonda YOK): web session.clear()
    + user_sessions satırı silinerek TARAYICI oturumu kapatılır; native'de
    Flask session hiç yok. Bunun yerine bu isteğin taşıdığı Bearer token'ın
    KENDİSİ iptal edilir (logout()'daki AYNI desen) — deaktivasyon sonrası
    aynı token'la native tarafta hiçbir şey yapılamaz. Supabase Auth
    sign-out adımı da native'de YOK çünkü native login() zaten bir Supabase
    Auth session'ı hiç saklamıyor (kapatılacak bir tarayıcı session'ı yok).
    """
    sb = get_sb()
    me = request.api_user["id"]
    user_email = request.api_user.get("email")

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    password = _str_field(data, "password")

    # Google-only hesaplarda (hiç şifre yok) reverifikasyon adımı atlanır —
    # deactivate_account()'daki AYNI gerekçe (bkz. _api_user_has_password_identity).
    if _api_user_has_password_identity(sb, me):
        if not password:
            return jsonify(error="password_required"), 400

        # Rate limit — deactivate_account()'daki AYNI anahtar+limit (web ile
        # PAYLAŞILIR: aynı kullanıcı web+native'den art arda denerse ikisi
        # birlikte sayılır — login()'deki paylaşılan anahtar gerekçesiyle tutarlı).
        if is_rate_limited(f"deactivate:{me}", 5, 300):
            return jsonify(error="rate_limited"), 429

        try:
            tmp_auth = create_client(
                current_app.config["SUPABASE_URL"],
                current_app.config["SUPABASE_PUBLISHABLE_KEY"],
            )
            call_with_ssl_retry(
                lambda: tmp_auth.auth.sign_in_with_password({
                    "email": user_email, "password": password,
                })
            )
        except Exception:
            return jsonify(error="invalid_password"), 401

    try:
        sb.table("profiles").update({"is_deactivated": True}).eq("id", me).execute()
    except Exception:
        return jsonify(error="deactivation_failed"), 500

    # Bu isteğin token'ını iptal et — logout()'daki AYNI desen (yukarıdaki
    # docstring'deki gerekçe: native'de kapatılacak bir Flask session yok).
    # sb_* kolonları da temizlenir — logout()'daki AYNI defense-in-depth gerekçesi.
    try:
        sb.table("api_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "sb_access_token_enc": None,
            "sb_refresh_token_enc": None,
            "sb_token_expires_at": None,
        }).eq("token_hash", request.api_token_hash).execute()
    except Exception:
        pass

    return jsonify(ok=True)
