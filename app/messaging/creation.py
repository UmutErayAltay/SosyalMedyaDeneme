"""Yeni konuşma/grup başlatma + paylaşım hedefi (kullanıcı) arama."""
from flask import request, redirect, url_for, session, abort, flash, jsonify
from . import bp
from ._common import _get_or_create_conversation
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..notifications import notify
from ..blocks import is_blocked_either_way, blocked_user_ids


@bp.route("/new/<username>", methods=["POST"])
@login_required
@retry_on_connection_error
def start_conversation(username):
    """Bir kullanıcıyla konuşma başlat. Mevcutsa ona, yoksa yenisini oluştur."""
    sb = get_sb()
    me = session["user"]["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute()
    if not target.data:
        abort(404)
    target_id = target.data[0]["id"]

    if is_blocked_either_way(sb, me, target_id):
        flash("Bu kullanıcıyla mesajlaşamazsın.", "error")
        return redirect(url_for("routes.profile", username=username))

    cid = _get_or_create_conversation(me, target_id)
    return redirect(url_for("messaging.conversation", conversation_id=cid))


@bp.route("/group/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_group():
    """Yeni bir grup sohbeti oluşturur (isim + en az 2 üye, kendisi dahil en az 3 kişi).

    sql/migration_group_chat.sql henüz uygulanmamışsa conversations.is_group/name
    kolonları yok — bu durumda grup oluşturma anlamlı olmadığı için (2 kişilik
    "grup" normal DM'den ayırt edilemez) net bir hata döndürülür, sessizce
    1:1'e düşülmez.
    """
    me = session["user"]["id"]
    sb = get_sb()
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))  # kendini hariç tut, sırayı koru

    if not name:
        return jsonify(error="Grup adı gerekli."), 400
    if len(user_ids) < 2:
        return jsonify(error="En az 2 kişi seçmelisin."), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="Engellediğin/seni engelleyen biriyle grup oluşturamazsın."), 403

    try:
        conv = sb.table("conversations").insert({
            "is_group": True, "name": name, "created_by": me,
        }).execute()
    except Exception:
        return jsonify(error="Grup sohbeti özelliği henüz aktif değil (migration uygulanmamış)."), 503
    cid = conv.data[0]["id"]

    # Grubu oluşturan kişi otomatik yönetici olur, davet edilenler değil
    # (bkz. sql/migration_group_chat_admin.sql — created_by ile tutarlı).
    sb.table("conversation_participants").insert(
        [{"conversation_id": cid, "user_id": me, "is_admin": True}]
        + [{"conversation_id": cid, "user_id": uid, "is_admin": False} for uid in user_ids]
    ).execute()

    for uid in user_ids:
        notify(sb, recipient_id=uid, actor_id=me, type_="message", conversation_id=cid)

    return jsonify(conversation_id=cid)


@bp.route("/share-targets")
@login_required
@retry_on_connection_error
def share_targets():
    """Modal açıldığında veya arama yapıldığında kullanıcı listesini döner."""
    sb = get_sb()
    me = session["user"]["id"]
    q = request.args.get("q", "").strip()
    blocked_ids = blocked_user_ids(sb, me)

    if q:
        if len(q) < 2:
            return jsonify([])
        # Arama yapıldığında eşleşen kullanıcıları getir (kendisi hariç)
        users = sb.table("profiles").select("id, username, avatar_url").ilike("username", f"%{q}%").neq("id", me).limit(20).execute().data
        users = [u for u in users if u["id"] not in blocked_ids]
        return jsonify(users)

    # Varsayılan: Takip edilen kullanıcıları getir
    follows = sb.table("follows").select(
        "profiles!follows_following_id_fkey(id, username, avatar_url)"
    ).eq("follower_id", me).execute().data

    # Tekrar edenleri temizle
    user_dict = {}
    for f in follows:
        if f.get("profiles") and f["profiles"]["id"] not in blocked_ids:
            p = f["profiles"]
            user_dict[p["id"]] = p

    return jsonify(list(user_dict.values()))
