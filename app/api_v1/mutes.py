from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb


# ----------------------- SESSİZE ALMA (Faz 5 Dalga 2A, native Android) -----------------------
# app/mutes.py toggle_mute()/app/post_mutes.py toggle_mute_post()'un AYNI mantığı
# JSON'a taşınır (form/redirect dalı YOK, native zaten JSON bekliyor). Okuma
# tarafında (feed filtreleme, profile.is_muted, _attach_post_metrics muted_by_me)
# SIFIR değişiklik gerekti — sadece bu iki toggle endpoint'i eksikti.

@bp.route("/users/<user_id>/mute", methods=["POST"])
@api_login_required
def api_toggle_mute_user(user_id):
    """Bir kullanıcıyı sessize al (feed'den gizle) veya sessizliğini kaldır —
    app/mutes.py toggle_mute()'ın AYNI mantığı. Kendi kendini mute etmeye engel;
    blocked/blocker olunun durumu ne olursa olsun mute/unmute yapılabilir.
    """
    sb = get_sb()
    me = request.api_user["id"]

    if user_id == me:
        return jsonify(error="cannot_mute_self"), 400

    try:
        existing = sb.table("muted_users").select("muter_id").eq(
            "muter_id", me
        ).eq("muted_id", user_id).execute().data

        if existing:
            sb.table("muted_users").delete().eq("muter_id", me).eq(
                "muted_id", user_id
            ).execute()
            action = "unmuted"
        else:
            sb.table("muted_users").insert({
                "muter_id": me,
                "muted_id": user_id,
            }).execute()
            action = "muted"
    except Exception:
        return jsonify(error="Mute özelliği henüz aktif değil."), 503

    return jsonify(ok=True, action=action)


@bp.route("/posts/<post_id>/mute", methods=["POST"])
@api_login_required
def api_toggle_mute_post(post_id):
    """Bir gönderiye gelen bildirimleri sessize al (beğeni/yorum) veya
    sessizlikten çıkar — app/post_mutes.py toggle_mute_post()'ın AYNI mantığı.

    Sahiplik kontrolü BİLİNÇLİ OLARAK YOK (web'de de yok) — post sahibi DAHİL
    herkes kendi bildirimlerini mute edebilir (asıl kullanım senaryosu tam
    olarak bu: kendi paylaştığın popüler bir postun beğeni/yorum bildirimi
    seli altında kalman).
    """
    sb = get_sb()
    me = request.api_user["id"]

    try:
        existing = sb.table("muted_posts").select("user_id").eq(
            "user_id", me
        ).eq("post_id", post_id).execute().data

        if existing:
            sb.table("muted_posts").delete().eq("user_id", me).eq(
                "post_id", post_id
            ).execute()
            action = "unmuted"
        else:
            sb.table("muted_posts").insert({
                "user_id": me,
                "post_id": post_id,
            }).execute()
            action = "muted"
    except Exception:
        return jsonify(error="Bu özellik henüz aktif değil."), 503

    return jsonify(ok=True, action=action)
