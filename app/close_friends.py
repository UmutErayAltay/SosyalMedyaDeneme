"""Yakın arkadaşlar listesi: postları herkese açık/takipçi katmanından daha
dar bir gruba özel paylaşmak için elle seçilen liste (bkz. app/visibility.py)."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .blocks import is_blocked_either_way

bp = Blueprint("close_friends", __name__)


@bp.route("/close-friends")
@login_required
@retry_on_connection_error
def close_friends_list():
    sb = get_sb()
    me = session["user"]["id"]
    users = []
    try:
        rows = sb.table("close_friends").select(
            "profiles!close_friends_friend_id_fkey(id, username, avatar_url, full_name)"
        ).eq("owner_id", me).order("created_at", desc=True).execute().data
        users = [r["profiles"] for r in rows if r.get("profiles")]
    except Exception:
        pass
    return render_template("close_friends.html", users=users, me=session.get("user"))


@bp.route("/close-friends/add", methods=["POST"])
@login_required
@retry_on_connection_error
def add_close_friend():
    sb = get_sb()
    me = session["user"]["id"]
    data = request.get_json(silent=True) or {}
    target_id = data.get("user_id")
    if not target_id:
        return jsonify(error="Kullanıcı gerekli."), 400
    if target_id == me:
        return jsonify(error="Kendini ekleyemezsin."), 400
    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="Engellenen/engelleyen kullanıcıları ekleyemezsin."), 403
    try:
        sb.table("close_friends").upsert({"owner_id": me, "friend_id": target_id}).execute()
    except Exception:
        return jsonify(error="Özellik henüz aktif değil (migration uygulanmamış)."), 503
    return jsonify(ok=True)


@bp.route("/close-friends/<user_id>/remove", methods=["POST"])
@login_required
@retry_on_connection_error
def remove_close_friend(user_id):
    sb = get_sb()
    me = session["user"]["id"]
    sb.table("close_friends").delete().eq("owner_id", me).eq("friend_id", user_id).execute()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Yakın arkadaşlardan çıkarıldı.", "success")
    return redirect(url_for("close_friends.close_friends_list"))
