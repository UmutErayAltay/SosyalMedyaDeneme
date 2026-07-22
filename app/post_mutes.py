"""Post-bazlı sessize alma: bu gönderiye gelen bildirimleri (beğeni/yorum) kapat, kullanıcıyı tamamen mute etmeden."""

from flask import Blueprint, request, session, jsonify, redirect, url_for, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("post_mutes", __name__)


def muted_post_ids(sb, me: str) -> set:
    """me'nin sessize aldığı post id'lerinin kümesi — notify() içinde kullanılır.

    Migration henüz uygulanmamışsa boş set döner (fail-open).
    """
    ids = set()
    try:
        ids = {
            r["post_id"] for r in sb.table("muted_posts").select("post_id")
            .eq("user_id", me).execute().data
        }
    except Exception:
        pass  # migration henüz uygulanmamış olabilir
    return ids


@bp.route("/post/<post_id>/mute", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_mute_post(post_id):
    """Bir gönderiye gelen bildirimleri sessize al (beğeni/yorum) veya sessizlikten çıkar.

    Post sahibi DAHİL herkes kendi bildirimlerini mute edebilir — asıl
    kullanım senaryosu tam olarak bu: kendi paylaştığın popüler bir postun
    beğeni/yorum bildirimi seli altında kalman (like/comment recipient'ı
    post'un sahibidir, bkz. app/social.py toggle_like()/add_comment()).
    """
    sb = get_sb()
    me = session["user"]["id"]

    try:
        # Zaten mute edilmiş mi?
        existing = sb.table("muted_posts").select("user_id").eq(
            "user_id", me
        ).eq("post_id", post_id).execute().data

        if existing:
            # Unmute et
            sb.table("muted_posts").delete().eq("user_id", me).eq(
                "post_id", post_id
            ).execute()
            action = "unmuted"
        else:
            # Mute et
            sb.table("muted_posts").insert({
                "user_id": me,
                "post_id": post_id
            }).execute()
            action = "muted"
    except Exception:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Bu özellik henüz aktif değil."), 503
        flash("Bu özellik henüz aktif değil (migration uygulanmamış).", "error")
        return redirect(request.referrer or url_for("routes.feed"))

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, action=action)

    if action == "muted":
        flash("Bu gönderi için bildirimleri kapatıldı.", "success")
    else:
        flash("Bu gönderi için bildirimleri tekrar açıldı.", "success")

    return redirect(request.referrer or url_for("routes.feed"))
