"""Birebir mesajlaşma (DM) + grup sohbeti + Paylaşım özellikleri.

Model: bir 'conversation' satırı, conversation_participants (many-to-many)
üzerinden N kullanıcıya bağlanır — 1:1 DM de grup sohbeti de AYNI şema,
sadece conversations.is_group/name grup meta bilgisini taşır (bkz.
sql/migration_group_chat.sql). Görsel mesajları, tekli post paylaşma ve
çoklu post paylaşma desteklenir.

Grup sohbetinde okundu bilgisi (✓✓) BİLEREK gösterilmiyor — messages.read_at
tek bir kolon, "kim okudu" bilgisini tutamaz (N kişiden biri okuyunca ✓✓
göstermek yanıltıcı olurdu). 1:1'de mevcut davranış aynen korunuyor.
"""
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image
from .notifications import notify
from .blocks import is_blocked_either_way, blocked_user_ids

bp = Blueprint("messaging", __name__)


def _mark_read(sb, conversation_id: str, me: str, messages: list[dict]) -> None:
    """Karşı tarafın (henüz okunmamış) mesajlarını okundu işaretler.

    sql/migration_read_receipts.sql çalıştırılmadan `read_at` kolonu yoksa
    PostgREST hata döner — bu durumda sessizce atlanır (konuşma sayfası
    migration uygulanana kadar da çalışmaya devam etsin diye).
    """
    unread_ids = [m["id"] for m in messages if m["sender_id"] != me and not m.get("read_at")]
    if not unread_ids:
        return
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        sb.table("messages").update({"read_at": now_iso}).in_("id", unread_ids).execute()
    except Exception:
        pass


def _notify_conversation(sb, conversation_id: str, sender_id: str) -> None:
    """Konuşmadaki diğer katılımcı(lar)a yeni mesaj bildirimi gönderir."""
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", sender_id).execute().data
    for o in others:
        notify(sb, recipient_id=o["user_id"], actor_id=sender_id,
               type_="message", conversation_id=conversation_id)


def _get_or_create_conversation(me_id: str, target_id: str) -> str:
    """İki kullanıcı arasındaki konuşmayı bulur veya yenisini oluşturur, ID'sini döner."""
    sb = get_sb()
    my_convs = sb.table("conversation_participants").select("conversation_id").eq("user_id", me_id).execute().data
    target_convs = sb.table("conversation_participants").select("conversation_id").eq("user_id", target_id).execute().data
    
    my_ids = {c["conversation_id"] for c in my_convs}
    target_ids = {c["conversation_id"] for c in target_convs}
    shared = my_ids & target_ids

    if shared:
        return shared.pop()
    else:
        new_conv = sb.table("conversations").insert({}).execute()
        cid = new_conv.data[0]["id"]
        sb.table("conversation_participants").insert([
            {"conversation_id": cid, "user_id": me_id},
            {"conversation_id": cid, "user_id": target_id},
        ]).execute()
        return cid


def _build_convos(sb, me: str) -> list[dict]:
    """Kullanıcının konuşma listesini (diğer katılımcı + son mesaj) döner.

    inbox() ve conversation() arasında paylaşılır — iki panelli düzende her
    ikisi de sol taraftaki aynı listeyi render eder.
    """
    parts = sb.table("conversation_participants").select(
        "conversation_id"
    ).eq("user_id", me).execute().data
    cids = [p["conversation_id"] for p in parts]

    convos = []
    if cids:
        # Grup meta bilgisi (is_group/name) — sql/migration_group_chat.sql
        # henüz uygulanmamışsa kolonlar yok, hepsi 1:1 gibi davranılır.
        conv_meta = {}
        try:
            rows = sb.table("conversations").select("id, is_group, name").in_("id", cids).execute().data
            conv_meta = {r["id"]: r for r in rows}
        except Exception:
            pass

        # Diğer katılımcılar tek sorguda (konuşma başına ayrı sorgu yerine).
        # Grup sohbetinde birden fazla "diğer katılımcı" olabilir, bu yüzden
        # cid başına LİSTE olarak toplanır (1:1'de tek elemanlı olur).
        others = sb.table("conversation_participants").select(
            "conversation_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
        ).in_("conversation_id", cids).neq("user_id", me).execute().data
        others_by_cid: dict = {}
        for o in others:
            others_by_cid.setdefault(o["conversation_id"], []).append(o.get("profiles"))

        # Son mesajlar tek sorguda: yeni → eski çekilir, konuşma başına
        # ilk görülen kayıt son mesajdır. limit, tüm konuşmaların son
        # mesajını kapsayacak kadar geniş tutulur.
        msgs = sb.table("messages").select(
            "*, profiles!messages_sender_id_fkey(username, avatar_url)"
        ).in_("conversation_id", cids).order(
            "created_at", desc=True
        ).limit(max(len(cids) * 30, 300)).execute().data
        last_by_cid = {}
        for m in msgs:
            last_by_cid.setdefault(m["conversation_id"], m)

        convos = []
        for cid in cids:
            meta = conv_meta.get(cid, {})
            is_group = bool(meta.get("is_group"))
            other_list = others_by_cid.get(cid, [])
            convos.append({
                "id": cid,
                "last_message": last_by_cid.get(cid),
                "other_user": None if is_group else (other_list[0] if other_list else None),
                "is_group": is_group,
                "name": meta.get("name") if is_group else None,
                "member_count": len(other_list) + 1 if is_group else None,
            })

    convos.sort(key=lambda c: c["last_message"]["created_at"]
                if c["last_message"] else "", reverse=True)
    return convos


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
        "user_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).neq("user_id", me).eq("conversation_id", conversation_id).execute().data
    other_profiles = [o["profiles"] for o in others if o.get("profiles")]
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
               conversation_id=conversation_id, me=session["user"])

    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("messages/_conversation_panel.html", **ctx)

    convos = _build_convos(sb, me)
    return render_template("messages/conversation.html", convos=convos,
                           active_id=conversation_id, **ctx)


@bp.route("/<conversation_id>/send", methods=["POST"])
@login_required
@retry_on_connection_error
def send_message(conversation_id):
    sb = get_sb()
    me = session["user"]["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute()
    if not part.data:
        abort(403)

    content = request.form.get("content", "").strip()
    image_file = request.files.get("image")
    has_image = image_file and image_file.filename
    wants_json = "application/json" in request.headers.get("Accept", "")

    # Engelleme: konuşma bir engellemeden ÖNCE başlamış olabilir — her mesaj
    # gönderiminde diğer katılımcı(lar)la aramda bir engelleme var mı kontrol et.
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        if wants_json:
            return jsonify({"error": "blocked"}), 403
        flash("Bu kullanıcıyla mesajlaşamazsın.", "error")
        return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    if not content and not has_image:
        if wants_json:
            return jsonify({"error": "empty"}), 400
        return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    image_url = None
    if has_image:
        image_url = upload_image(image_file, folder="messages")
        if not image_url:
            if wants_json:
                return jsonify({"error": "upload_failed"}), 400
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

    inserted = sb.table("messages").insert({
        "conversation_id": conversation_id,
        "sender_id": me,
        "content": content,
        "image_url": image_url,
    }).execute()
    _notify_conversation(sb, conversation_id, me)

    if wants_json:
        return jsonify(inserted.data[0])
    return redirect(url_for("messaging.conversation", conversation_id=conversation_id))


@bp.route("/<conversation_id>/share-post/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def share_post(conversation_id, post_id):
    sb = get_sb()
    me = session["user"]["id"]

    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        abort(403)

    # Postu, görselleriyle birlikte çek
    post = sb.table("posts").select(
        "id, content, image_url, image_urls, profiles!posts_user_id_fkey(username)"
    ).eq("id", post_id).execute().data
    
    if not post: abort(404)
    p = post[0]

    # İlk görseli al (öncelik image_urls dizisindeyse onu kullan)
    first_img = None
    if p.get("image_urls") and len(p["image_urls"]) > 0:
        first_img = p["image_urls"][0]
    elif p.get("image_url"):
        first_img = p["image_url"]

    # Mesajın metin kısmını hazırla
    share_text = f"📎 Post paylaştı: @{p['profiles']['username']}\n{p['content'][:50]}..."
    
    # Veritabanına kaydet
    sb.table("messages").insert({
        "conversation_id": conversation_id,
        "sender_id": me,
        "content": share_text,
        "image_url": first_img, # Görseli buraya ekliyoruz!
    }).execute()
    _notify_conversation(sb, conversation_id, me)

    return redirect(url_for("messaging.conversation", conversation_id=conversation_id))

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

    all_members = [me] + user_ids
    sb.table("conversation_participants").insert([
        {"conversation_id": cid, "user_id": uid} for uid in all_members
    ]).execute()

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


@bp.route("/share/<post_id>", methods=["POST"])
@login_required
@retry_on_connection_error
def share_post_multiple(post_id):
    """Postu seçili birden fazla kullanıcıya DM olarak gönderir (yeni modal yöntemi)."""
    me = session["user"]["id"]
    sb = get_sb()
    data = request.get_json()
    
    user_ids = data.get("user_ids", [])
    note = data.get("note", "").strip()

    if not user_ids:
        return jsonify({"error": "Kullanıcı seçilmedi"}), 400

    # Post önizlemesini çek (GÖRSELLERİ DE DAHİL ETTİK)
    post = sb.table("posts").select(
        "id, content, image_url, image_urls, profiles!posts_user_id_fkey(username)"
    ).eq("id", post_id).execute().data
    
    if not post:
        return jsonify({"error": "Post bulunamadı"}), 404
    post_data = post[0]

    # Postun ilk görselini bul
    post_image = None
    if post_data.get("image_urls") and len(post_data["image_urls"]) > 0:
        post_image = post_data["image_urls"][0]
    elif post_data.get("image_url"):
        post_image = post_data["image_url"]

    share_text = note + "\n\n" if note else ""
    share_text += f"📎 Paylaşılan post: /post/{post_id}\n\"{post_data['content'][:100]}\""
    if post_data.get("profiles"):
        share_text += f"\n— @{post_data['profiles']['username']}"

    sent_count = 0
    for target_id in set(user_ids):
        cid = _get_or_create_conversation(me, target_id)
        sb.table("messages").insert({
            "conversation_id": cid,
            "sender_id": me,
            "content": share_text.strip(),
            "image_url": post_image
        }).execute()
        _notify_conversation(sb, cid, me)
        sent_count += 1

    return jsonify({"sent": sent_count})