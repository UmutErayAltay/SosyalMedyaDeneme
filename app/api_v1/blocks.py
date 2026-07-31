from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..cache import invalidate


# ----------------------- ENGELLEME (Faz 4 sonrası eksik giderme, native Android) -----------------------
# app/blocks.py toggle_block()/blocked_list()'in AYNI mantığı JSON'a taşınır —
# eksik denetimi (2026-07-30) native'de kullanıcı engelleme/engellenenler
# listesinin hiç olmadığını ortaya çıkardı. blocked_user_ids/has_blocked/
# is_blocked_either_way ZATEN import edilmişti (dosya başı) çünkü feed/discover/
# messaging filtrelemesinde reuse ediliyordu — burada SADECE toggle+liste eklendi.

@bp.route("/block/<username>", methods=["POST"])
@api_login_required
def api_toggle_block(username):
    """Engelle/engeli kaldır toggle — blocks.py toggle_block()'ın AYNI mantığı
    (form/redirect dalı YOK, native zaten JSON bekliyor). Engelleyince karşılıklı
    takip ilişkisi de kopar (her iki yönde) — toggle_block()'daki AYNI yan etki.
    """
    sb = get_sb()
    me = request.api_user["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute().data
    if not target:
        return jsonify(error="not_found"), 404
    target_id = target[0]["id"]
    if target_id == me:
        return jsonify(error="cannot_block_self"), 400

    try:
        existing = sb.table("blocks").select().eq("blocker_id", me).eq(
            "blocked_id", target_id
        ).execute().data
        if existing:
            sb.table("blocks").delete().eq("blocker_id", me).eq("blocked_id", target_id).execute()
            blocked = False
        else:
            sb.table("blocks").insert({"blocker_id": me, "blocked_id": target_id}).execute()
            sb.table("follows").delete().eq("follower_id", me).eq("following_id", target_id).execute()
            sb.table("follows").delete().eq("follower_id", target_id).eq("following_id", me).execute()
            invalidate(f"sidebar:{me}")
            invalidate(f"sidebar:{target_id}")
            blocked = True
    except Exception:
        return jsonify(error="blocks_not_available"), 503

    return jsonify(ok=True, blocked=blocked)


@bp.route("/blocked")
@api_login_required
def api_blocked_list():
    """Engellenen kullanıcılar listesi — blocks.py blocked_list()'in AYNI
    sorgusu (SADECE me'nin engellediği kullanıcılar, me'yi engelleyenler DEĞİL —
    blocked_list() ile birebir tutarlı)."""
    sb = get_sb()
    me = request.api_user["id"]

    users = []
    try:
        rows = sb.table("blocks").select(
            "profiles!blocks_blocked_id_fkey(id, username, avatar_url, full_name)"
        ).eq("blocker_id", me).order("created_at", desc=True).execute().data
        users = [r["profiles"] for r in rows if r.get("profiles")]
    except Exception:
        pass

    return jsonify(users=users)
