"""Sessize alma: takip etmeye devam ederken feed'den gizle.

Engelleme (block) gibi karşı tarafı etkilemez — sadece muter'ın feed'inde
muted kişinin postları gizlenir. Profil sayfası, keşfet, arama — muted kişinin
postları YİNE görünür."""

from flask import Blueprint, request, session, abort, jsonify, redirect, url_for, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("mutes", __name__)


def muted_user_ids(sb, me: str) -> set:
    """me'nin mute ettiği (gizlediği) kullanıcı id'lerinin kümesi.

    SADECE feed'de, feed_page_posts RPC'de veya Python fallback'te kullanılır.
    Profil/keşfet/arama bu filtreyi UYGULAMAZ — muted kişinin postları
    bu yerlerde görünür.
    """
    ids = set()
    try:
        ids = {
            r["muted_id"] for r in sb.table("muted_users").select("muted_id")
            .eq("muter_id", me).execute().data
        }
    except Exception:
        pass  # migration henüz uygulanmamış olabilir
    return ids


@bp.route("/mute/<user_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_mute(user_id):
    """Bir kullanıcıyı sessize al (feed'den gizle) veya sessizsil.

    Kendi kendini mute etmeye engel; blocked/blocker olunun durumu ne olursa
    olsun mute/unmute yapılabilir.
    """
    sb = get_sb()
    me = session["user"]["id"]

    if user_id == me:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Kendini mute edemezsin."), 400
        flash("Kendini mute edemezsin.", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    try:
        # Zaten mute edilmiş mi?
        existing = sb.table("muted_users").select("muter_id").eq(
            "muter_id", me
        ).eq("muted_id", user_id).execute().data

        if existing:
            # Unmute et
            sb.table("muted_users").delete().eq("muter_id", me).eq(
                "muted_id", user_id
            ).execute()
            action = "unmuted"
        else:
            # Mute et
            sb.table("muted_users").insert({
                "muter_id": me,
                "muted_id": user_id
            }).execute()
            action = "muted"
    except Exception:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Mute özelliği henüz aktif değil."), 503
        flash("Mute özelliği henüz aktif değil (migration uygulanmamış).", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, action=action)

    if action == "muted":
        flash("Kullanıcı sessize alındı. Gönderileri feed'de görünmeyecek.", "success")
    else:
        flash("Kullanıcının sessizliği kaldırıldı.", "success")

    return redirect(request.referrer or url_for("routes.feed"))
