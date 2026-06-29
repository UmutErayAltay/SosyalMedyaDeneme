"""Birebir mesajlaşma (DM).

Model: her iki kullanıcı için ortak bir 'conversation' satırı.
conversation_participants üzerinden kullanıcı ↔ konuşma eşleşmesi.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb

bp = Blueprint("messaging", __name__)


@bp.route("/")
@login_required
def inbox():
    """Konuşma listesi (en son mesaja göre sıralı)."""
    sb = get_sb()
    me = session["user"]["id"]

    parts = sb.table("conversation_participants").select(
        "conversation_id"
    ).eq("user_id", me).execute().data

    convos = []
    for p in parts:
        cid = p["conversation_id"]

        msgs = sb.table("messages").select("*, profiles!messages_sender_id_fkey(username, avatar_url)").eq(
            "conversation_id", cid
        ).order("created_at", desc=True).limit(1).execute().data
        last_msg = msgs[0] if msgs else None

        # Diğer katılımcı (conversation() route'undaki ile aynı mantık)
        other = sb.table("conversation_participants").select(
            "user_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
        ).neq("user_id", me).eq("conversation_id", cid).execute()
        other_user = other.data[0]["profiles"] if other.data else None

        convos.append({
            "id": cid,
            "last_message": last_msg,
            "other_user": other_user,
        })

    convos.sort(key=lambda c: c["last_message"]["created_at"]
                if c["last_message"] else "", reverse=True)

    return render_template("messages/inbox.html", convos=convos, me=session["user"])


@bp.route("/<conversation_id>")
@login_required
def conversation(conversation_id):
    """Tek bir konuşmanın mesaj akışı."""
    sb = get_sb()
    me = session["user"]["id"]

    # Kullanıcı bu konuşmada mı?
    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute()
    if not part.data:
        abort(403)

    # Diğer katılımcı
    other = sb.table("conversation_participants").select(
        "user_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).neq("user_id", me).eq("conversation_id", conversation_id).execute()
    other_user = other.data[0]["profiles"] if other.data else None

    messages = sb.table("messages").select(
        "*, profiles!messages_sender_id_fkey(username, avatar_url)"
    ).eq("conversation_id", conversation_id).order("created_at").execute().data

    return render_template("messages/conversation.html",
                           messages=messages, other_user=other_user,
                           conversation_id=conversation_id,
                           me=session["user"])


@bp.route("/<conversation_id>/send", methods=["POST"])
@login_required
def send_message(conversation_id):
    content = request.form.get("content", "").strip()
    if not content:
        return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    get_sb().table("messages").insert({
        "conversation_id": conversation_id,
        "sender_id": session["user"]["id"],
        "content": content,
    }).execute()
    return redirect(url_for("messaging.conversation", conversation_id=conversation_id))


@bp.route("/new/<username>")
@login_required
def start_conversation(username):
    """Bir kullanıcıyla konuşma başlat. Mevcutsa ona, yoksa yenisini oluştur."""
    sb = get_sb()
    me = session["user"]["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute()
    if not target.data:
        abort(404)
    target_id = target.data[0]["id"]

    # İkisinin de ortak olduğu bir konuşma var mı?
    my_convs = sb.table("conversation_participants").select(
        "conversation_id"
    ).eq("user_id", me).execute().data
    target_convs = sb.table("conversation_participants").select(
        "conversation_id"
    ).eq("user_id", target_id).execute().data
    my_ids = {c["conversation_id"] for c in my_convs}
    target_ids = {c["conversation_id"] for c in target_convs}
    shared = my_ids & target_ids

    if shared:
        cid = shared.pop()
    else:
        new_conv = sb.table("conversations").insert({}).execute()
        cid = new_conv.data[0]["id"]
        sb.table("conversation_participants").insert([
            {"conversation_id": cid, "user_id": me},
            {"conversation_id": cid, "user_id": target_id},
        ]).execute()

    return redirect(url_for("messaging.conversation", conversation_id=cid))