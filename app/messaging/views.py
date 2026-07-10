"""Konuşma listesi (inbox) + tek bir konuşmanın görüntülenmesi."""
from concurrent.futures import ThreadPoolExecutor
from flask import render_template, request, session, abort, jsonify
from . import bp
from ._common import _mark_read, _build_convos, unread_message_count, mark_active
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..auth import refresh_session_tokens


@bp.route("/")
@login_required
@retry_on_connection_error
def inbox():
    """Konuşma listesi (en son mesaja göre sıralı). Sağ panel boş/placeholder."""
    sb = get_sb()
    me = session["user"]["id"]
    # Realtime kurulumu taze access token'la yapılmalı (bkz. _realtime_init.html)
    refresh_session_tokens()
    convos = _build_convos(sb, me)
    return render_template("messages/inbox.html", convos=convos, active_id=None,
                           me=session["user"])


@bp.route("/unread-count")
@login_required
@retry_on_connection_error
def unread_count():
    """Navbar rozeti için polling ucu — notifications.js'teki desenin aynısı."""
    sb = get_sb()
    me = session["user"]["id"]
    return jsonify({"count": unread_message_count(sb, me)})


@bp.route("/<conversation_id>")
@login_required
@retry_on_connection_error
def conversation(conversation_id):
    """Tek bir konuşmanın mesaj akışı.

    AJAX isteği (sol listeden tıklanınca, X-Requested-With: fetch) ise sadece
    sağ paneli döner — messagesPanel.js bunu tam sayfa yenilemeden DOM'a enjekte
    eder. Normal (tam sayfa) istekte ise sol liste + sağ panel birlikte render edilir.
    """
    # Tam sayfa render'da Realtime taze token'la kurulur (bkz. _realtime_init.html)
    if request.headers.get("X-Requested-With") != "fetch":
        refresh_session_tokens()
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
    # Sayfa açılır açılmaz "aktif" işaretle (chat.js periyodik ping ile tazeler) —
    # bu sohbetten gelen mesaj bildirimi/push'u üretilmesin (bkz. _notify_conversation)
    mark_active(me, conversation_id)

    # Diğer veri (grup meta, katılımcılar, mesajlar) paralel çek — conversation_id
    # doğrulandıktan sonra 3 sorgu birbirinden bağımsız
    def _fetch_conv_meta():
        try:
            conv = sb.table("conversations").select("is_group, name").eq("id", conversation_id).execute().data
            if conv:
                return bool(conv[0].get("is_group")), conv[0].get("name")
        except Exception:
            pass
        return False, None

    def _fetch_others():
        # Tek geçici hata başlığı 'Bilinmeyen' yapıyordu — bir kez yeniden dene
        for attempt in (0, 1):
            try:
                others = sb.table("conversation_participants").select(
                    "user_id, is_admin, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
                ).neq("user_id", me).eq("conversation_id", conversation_id).execute().data
                other_profiles = []
                for o in others:
                    p = o.get("profiles")
                    if p:
                        other_profiles.append({**p, "is_admin": bool(o.get("is_admin"))})
                return other_profiles
            except Exception:
                if attempt:
                    return []

    def _fetch_messages():
        try:
            messages = sb.table("messages").select(
                "*, profiles!messages_sender_id_fkey(username, avatar_url)"
            ).eq("conversation_id", conversation_id).order("created_at").execute().data
            return messages
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=3) as executor:
        meta_future = executor.submit(_fetch_conv_meta)
        others_future = executor.submit(_fetch_others)
        messages_future = executor.submit(_fetch_messages)

        (is_group, group_name) = meta_future.result()
        other_profiles = others_future.result()
        messages = messages_future.result()

    # Tepkileri bağla — message_reactions tablosu henüz yoksa sessizce boş liste
    try:
        if messages:
            msg_ids = [m["id"] for m in messages]
            reactions_raw = sb.table("message_reactions").select(
                "message_id, reaction, user_id"
            ).in_("message_id", msg_ids).execute().data

            # Gruplama: {message_id: {reaction: [user_ids]}}
            reactions_by_msg = {}
            for r in reactions_raw:
                mid = r["message_id"]
                react = r["reaction"]
                uid = r["user_id"]
                if mid not in reactions_by_msg:
                    reactions_by_msg[mid] = {}
                if react not in reactions_by_msg[mid]:
                    reactions_by_msg[mid][react] = []
                reactions_by_msg[mid][react].append(uid)

            # Mesajlara bağla
            for m in messages:
                m["reactions"] = []
                if m["id"] in reactions_by_msg:
                    for reaction, user_ids in reactions_by_msg[m["id"]].items():
                        m["reactions"].append({
                            "reaction": reaction,
                            "count": len(user_ids),
                            "mine": me in user_ids
                        })
        else:
            # Boş mesaj listesi
            for m in messages:
                m["reactions"] = []
    except Exception:
        # message_reactions tablosu henüz oluşturulmamış — tepkiler boş liste
        for m in messages:
            m["reactions"] = []

    # Sticker'ları bağla — sticker_id sütunu henüz yoksa sessizce boş
    try:
        if messages:
            sticker_ids = [m.get("sticker_id") for m in messages if m.get("sticker_id")]
            stickers_by_id = {}
            if sticker_ids:
                stickers_raw = sb.table("stickers").select(
                    "id, image_url"
                ).in_("id", sticker_ids).execute().data
                for s in stickers_raw:
                    stickers_by_id[s["id"]] = {"id": s["id"], "image_url": s["image_url"]}

            for m in messages:
                if m.get("sticker_id") and m.get("sticker_id") in stickers_by_id:
                    m["sticker"] = stickers_by_id[m["sticker_id"]]
                else:
                    m["sticker"] = None
    except Exception:
        # stickers tablosu henüz oluşturulmamış — sticker'lar None
        for m in messages:
            m["sticker"] = None

    other_user = None if is_group else (other_profiles[0] if other_profiles else None)
    # Supabase Realtime INSERT payload'ı sadece ham satırı verir (join yok) —
    # grup sohbetinde yeni mesajın kimden geldiğini göstermek için client-side
    # bir id→username haritası gerekiyor (bkz. chat.js, data-member-map).
    member_map = {p["id"]: p["username"] for p in other_profiles if p.get("id")}

    # Okunmamış ilk mesaj — _mark_read read_at'i güncellemeden ÖNCE hesaplanmalı;
    # template bu mesaja çapa koyar, chat.js sohbeti oradan başlatır (yoksa en alttan)
    first_unread_id = None
    if not is_group:
        for m in messages:
            if m.get("sender_id") != me and not m.get("read_at"):
                first_unread_id = m["id"]
                break
        # Grupta "okundu" bilgisi anlamsız (bkz. modül docstring'i) — sadece 1:1'de işaretlenir
        _mark_read(sb, conversation_id, me, messages)

    ctx = dict(messages=messages, first_unread_id=first_unread_id,
               other_user=other_user, is_group=is_group,
               group_name=group_name, group_members=other_profiles, member_map=member_map,
               conversation_id=conversation_id, me=session["user"],
               my_is_admin=my_is_admin if is_group else False)

    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("messages/_conversation_panel.html", **ctx)

    convos = _build_convos(sb, me)
    return render_template("messages/conversation.html", convos=convos,
                           active_id=conversation_id, **ctx)


@bp.route("/<conversation_id>/mark-read", methods=["POST"])
@login_required
@retry_on_connection_error
def mark_conversation_read(conversation_id):
    """Sohbet AÇIKKEN Realtime ile düşen mesajı anında okundu işaretler
    (chat.js INSERT handler'ı çağırır) — yoksa rozet sohbetteyken bile
    birikiyordu. Katılımcı olmayan 404 (enumeration koruması)."""
    from datetime import datetime, timezone
    sb = get_sb()
    me = session["user"]["id"]
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        abort(404)
    try:
        sb.table("messages").update(
            {"read_at": datetime.now(timezone.utc).isoformat()}
        ).eq("conversation_id", conversation_id).neq(
            "sender_id", me).is_("read_at", "null").execute()
    except Exception:
        pass  # read_at migration'ı yoksa sessizce atla (_mark_read ile aynı tavır)
    return jsonify(ok=True)


@bp.route("/<conversation_id>/active", methods=["POST"])
@login_required
@retry_on_connection_error
def mark_conversation_active(conversation_id):
    """Sohbet ekranı açık kaldığı sürece chat.js'in periyodik çağırdığı 'nabız'.

    Bu, sunucu tarafında hangi kullanıcının hangi sohbeti şu an açık
    tuttuğunu bilmemizi sağlar (bkz. _common.mark_active/is_active_in) —
    o sohbetten gelen yeni mesaj bildirimini/push'unu bastırmak için
    kullanılır. Katılımcı olmayan 404 (enumeration koruması)."""
    sb = get_sb()
    me = session["user"]["id"]
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        abort(404)
    mark_active(me, conversation_id)
    return jsonify(ok=True)
