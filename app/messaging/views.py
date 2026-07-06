"""Konuşma listesi (inbox) + tek bir konuşmanın görüntülenmesi."""
from flask import render_template, request, session, abort
from . import bp
from ._common import _mark_read, _build_convos
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error


@bp.route("/")
@login_required
@retry_on_connection_error
def inbox():
    """Konuşma listesi (en son mesaja göre sıralı). Sağ panel boş/placeholder."""
    sb = get_sb()
    me = session["user"]["id"]
    convos = _build_convos(sb, me)
    return render_template("messages/inbox.html", convos=convos, active_id=None,
                           me=session["user"])


@bp.route("/<conversation_id>")
@login_required
@retry_on_connection_error
def conversation(conversation_id):
    """Tek bir konuşmanın mesaj akışı.

    AJAX isteği (sol listeden tıklanınca, X-Requested-With: fetch) ise sadece
    sağ paneli döner — messagesPanel.js bunu tam sayfa yenilemeden DOM'a enjekte
    eder. Normal (tam sayfa) istekte ise sol liste + sağ panel birlikte render edilir.
    """
    sb = get_sb()
    me = session["user"]["id"]

    # Kullanıcı bu konuşmada mı?
    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute()
    if not part.data:
        abort(403)
    # is_admin migration uygulanmamışsa kolon dict'te yok — güvenli varsayılan False
    my_is_admin = bool(part.data[0].get("is_admin"))

    # Grup meta bilgisi (is_group/name) — migration henüz uygulanmamışsa 1:1 gibi davranılır
    is_group = False
    group_name = None
    try:
        conv = sb.table("conversations").select("is_group, name").eq("id", conversation_id).execute().data
        if conv:
            is_group = bool(conv[0].get("is_group"))
            group_name = conv[0].get("name")
    except Exception:
        pass

    # Diğer katılımcı(lar) — grupta birden fazla olabilir
    others = sb.table("conversation_participants").select(
        "user_id, is_admin, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).neq("user_id", me).eq("conversation_id", conversation_id).execute().data
    other_profiles = []
    for o in others:
        p = o.get("profiles")
        if p:
            other_profiles.append({**p, "is_admin": bool(o.get("is_admin"))})
    other_user = None if is_group else (other_profiles[0] if other_profiles else None)
    # Supabase Realtime INSERT payload'ı sadece ham satırı verir (join yok) —
    # grup sohbetinde yeni mesajın kimden geldiğini göstermek için client-side
    # bir id→username haritası gerekiyor (bkz. chat.js, data-member-map).
    member_map = {p["id"]: p["username"] for p in other_profiles if p.get("id")}

    messages = sb.table("messages").select(
        "*, profiles!messages_sender_id_fkey(username, avatar_url)"
    ).eq("conversation_id", conversation_id).order("created_at").execute().data
    if not is_group:
        # Grupta "okundu" bilgisi anlamsız (bkz. modül docstring'i) — sadece 1:1'de işaretlenir
        _mark_read(sb, conversation_id, me, messages)

    ctx = dict(messages=messages, other_user=other_user, is_group=is_group,
               group_name=group_name, group_members=other_profiles, member_map=member_map,
               conversation_id=conversation_id, me=session["user"],
               my_is_admin=my_is_admin if is_group else False)

    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("messages/_conversation_panel.html", **ctx)

    convos = _build_convos(sb, me)
    return render_template("messages/conversation.html", convos=convos,
                           active_id=conversation_id, **ctx)
