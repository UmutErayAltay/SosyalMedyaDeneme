"""Birebir mesajlaşma (DM) ve Paylaşım özellikleri.

Model: her iki kullanıcı için ortak bir 'conversation' satırı.
conversation_participants üzerinden kullanıcı ↔ konuşma eşleşmesi.
Görsel mesajları, tekli post paylaşma ve çoklu post paylaşma desteklenir.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image
from .notifications import notify

bp = Blueprint("messaging", __name__)


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
        # Diğer katılımcılar tek sorguda (konuşma başına ayrı sorgu yerine)
        others = sb.table("conversation_participants").select(
            "conversation_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
        ).in_("conversation_id", cids).neq("user_id", me).execute().data
        other_by_cid = {o["conversation_id"]: o.get("profiles") for o in others}

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

        convos = [{
            "id": cid,
            "last_message": last_by_cid.get(cid),
            "other_user": other_by_cid.get(cid),
        } for cid in cids]

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

    # Diğer katılımcı
    other = sb.table("conversation_participants").select(
        "user_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).neq("user_id", me).eq("conversation_id", conversation_id).execute()
    other_user = other.data[0]["profiles"] if other.data else None

    messages = sb.table("messages").select(
        "*, profiles!messages_sender_id_fkey(username, avatar_url)"
    ).eq("conversation_id", conversation_id).order("created_at").execute().data

    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("messages/_conversation_panel.html",
                               messages=messages, other_user=other_user,
                               conversation_id=conversation_id, me=session["user"])

    convos = _build_convos(sb, me)
    return render_template("messages/conversation.html",
                           messages=messages, other_user=other_user,
                           conversation_id=conversation_id, convos=convos,
                           active_id=conversation_id, me=session["user"])


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

    cid = _get_or_create_conversation(me, target_id)
    return redirect(url_for("messaging.conversation", conversation_id=cid))


@bp.route("/share-targets")
@login_required
@retry_on_connection_error
def share_targets():
    """Modal açıldığında veya arama yapıldığında kullanıcı listesini döner."""
    sb = get_sb()
    me = session["user"]["id"]
    q = request.args.get("q", "").strip()

    if q:
        if len(q) < 2:
            return jsonify([])
        # Arama yapıldığında eşleşen kullanıcıları getir (kendisi hariç)
        users = sb.table("profiles").select("id, username, avatar_url").ilike("username", f"%{q}%").neq("id", me).limit(20).execute().data
        return jsonify(users)

    # Varsayılan: Takip edilen kullanıcıları getir
    follows = sb.table("follows").select(
        "profiles!follows_following_id_fkey(id, username, avatar_url)"
    ).eq("follower_id", me).execute().data

    # Tekrar edenleri temizle
    user_dict = {}
    for f in follows:
        if f.get("profiles"):
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