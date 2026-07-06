"""Grup sohbeti yönetici (admin) rolü: yeniden adlandırma, üye ekle/çıkar,
admin yetkisi devri, gruptan ayrılma.

Tüm route'lar service-role client (get_sb) kullanır — RLS bypass edildiği
için "çağıran gerçekten admin mi" kontrolü burada, uygulama katmanında
yapılmak ZORUNDA (bkz. CLAUDE.md güvenlik kuralları).
"""
from flask import request, session, jsonify, url_for
from . import bp
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..notifications import notify
from ..blocks import blocked_user_ids


def _is_admin(sb, conversation_id: str, user_id: str) -> bool:
    """user_id, conversation_id'de gerçekten is_admin=True bir katılımcı mı?

    Service-role RLS'i bypass ettiği için bu kontrol her admin-gated
    route'ta DB'den tazelenir — session/ctx üzerinden gelen bilgiye güvenilmez.
    """
    row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    return bool(row) and bool(row[0].get("is_admin"))


@bp.route("/group/<conversation_id>/rename", methods=["POST"])
@login_required
@retry_on_connection_error
def rename_group(conversation_id):
    """Grup adını değiştirir. Sadece yöneticiler yapabilir."""
    sb = get_sb()
    me = session["user"]["id"]
    if not _is_admin(sb, conversation_id, me):
        return jsonify(error="Bu işlem için yönetici olman gerekiyor."), 403

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(error="Grup adı gerekli."), 400

    sb.table("conversations").update({"name": name}).eq("id", conversation_id).execute()
    return jsonify(ok=True, name=name)


@bp.route("/group/<conversation_id>/members/add", methods=["POST"])
@login_required
@retry_on_connection_error
def add_group_members(conversation_id):
    """Gruba yeni üye(ler) ekler. Sadece yöneticiler yapabilir."""
    sb = get_sb()
    me = session["user"]["id"]
    if not _is_admin(sb, conversation_id, me):
        return jsonify(error="Bu işlem için yönetici olman gerekiyor."), 403

    data = request.get_json(silent=True) or {}
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))
    if not user_ids:
        return jsonify(error="Eklenecek kullanıcı seçilmedi."), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="Engellediğin/seni engelleyen biriyle grup oluşturamazsın."), 403

    # Zaten üye olanları tekrar eklemeye çalışmak primary key ihlaline yol
    # açar — mevcut üyeleri çekip filtrelemek yeterli (grup üye sayısı küçük).
    existing = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).execute().data
    existing_ids = {r["user_id"] for r in existing}
    new_ids = [uid for uid in user_ids if uid not in existing_ids]
    if not new_ids:
        return jsonify(ok=True, added=[])

    sb.table("conversation_participants").insert([
        {"conversation_id": conversation_id, "user_id": uid, "is_admin": False} for uid in new_ids
    ]).execute()

    for uid in new_ids:
        notify(sb, recipient_id=uid, actor_id=me, type_="message", conversation_id=conversation_id)

    profiles = sb.table("profiles").select("id, username, avatar_url").in_("id", new_ids).execute().data
    added = [{**p, "is_admin": False} for p in profiles]
    return jsonify(ok=True, added=added)


@bp.route("/group/<conversation_id>/members/<user_id>/remove", methods=["POST"])
@login_required
@retry_on_connection_error
def remove_group_member(conversation_id, user_id):
    """Bir üyeyi gruptan çıkarır. Sadece yöneticiler yapabilir."""
    sb = get_sb()
    me = session["user"]["id"]
    if not _is_admin(sb, conversation_id, me):
        return jsonify(error="Bu işlem için yönetici olman gerekiyor."), 403
    if user_id == me:
        return jsonify(error="Kendini çıkaramazsın, gruptan ayrılmayı kullan."), 400

    target = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="Kullanıcı bu grupta değil."), 404

    sb.table("conversation_participants").delete().eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True)


@bp.route("/group/<conversation_id>/members/<user_id>/toggle-admin", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_group_admin(conversation_id, user_id):
    """Bir üyenin yöneticilik durumunu tersine çevirir. Sadece yöneticiler yapabilir."""
    sb = get_sb()
    me = session["user"]["id"]
    if not _is_admin(sb, conversation_id, me):
        return jsonify(error="Bu işlem için yönetici olman gerekiyor."), 403

    target = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="Kullanıcı bu grupta değil."), 404

    new_value = not bool(target[0].get("is_admin"))

    if user_id == me and not new_value:
        # Grup admin'siz kalmasın diye kendini düşürmeden önce başka bir
        # admin olduğundan emin olunur.
        other_admins = sb.table("conversation_participants").select("user_id").eq(
            "conversation_id", conversation_id
        ).eq("is_admin", True).neq("user_id", me).execute().data
        if not other_admins:
            return jsonify(error="Grubun tek yöneticisisin, önce başka birini yönetici yap."), 400

    sb.table("conversation_participants").update({"is_admin": new_value}).eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True, is_admin=new_value)


@bp.route("/group/<conversation_id>/leave", methods=["POST"])
@login_required
@retry_on_connection_error
def leave_group(conversation_id):
    """Herhangi bir üye gruptan ayrılabilir (admin şartı yok).

    Ayrılan kişi tek yöneticiyse ve grupta başka üye kaldıysa, grup
    yöneticisiz kalmasın diye kalan en eski üye otomatik yönetici yapılır.
    """
    sb = get_sb()
    me = session["user"]["id"]

    me_row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not me_row:
        return jsonify(error="Bu grupta değilsin."), 404
    was_admin = bool(me_row[0].get("is_admin"))

    sb.table("conversation_participants").delete().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute()

    remaining = sb.table("conversation_participants").select("user_id, is_admin").eq(
        "conversation_id", conversation_id
    ).order("created_at").execute().data

    if not remaining:
        # Grupta kimse kalmadı, konuşma satırını da temizle.
        sb.table("conversations").delete().eq("id", conversation_id).execute()
    elif was_admin and not any(r.get("is_admin") for r in remaining):
        oldest = remaining[0]["user_id"]
        sb.table("conversation_participants").update({"is_admin": True}).eq(
            "conversation_id", conversation_id
        ).eq("user_id", oldest).execute()

    return jsonify(ok=True, redirect=url_for("messaging.inbox"))
