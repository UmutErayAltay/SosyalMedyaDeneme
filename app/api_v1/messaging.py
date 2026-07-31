from datetime import datetime, timezone

from flask import request, jsonify, current_app

from . import bp
from ._common import _str_field, api_login_required
from ..supabase_client import get_sb
from ..rate_limit import is_rate_limited
from ..blocks import blocked_user_ids, is_blocked_either_way
from ..storage_helper import upload_image
from ..cache import invalidate
from ..mentions import notify_mentions
from ..messaging._common import (
    _build_convos, _get_or_create_conversation, _mark_read,
    _mark_message_notifications_read, _notify_conversation,
)
from ..messaging.views import MESSAGE_PAGE, _write_pool
from ..notifications import notify


# ----------------------- MESAJLAŞMA (Faz 3, native Android mesajlaşma ekranı) -----------------------
# app/messaging/*.py'nin BİLİNÇLİ bir alt kümesi JSON'a taşınır: inbox +
# konuşma geçmişi (sayfalı) + metin/görsel mesajı gönderme (+reply_to) +
# get-or-create 1:1 konuşma başlatma + okundu işaretleme + grup oluşturma/
# yönetimi (Faz 4). KAPSAM DIŞI (hâlâ hiç dokunulmadı): mesaj düzenleme/
# silme/sabitleme/iletme/tepki, ses/sticker/GIF mesaj, WebRTC/LiveKit arama,
# Supabase Realtime (native taraf basit polling yapacak).

def _serialize_conversation_summary(c: dict) -> dict:
    """_common.py _build_convos() satırını JSON sözleşmesine çevirir.

    'unread' zaten _build_convos()'ta BOOL (mesaj değil SOHBET bazlı,
    gruplarda hep False — bkz. o dosyadaki unread_message_count docstring'i);
    burada ayrı bir sayaç İCAT edilmez, aynı anlam `has_unread` adıyla taşınır.
    """
    is_group = c["is_group"]
    other = c.get("other_user")
    last = c.get("last_message")
    return {
        "id": c["id"],
        "is_group": is_group,
        "name": c["name"] if is_group else (other.get("username") if other else None),
        "avatar_url": None if is_group else (other.get("avatar_url") if other else None),
        "last_message_preview": (last.get("content") or "")[:40] if last else None,
        "last_message_at": last.get("created_at") if last else None,
        "has_unread": c["unread"],
        # _build_convos() zaten hesaplıyor (grupta değilse None) — Faz 4 grup
        # yönetimi eklenirken inbox satırında "3 üye" gibi bir özet göstermek
        # için serileştirmeye eklendi, native eski sürümler bilinmeyen alanı
        # yok sayar (additive/geriye uyumlu).
        "member_count": c.get("member_count") if is_group else None,
    }


@bp.route("/messages/conversations")
@api_login_required
def api_message_conversations():
    """Gelen kutusu (inbox) — messaging/views.py inbox()'ın AYNI mantığı
    (paylaşılan _common.py _build_convos() üzerinden, ayrı bir sorgu/kural
    İCAT edilmedi), JSON döner. Sıralama zaten _build_convos() içinde son
    mesaj zamanına göre azalan yapılıyor.
    """
    sb = get_sb()
    me = request.api_user["id"]
    convos = _build_convos(sb, me)
    return jsonify(conversations=[_serialize_conversation_summary(c) for c in convos])


@bp.route("/messages/conversations/<conversation_id>")
@api_login_required
def api_message_conversation_detail(conversation_id):
    """Bir konuşmanın mesaj geçmişi — messaging/views.py conversation()'ın
    çekirdek mantığı (katılımcı doğrulama → 403; tepki/sticker embed +
    fallback; reply_to için self-join yerine TEK toplu IN sorgusu — hepsi
    o dosyadaki AYNI gerekçelerle).

    BİLİNÇLİ SAPMA: web'de numaralı sayfalama yok, tek bir "?all=1" bayrağı
    var (son MESSAGE_PAGE mesaj ya da TÜM geçmiş — DOM'da sonsuz kaydırma
    JS tarafında tutuluyor). Native tarafın sunucu-taraflı sayfalı sonsuz
    kaydırma yapması gerektiği için burada ?page= (1-tabanlı, en yeniden en
    eskiye offset) eklendi: page=1 = web'in varsayılan görünümüyle AYNI son
    MESSAGE_PAGE mesaj, page=2 bir önceki MESSAGE_PAGE, vb. has_more=True
    ise bir sonraki (daha eski) sayfa mevcuttur.

    Okundu işaretleme SADECE page=1 (en güncel sayfa) çekilince tetiklenir —
    "sohbeti açmak = okundu işaretlemek" web davranışıyla tutarlı; eski
    sayfaları (page>1) çekmek okundu durumunu DEĞİŞTİRMEZ.
    """
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        # conversation()'daki AYNI durum kodu (403) — abort() yerine jsonify
        # (JSON API'de HTML hata sayfası dönmesin diye)
        return jsonify(error="forbidden"), 403

    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * MESSAGE_PAGE

    try:
        conv_rows = sb.table("conversations").select("is_group, name").eq("id", conversation_id).execute().data
        is_group = bool(conv_rows[0].get("is_group")) if conv_rows else False
        group_name = conv_rows[0].get("name") if conv_rows else None
    except Exception:
        is_group, group_name = False, None

    other_user = None
    if not is_group:
        try:
            others_raw = sb.table("conversation_participants").select(
                "profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
            ).eq("conversation_id", conversation_id).neq("user_id", me).execute().data
            other_profiles = [o["profiles"] for o in others_raw if o.get("profiles")]
            other_user = other_profiles[0] if other_profiles else None
        except Exception:
            other_user = None

    # Mesajlar — views.py conversation()'daki _fetch_messages ile AYNI embed +
    # fallback deseni: embed'li select patlarsa sade select'e düşülür, sohbet
    # render'ı ASLA embed yüzünden kırılmaz.
    base = "*, profiles!messages_sender_id_fkey(username, avatar_url)"
    selects = (base + ", sticker:stickers(id, image_url), message_reactions(reaction, user_id)", base)
    rows = []
    for sel in selects:
        try:
            rows = sb.table("messages").select(sel).eq(
                "conversation_id", conversation_id
            ).order("created_at", desc=True).limit(MESSAGE_PAGE + 1).offset(offset).execute().data
            break
        except Exception:
            continue

    has_more = len(rows) > MESSAGE_PAGE
    rows = rows[:MESSAGE_PAGE]
    rows.reverse()  # istemci artan (eskiden yeniye) sırayla render etsin

    # Alıntı (reply_to) — TEK toplu IN sorgusu (self-join embed yön belirsizliği
    # yaratıyor, bkz. views.py conversation() yorumu — burada AYNI çözüm).
    reply_lookup = {}
    try:
        reply_ids = list({m["reply_to_id"] for m in rows if m.get("reply_to_id")})
        if reply_ids:
            quoted = sb.table("messages").select(
                "id, content, image_url, sender_id, profiles!messages_sender_id_fkey(username)"
            ).in_("id", reply_ids).execute().data
            reply_lookup = {q["id"]: q for q in quoted}
    except Exception:
        reply_lookup = {}

    for m in rows:
        m["reply_to"] = reply_lookup.get(m.get("reply_to_id"))
        raw_reactions = m.pop("message_reactions", None) or []
        by_react = {}
        for r in raw_reactions:
            by_react.setdefault(r["reaction"], []).append(r["user_id"])
        m["reactions"] = [
            {"reaction": react, "count": len(uids), "mine": me in uids}
            for react, uids in by_react.items()
        ]
        if "sticker" not in m:
            m["sticker"] = None

    if page == 1:
        # views.py conversation()'daki _write_reads() ile AYNI arka plan
        # deseni — yanıt bu yazmaları BEKLEMEZ (first_unread hesaplama gibi
        # bir ekran kaygısı JSON API'de yok, doğrudan arka plana atılır).
        if not is_group:
            _write_pool.submit(lambda: _mark_read(sb, conversation_id, me, rows))
        _write_pool.submit(lambda: _mark_message_notifications_read(sb, me, conversation_id))

    return jsonify(
        messages=rows,
        has_more=has_more,
        conversation={
            "id": conversation_id,
            "is_group": is_group,
            "name": group_name if is_group else (other_user.get("username") if other_user else None),
            "avatar_url": None if is_group else (other_user.get("avatar_url") if other_user else None),
        },
    )


@bp.route("/messages/conversations/<conversation_id>/send", methods=["POST"])
@api_login_required
def api_send_message(conversation_id):
    """Mesaj gönder — messaging/sending.py send_message()'ın metin+reply_to_id+
    GÖRSEL dalları port edildi (ses/sticker/GIF dalları BİLİNÇLİ olarak
    atlandı — ses RECORD_AUDIO izni+MediaRecorder gerektiriyor, sticker yeni
    bir katalog endpoint'i, GIF üçüncü-parti Klipy entegrasyonu istiyor, hepsi
    ayrı birer iterasyon).

    BİLİNÇLİ SAPMA: JSON'dan multipart/form-data'ya geçildi (api_create_post()
    ile AYNI kodlama deseni) — native henüz gerçek kullanıcıya çıkmadığı için
    (bkz. proje yol haritası) bu, geriye dönük uyumluluk kaygısı olmadan
    yapılabilecek bir sözleşme değişikliği.
    """
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select().eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    # send_message()'daki AYNI limit: kullanıcı bazlı (IP değil — aynı ev/ofisteki
    # birden fazla kişi birbirini kilitlemesin), dakikada 30 mesaj.
    if is_rate_limited(f"send_message:{me}", 30, 60):
        return jsonify(error="rate_limited"), 429

    # send_message()'daki AYNI engelleme kontrolü: konuşma bir engellemeden
    # ÖNCE başlamış olabilir, her gönderimde tekrar kontrol edilir.
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        return jsonify(error="blocked"), 403

    content = (request.form.get("content") or "").strip()
    reply_to_id = (request.form.get("reply_to_id") or "").strip() or None
    image_file = request.files.get("image")
    has_image = bool(image_file and image_file.filename)

    if not content and not has_image:
        return jsonify(error="empty"), 400

    image_url = None
    if has_image:
        image_url = upload_image(image_file, folder="messages")
        if not image_url:
            return jsonify(error="upload_failed"), 400

    if reply_to_id:
        # send_message()'daki AYNI doğrulama: farklı konuşmadansa/mevcut
        # değilse reply'siz gönder (sessizce, hata değil).
        reply_msg = sb.table("messages").select("id").eq("id", reply_to_id).eq(
            "conversation_id", conversation_id
        ).execute().data
        if not reply_msg:
            reply_to_id = None

    insert_data = {"conversation_id": conversation_id, "sender_id": me, "content": content, "image_url": image_url}
    try:
        # reply_to_id opsiyonel kolon — migration_message_reply.sql henüz
        # uygulanmamışsa kolonsuz insert'e düş (send_message()'daki AYNI desen).
        data_full = dict(insert_data)
        if reply_to_id:
            data_full["reply_to_id"] = reply_to_id
        inserted = sb.table("messages").insert(data_full).execute()
    except Exception:
        inserted = sb.table("messages").insert(insert_data).execute()

    # Bildirim + @etiketleme yanıtı BEKLETMESİN — send_message()'daki AYNI
    # arka plan deseni (test_request_context: url_for kullanan yardımcılar
    # bağlamsız thread'de RuntimeError fırlatmasın diye).
    app = current_app._get_current_object()
    allowed_ids = {o["user_id"] for o in others}

    def _bg_notify():
        try:
            with app.test_request_context():
                _notify_conversation(sb, conversation_id, me)
                notify_mentions(sb, actor_id=me, content=content,
                                 conversation_id=conversation_id, allowed_ids=allowed_ids)
        except Exception:
            pass

    _write_pool.submit(_bg_notify)

    return jsonify(message=inserted.data[0])


@bp.route("/messages/start/<username>", methods=["POST"])
@api_login_required
def api_start_conversation(username):
    """Yeni 1:1 konuşma başlat (get-or-create) — messaging/creation.py
    start_conversation()'ın AYNI mantığı (_get_or_create_conversation() zaten
    grup konuşmalarını kesişimden eleyip SADECE 1:1 döndürüyor)."""
    sb = get_sb()
    me = request.api_user["id"]

    target = sb.table("profiles").select("id").eq("username", username).execute()
    if not target.data:
        return jsonify(error="not_found"), 404
    target_id = target.data[0]["id"]

    if is_blocked_either_way(sb, me, target_id):
        return jsonify(error="blocked"), 403

    cid = _get_or_create_conversation(me, target_id)
    return jsonify(conversation_id=cid)


@bp.route("/messages/conversations/<conversation_id>/mark-read", methods=["POST"])
@api_login_required
def api_mark_conversation_read(conversation_id):
    """Konuşmayı okundu işaretle — messaging/views.py
    mark_conversation_read()'in AYNI mantığı (katılımcı değilse 404 —
    kaynak fonksiyonla BİREBİR aynı durum kodu; conversation() endpoint'inin
    403'ünden BİLEREK farklı, ikisi de kendi kaynağıyla tutarlı)."""
    sb = get_sb()
    me = request.api_user["id"]
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="not_found"), 404

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        sb.table("messages").update({"read_at": now_iso}).eq(
            "conversation_id", conversation_id
        ).neq("sender_id", me).is_("read_at", "null").execute()
        invalidate(f"unread_msgs:{me}")
    except Exception:
        pass  # read_at migration'ı yoksa sessizce atla (mark_conversation_read ile aynı tavır)

    _mark_message_notifications_read(sb, me, conversation_id)
    return jsonify(ok=True)


# --- Grup sohbeti yönetimi (Faz 4, native Android) ---
# app/messaging/creation.py create_group() + app/messaging/group_admin.py'nin
# TAMAMI (rename/üye ekle-çıkar/admin toggle/ayrılma) BİREBİR aynı iş mantığıyla
# JSON'a taşınır — yeni bir kural İCAT edilmez, sadece jsonify/durum kodu
# sözleşmesi native'e uygun hale getirilir.

def _api_is_group_admin(sb, conversation_id: str, user_id: str) -> bool:
    """group_admin.py _is_admin()'in kopyası (import değil — o dosya bir web
    Blueprint'i, route-seviyesi yardımcılar burada mevcut desene uyup
    kopyalanır, sadece _common.py'deki paylaşılan yardımcılar import edilir).

    Service-role RLS'i bypass ettiğinden bu kontrol DB'den her admin-gated
    route'ta TAZE yapılır — token'ın kime ait olduğu bilgisine güvenilmez.
    """
    row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    return bool(row) and bool(row[0].get("is_admin"))


@bp.route("/messages/group/new", methods=["POST"])
@api_login_required
def api_create_group():
    """Yeni grup sohbeti — creation.py create_group()'ın AYNI mantığı
    (isim + kendisi hariç en az 2 üye, engellenen biri varsa 403,
    migration uygulanmamışsa insert patlar -> 503)."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    name = _str_field(data, "name")
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))

    if not name:
        return jsonify(error="missing_name"), 400
    if len(user_ids) < 2:
        return jsonify(error="too_few_members"), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="blocked"), 403

    try:
        conv = sb.table("conversations").insert({
            "is_group": True, "name": name, "created_by": me,
        }).execute()
    except Exception:
        return jsonify(error="groups_not_available"), 503
    cid = conv.data[0]["id"]

    # Grubu oluşturan otomatik yönetici olur, davet edilenler değil
    # (create_group()'daki AYNI kural, created_by ile tutarlı).
    sb.table("conversation_participants").insert(
        [{"conversation_id": cid, "user_id": me, "is_admin": True}]
        + [{"conversation_id": cid, "user_id": uid, "is_admin": False} for uid in user_ids]
    ).execute()

    for uid in user_ids:
        notify(sb, recipient_id=uid, actor_id=me, type_="message", conversation_id=cid)

    return jsonify(conversation_id=cid)


@bp.route("/messages/group/<conversation_id>/rename", methods=["POST"])
@api_login_required
def api_rename_group(conversation_id):
    """Grup adını değiştirir — group_admin.py rename_group() ile AYNI mantık.

    Çağıran gruba hiç katılımcı değilse de _api_is_group_admin() False döner
    (satır yok) — outsider ve "admin olmayan üye" AYNI 403 not_admin'i alır,
    detail/members/send'deki forbidden davranışıyla tutarlı.
    """
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    name = _str_field(data, "name")
    if not name:
        return jsonify(error="missing_name"), 400

    sb.table("conversations").update({"name": name}).eq("id", conversation_id).execute()
    return jsonify(ok=True, name=name)


def _serialize_group_member(row: dict) -> dict:
    """conversation_participants + profiles join satırını JSON üye sözleşmesine çevirir."""
    p = row.get("profiles") or {}
    return {
        "id": p.get("id"),
        "username": p.get("username"),
        "avatar_url": p.get("avatar_url"),
        "is_admin": bool(row.get("is_admin")),
    }


@bp.route("/messages/group/<conversation_id>/members")
@api_login_required
def api_group_members(conversation_id):
    """Grup üye listesi + is_admin bayrağı — web'de doğrudan karşılığı yok,
    native'in "Grubu Yönet" ekranı için EKLENDİ (bkz. görev tarifi). Çağıran
    katılımcı değilse diğer endpoint'lerle (detail/send) AYNI 403 forbidden."""
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    rows = sb.table("conversation_participants").select(
        "is_admin, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
    ).eq("conversation_id", conversation_id).execute().data
    members = [_serialize_group_member(r) for r in rows]
    members.sort(key=lambda m: (not m["is_admin"], (m["username"] or "").lower()))
    return jsonify(members=members)


@bp.route("/messages/group/<conversation_id>/members/add", methods=["POST"])
@api_login_required
def api_add_group_members(conversation_id):
    """Gruba üye ekler — group_admin.py add_group_members() ile AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    user_ids = [u for u in data.get("user_ids", []) if isinstance(u, str)]
    user_ids = list(dict.fromkeys(uid for uid in user_ids if uid != me))
    if not user_ids:
        return jsonify(error="missing_user_ids"), 400

    blocked_ids = blocked_user_ids(sb, me)
    if any(uid in blocked_ids for uid in user_ids):
        return jsonify(error="blocked"), 403

    # Zaten üye olanları tekrar eklemek primary key ihlaline yol açar
    # (add_group_members()'daki AYNI ön-filtre, grup üye sayısı küçük).
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


@bp.route("/messages/group/<conversation_id>/members/<user_id>/remove", methods=["POST"])
@api_login_required
def api_remove_group_member(conversation_id, user_id):
    """Üyeyi gruptan çıkarır — group_admin.py remove_group_member() ile AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403
    if user_id == me:
        return jsonify(error="cannot_remove_self"), 400

    target = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="not_a_member"), 404

    sb.table("conversation_participants").delete().eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True)


@bp.route("/messages/group/<conversation_id>/members/<user_id>/toggle-admin", methods=["POST"])
@api_login_required
def api_toggle_group_admin(conversation_id, user_id):
    """Üyenin yöneticilik durumunu tersine çevirir — group_admin.py
    toggle_group_admin() ile AYNI mantık (tek admin kendini düşüremez)."""
    sb = get_sb()
    me = request.api_user["id"]
    if not _api_is_group_admin(sb, conversation_id, me):
        return jsonify(error="not_admin"), 403

    target = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute().data
    if not target:
        return jsonify(error="not_a_member"), 404

    new_value = not bool(target[0].get("is_admin"))

    if user_id == me and not new_value:
        # Grup admin'siz kalmasın diye kendini düşürmeden önce başka bir
        # admin olduğundan emin olunur (toggle_group_admin()'daki AYNI kontrol).
        other_admins = sb.table("conversation_participants").select("user_id").eq(
            "conversation_id", conversation_id
        ).eq("is_admin", True).neq("user_id", me).execute().data
        if not other_admins:
            return jsonify(error="last_admin"), 400

    sb.table("conversation_participants").update({"is_admin": new_value}).eq(
        "conversation_id", conversation_id
    ).eq("user_id", user_id).execute()
    return jsonify(ok=True, is_admin=new_value)


@bp.route("/messages/group/<conversation_id>/leave", methods=["POST"])
@api_login_required
def api_leave_group(conversation_id):
    """Gruptan ayrılır — group_admin.py leave_group() ile AYNI mantık
    (atomik RPC: ayrılma + boş grup temizliği + tek adminse admin devri,
    hepsi tek transaction'da — bkz. sql/migration_leave_group_atomic_rpc.sql)."""
    sb = get_sb()
    me = request.api_user["id"]

    me_row = sb.table("conversation_participants").select("is_admin").eq(
        "conversation_id", conversation_id
    ).eq("user_id", me).execute().data
    if not me_row:
        return jsonify(error="not_a_member"), 404

    sb.rpc("leave_group_and_reassign_admin", {
        "p_conversation_id": conversation_id, "p_user_id": me
    }).execute()

    return jsonify(ok=True)
