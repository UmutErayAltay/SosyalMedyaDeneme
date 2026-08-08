import os
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from flask import request, jsonify, current_app
from livekit import api as livekit_api

from . import bp
from ._common import _str_field, api_login_required
from ..supabase_client import get_sb
from ..rate_limit import is_rate_limited
from ..blocks import blocked_user_ids, is_blocked_either_way
from ..storage_helper import upload_image, upload_video
from ..cache import invalidate
from ..mentions import notify_mentions
from ..messaging._common import (
    _build_convos, _get_or_create_conversation, _mark_read,
    _mark_message_notifications_read, _notify_conversation,
)
from ..messaging.views import MESSAGE_PAGE, _write_pool
from ..realtime_topics import realtime_topic
from ..notifications import notify
from ..gifs import is_valid_klipy_url


# ----------------------- MESAJLAŞMA (Faz 3, native Android mesajlaşma ekranı) -----------------------
# app/messaging/*.py'nin BİLİNÇLİ bir alt kümesi JSON'a taşınır: inbox +
# konuşma geçmişi (sayfalı) + metin/görsel/sticker/GIF mesajı gönderme
# (+reply_to) + get-or-create 1:1 konuşma başlatma + okundu işaretleme + grup
# oluşturma/yönetimi (Faz 4) + mesaj gelişmiş işlemleri (Faz 5: düzenle/sil/
# tepki/sabitle/ilet/sohbeti sessize al/arama) + sticker/GIF mesaj (Faz 5
# Dalga 3B, sending.py send_message()'ın AYNI mantığı) + grup sesli/görüntülü
# arama token'ı (group_calls.py call_token()'ın AYNI mantığı, aşağıda
# api_call_token). KAPSAM DIŞI (hâlâ hiç dokunulmadı): ses mesajı (RECORD_AUDIO
# izni gerektiriyor).

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
        # Sessize alma (Faz 5): _build_convos() bunu ZATEN hesaplıyor
        # (my_parts -> is_muted), sadece serileştirmeye eklendi — inbox
        # satırında sessiz-zil ikonu gösterilebilsin diye. Kolon/migration
        # yoksa _build_convos() zaten False döner.
        "is_muted": bool(c.get("is_muted")),
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
            # Sessize alma durumu (Faz 5) — EK SORGU GEREKMEZ: yukarıdaki
            # katılımcı doğrulaması zaten `.select()` (tüm kolonlar) ile
            # satırı çekmiş durumda. is_muted kolonu (migration_conversation_
            # mute.sql) yoksa .get() None döner -> False.
            "is_muted": bool(part[0].get("is_muted")),
            # Karşı tarafın arama kanalı adı (tahmin edilemez HMAC) — web'in
            # data-other-call-topic'iyle AYNI gerekçe (bkz. app/realtime_topics.py
            # ve .context/active_context.md "2026-08-07 devam 6/7"). SADECE bu
            # route'un yetki kontrolünden (yukarıdaki `part` katılımcılık
            # sorgusu) geçmiş kullanıcıya döner — grup sohbetinde None (1:1
            # dışında arama yok).
            "other_call_topic": (
                realtime_topic("calls", other_user["id"]) if other_user else None
            ),
        },
    )


@bp.route("/messages/conversations/<conversation_id>/send", methods=["POST"])
@api_login_required
def api_send_message(conversation_id):
    """Mesaj gönder — messaging/sending.py send_message()'ın metin+reply_to_id+
    GÖRSEL+sticker+GIF dalları port edildi (ses dalı hâlâ bilerek atlandı —
    RECORD_AUDIO izni+MediaRecorder gerektiriyor, ayrı bir iterasyon; video
    eklendi, 2026-08-08).
    `sticker_id` (form, /stickers/* katalogundan — app/api_v1/stickers.py) ve
    `gif_url` (form, Klipy CDN'inden) opsiyonel — send_message()'daki AYNI
    kurallar: sticker_id `stickers` tablosunda yoksa sessizce None'a düşer,
    gif_url `is_valid_klipy_url()` geçmezse yok sayılır, GIF ayrı bir kolona
    DEĞİL (messages'ta gif_url kolonu yok) `image_url`'e yazılır (görsel
    yoksa). `video` (form, multipart dosya) — `upload_video()` ile AYNI
    MIME+magic-byte+boyut doğrulaması (postlar/reels/stories'teki gibi),
    ayrı `video_url` kolonuna yazılır (image_url ile karışmaz, biri diğerini
    override ETMEZ — bir mesajda hem görsel hem video teorik olarak birlikte
    olabilir).

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
    video_file = request.files.get("video")
    has_video = bool(video_file and video_file.filename)
    sticker_id = (request.form.get("sticker_id") or "").strip() or None
    gif_url = (request.form.get("gif_url") or "").strip() or None

    # Sticker var mı kontrol et — send_message()'daki AYNI tolerans: tablo
    # yoksa veya id geçersizse hata DEĞİL, sessizce yok say.
    if sticker_id:
        try:
            sticker = sb.table("stickers").select("id").eq("id", sticker_id).execute()
            if not sticker.data:
                sticker_id = None
        except Exception:
            sticker_id = None

    # GIF URL kontrolü — sadece Klipy'den kabul et (send_message()'daki AYNI kural)
    if gif_url and not is_valid_klipy_url(gif_url):
        gif_url = None

    if not content and not has_image and not sticker_id and not gif_url and not has_video:
        return jsonify(error="empty"), 400

    image_url = None
    if has_image:
        image_url = upload_image(image_file, folder="messages")
        if not image_url:
            return jsonify(error="upload_failed"), 400

    video_url = None
    if has_video:
        video_url = upload_video(video_file, folder="messages")
        if not video_url:
            return jsonify(error="upload_failed"), 400

    # GIF ayrı bir kolona DEĞİL image_url'e yazılır — messages'ta gif_url
    # kolonu yok ve mevcut görsel render akışı GIF'i olduğu gibi gösterir
    # (send_message()'daki AYNI desen).
    if gif_url and not image_url:
        image_url = gif_url

    if reply_to_id:
        # send_message()'daki AYNI doğrulama: farklı konuşmadansa/mevcut
        # değilse reply'siz gönder (sessizce, hata değil).
        reply_msg = sb.table("messages").select("id").eq("id", reply_to_id).eq(
            "conversation_id", conversation_id
        ).execute().data
        if not reply_msg:
            reply_to_id = None

    insert_data = {
        "conversation_id": conversation_id, "sender_id": me, "content": content,
        "image_url": image_url, "video_url": video_url,
    }
    try:
        # Opsiyonel kolonlar: sticker_id, reply_to_id — migration henüz
        # uygulanmamışsa kolonsuz insert'e düş (send_message()'daki AYNI desen).
        data_full = dict(insert_data)
        if sticker_id:
            data_full["sticker_id"] = sticker_id
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


# --- Mesaj gelişmiş işlemleri (Faz 5 Dalga 1B, native Android) ---
# app/messaging/sending.py (delete/edit/pin/mute/forward-targets/forward),
# reactions.py (react) ve views.py (sohbet içi + global arama) BİREBİR aynı iş
# mantığı ve AYNI DURUM KODLARIYLA JSON'a taşınır — yeni bir kural, yeni bir
# yetki modeli veya yeni bir SQL migration'ı İCAT EDİLMEZ (gereken kolonların
# HEPSİ zaten canlıda: messages.edited_at/pinned_at/is_forwarded,
# conversation_participants.is_muted, message_reactions tablosu).
#
# İZİN ASİMETRİSİ (kaynak dosyalardan birebir — karıştırılmamalı):
#   - delete/edit : SADECE mesajın GÖNDERENİ (sender_id = me filtresi)
#   - pin         : konuşmanın HERHANGİ BİR katılımcısı (gönderen olmak şart değil)
#   - mute        : SADECE kendi katılımcı satırı (başkasını sessize alamazsın)
#   - react       : katılımcı olmayan 403 DEĞİL **404** alır — bu BİLİNÇLİ bir
#                   enumeration korumasıdır (reactions.py'deki abort(404)
#                   yorumu), "düzeltilmemeli".
#
# Okuma yolunda backend değişikliği GEREKMEDİ: api_message_conversation_detail()
# `select("*")` kullandığı için edited_at/pinned_at/is_forwarded zaten mesaj
# satırlarında dönüyor (native DTO'ya alan eklemek yeterli).
#
# BİLİNÇLİ SAPMA (web'den): hata gövdeleri Türkçe cümle DEĞİL makine-okunur kod
# (örn. "empty"/"not_found") — api_v1'in bu dosyadaki mevcut sözleşmesi bu
# (native istemci kodu switch'liyor, kullanıcıya gösterilecek metni KENDİ
# yerelleştirmesinden seçiyor). Durum KODLARI web ile birebir aynıdır.


@bp.route("/messages/<message_id>/delete", methods=["POST"])
@api_login_required
def api_delete_message(message_id):
    """Kendi mesajını siler — sending.py delete_message() ile AYNI mantık:
    HARD delete (soft-delete kolonu YOK), sahiplik uygulama katmanında
    `sender_id = me` filtresiyle sağlanır (service-role RLS'i bypass ettiği
    için bu filtre TEK savunma hattıdır, düşürülemez).

    Hiçbir satır eşleşmese bile (başkasının mesajı / zaten silinmiş) 404 DEĞİL
    {ok:true} döner — kaynak fonksiyonun AYNI davranışı; "bu id'li mesaj var
    mı" sorusuna cevap sızdırmamak da bu tavrın bir yan faydası.
    message_reactions satırları ON DELETE CASCADE ile birlikte ölür.
    """
    sb = get_sb()
    me = request.api_user["id"]
    sb.table("messages").delete().eq("id", message_id).eq("sender_id", me).execute()
    return jsonify(ok=True)


@bp.route("/messages/<message_id>/edit", methods=["POST"])
@api_login_required
def api_edit_message(message_id):
    """Kendi mesajının METNİNİ düzenler — sending.py edit_message() ile AYNI
    mantık (boş olamaz, <=2000 karakter, sender_id=me filtresi, edited_at
    kolonu yoksa yalnızca content'i güncelleyen fallback, hiçbir satır
    güncellenmezse 404).

    Ek (görsel/ses/sticker) ASLA değişmez — bu yüzden istemci düzenle
    seçeneğini SADECE salt-metin mesajlarda göstermelidir (web UI'ın AYNI
    kuralı, bkz. chat.js).
    """
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    content = _str_field(data, "content")
    if not content:
        return jsonify(error="empty"), 400
    if len(content) > 2000:
        return jsonify(error="too_long"), 400

    now = datetime.now(timezone.utc).isoformat()
    try:
        updated = sb.table("messages").update({"content": content, "edited_at": now}).eq(
            "id", message_id).eq("sender_id", me).execute().data
    except Exception:
        # edited_at migration'ı (sql/migration_message_edit.sql) uygulanmamışsa
        # "düzenlendi" etiketi olmadan sessizce content'i güncelle.
        updated = sb.table("messages").update({"content": content}).eq(
            "id", message_id).eq("sender_id", me).execute().data
        now = None
    if not updated:
        return jsonify(error="not_found"), 404
    return jsonify(ok=True, content=content, edited_at=now)


@bp.route("/messages/<message_id>/react", methods=["POST"])
@api_login_required
def api_react_message(message_id):
    """Mesaja emoji tepkisi ekle/değiştir/sil — reactions.py react_message()
    ile AYNI 3 yönlü toggle: aynı tepki tekrar gönderilirse SİLİNİR
    (reaction:null, 200), farklı tepki GÜNCELLENİR (200), hiç yoksa EKLENİR
    (201). Kullanıcı başına mesaj başına TEK tepki (PK (message_id, user_id)).

    DİKKAT — mesaj yoksa VEYA çağıran o konuşmanın katılımcısı değilse 403
    değil **404** döner: bu bilinçli bir enumeration korumasıdır (saldırgan
    "bu message_id var ama benim değil" ile "hiç yok"u ayırt edemesin), kaynak
    fonksiyondan aynen taşındı — "tutarsız" görünüp düzeltilmemelidir.
    """
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    reaction = _str_field(data, "reaction")
    if not reaction:
        return jsonify(error="empty_reaction"), 400

    try:
        msg = sb.table("messages").select("conversation_id").eq("id", message_id).execute().data
        if not msg:
            return jsonify(error="not_found"), 404
        conversation_id = msg[0]["conversation_id"]
    except Exception:
        return jsonify(error="not_found"), 404

    try:
        part = sb.table("conversation_participants").select("user_id").eq(
            "conversation_id", conversation_id).eq("user_id", me).execute().data
        if not part:
            return jsonify(error="not_found"), 404  # 403 DEĞİL — bkz. docstring
    except Exception:
        return jsonify(error="not_found"), 404

    try:
        existing = sb.table("message_reactions").select("reaction").eq(
            "message_id", message_id).eq("user_id", me).execute().data
        if existing:
            if existing[0].get("reaction") == reaction:
                sb.table("message_reactions").delete().eq(
                    "message_id", message_id).eq("user_id", me).execute()
                return jsonify(ok=True, reaction=None), 200
            sb.table("message_reactions").update({"reaction": reaction}).eq(
                "message_id", message_id).eq("user_id", me).execute()
            return jsonify(ok=True, reaction=reaction), 200
        sb.table("message_reactions").insert({
            "message_id": message_id, "user_id": me, "reaction": reaction,
        }).execute()
        return jsonify(ok=True, reaction=reaction), 201
    except Exception as e:
        # message_reactions tablosu henüz oluşturulmadıysa (reactions.py'deki
        # AYNI ayırt etme koşulu) — gerçek bir hata sessizce yutulmaz, raise edilir.
        if "message_reactions" in str(e) or "does not exist" in str(e):
            return jsonify(error="feature_not_yet_active"), 503
        raise


@bp.route("/messages/<message_id>/pin", methods=["POST"])
@api_login_required
def api_pin_message(message_id):
    """Mesajı sabitle / sabitlemeyi kaldır (toggle) — sending.py pin_message()
    ile AYNI mantık. Yetki, delete/edit'ten FARKLI olarak GÖNDEREN değil
    konuşmanın HERHANGİ BİR KATILIMCISIDIR (sabitleme sohbetin ortak hafızası,
    kaynak fonksiyonun bilinçli kuralı) — mesaj yoksa 404, katılımcı değilse
    403, pinned_at kolonu yoksa 503.
    """
    sb = get_sb()
    me = request.api_user["id"]

    try:
        msg = sb.table("messages").select("id, conversation_id, pinned_at").eq(
            "id", message_id).execute().data
    except Exception:
        # pinned_at kolonu (sql/migration_message_pin.sql) yoksa SELECT'in
        # kendisi patlar — web'de bu satır try DIŞINDA olduğu için 500 verirdi;
        # JSON API'de aynı durum, update'inkiyle AYNI 503'e çevrildi (bu
        # dosyanın migration-toleransı kuralı).
        return jsonify(error="unavailable"), 503
    if not msg:
        return jsonify(error="not_found"), 404
    conversation_id = msg[0]["conversation_id"]

    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    currently_pinned = msg[0].get("pinned_at") is not None
    new_pinned_at = None if currently_pinned else datetime.now(timezone.utc).isoformat()

    try:
        sb.table("messages").update({"pinned_at": new_pinned_at}).eq("id", message_id).execute()
    except Exception:
        return jsonify(error="unavailable"), 503
    return jsonify(ok=True, pinned=new_pinned_at is not None)


@bp.route("/messages/forward-targets")
@api_login_required
def api_forward_targets():
    """İletme hedefleri: kullanıcının TÜM konuşmaları — sending.py
    forward_targets() ile AYNI mantık (paylaşılan _build_convos() üzerinden,
    ayrı bir sorgu İCAT EDİLMEDİ; ad = grup adı / karşı kullanıcı adı /
    "Bilinmeyen").

    BİLİNÇLİ SAPMA: web çıplak bir JSON DİZİSİ döner, burada `{"targets": [...]}`
    nesnesi dönülür — bu dosyadaki diğer liste endpoint'leriyle (conversations=,
    members=) tutarlı olsun ve ileride `error` alanı eklenebilsin diye.
    """
    sb = get_sb()
    me = request.api_user["id"]
    convos = _build_convos(sb, me)
    targets = [
        {
            "id": c["id"],
            "name": c["name"] if c["is_group"] else (
                c["other_user"].get("username") if c["other_user"] else "Bilinmeyen"
            ),
        }
        for c in convos
    ]
    return jsonify(targets=targets)


@bp.route("/messages/<message_id>/forward", methods=["POST"])
@api_login_required
def api_forward_message(message_id):
    """Mesajı başka bir konuşmaya iletir — sending.py forward_message()'ın
    6 kontrolü AYNI SIRAYLA: (a) kaynak mesaj yoksa 404, (b) kaynak konuşmanın
    katılımcısı değilsem 403 (başkasının sohbetindeki mesaj iletilemez),
    (c) hedef konuşmanın katılımcısı değilsem 403, (d) hedefteki biriyle
    engelleme varsa 403 blocked, (e) send_message ile AYNI kullanıcı-bazlı
    limit (30/60sn) aşılırsa 429, (f) içerik kopyalanıp is_forwarded=True ile
    YENİ mesaj olarak insert edilir (kolon yoksa kolonsuz retry).

    Sıra ÖNEMLİ: rate limit yetki kontrollerinden SONRA gelir — geçersiz bir
    iletme denemesi kullanıcının kendi gönderim kotasını yakmaz.
    """
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    target_cid = _str_field(data, "target_conversation_id")
    if not target_cid:
        return jsonify(error="missing_target"), 400

    src = sb.table("messages").select(
        "id, conversation_id, sender_id, content, image_url, audio_url, sticker_id"
    ).eq("id", message_id).execute().data
    if not src:
        return jsonify(error="not_found"), 404
    msg = src[0]

    part_src = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", msg["conversation_id"]).eq("user_id", me).execute().data
    if not part_src:
        return jsonify(error="forbidden"), 403

    part_tgt = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", target_cid).eq("user_id", me).execute().data
    if not part_tgt:
        return jsonify(error="forbidden"), 403

    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", target_cid).neq("user_id", me).execute().data
    if any(is_blocked_either_way(sb, me, o["user_id"]) for o in others):
        return jsonify(error="blocked"), 403

    if is_rate_limited(f"send_message:{me}", 30, 60):
        return jsonify(error="rate_limited"), 429

    insert_data = {
        "conversation_id": target_cid,
        "sender_id": me,
        "content": msg.get("content", ""),
        "image_url": msg.get("image_url"),
    }
    optional = {}
    if msg.get("audio_url"):
        optional["audio_url"] = msg["audio_url"]
    if msg.get("sticker_id"):
        optional["sticker_id"] = msg["sticker_id"]
    try:
        sb.table("messages").insert({**insert_data, **optional, "is_forwarded": True}).execute()
    except Exception:
        # is_forwarded kolonu (sql/migration_message_forward.sql) yoksa onsuz dene
        sb.table("messages").insert({**insert_data, **optional}).execute()

    app = current_app._get_current_object()

    def _bg_notify():
        try:
            with app.test_request_context():
                _notify_conversation(sb, target_cid, me)
        except Exception:
            pass

    _write_pool.submit(_bg_notify)

    return jsonify(ok=True, conversation_id=target_cid)


@bp.route("/messages/conversations/<conversation_id>/mute", methods=["POST"])
@api_login_required
def api_mute_conversation(conversation_id):
    """Konuşmayı sessize al / sesi aç (toggle) — sending.py mute_conversation()
    ile AYNI mantık. SADECE ÇAĞIRANIN KENDİ katılımcı satırı güncellenir
    (başkasını sessize alma diye bir şey yok). Katılımcı değilse 403, is_muted
    kolonu yoksa 503.

    Anlamı (kaynak docstring'i): bildirim/push susar ama okunmamış mesaj
    sayacı normal şekilde artmaya devam eder (bkz. _common.py
    _notify_conversation — is_muted kontrolünden ÖNCE invalidate ediliyor).
    """
    sb = get_sb()
    me = request.api_user["id"]

    part = sb.table("conversation_participants").select("is_muted").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="forbidden"), 403

    new_muted = not bool(part[0].get("is_muted"))
    try:
        sb.table("conversation_participants").update({"is_muted": new_muted}).eq(
            "conversation_id", conversation_id).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="unavailable"), 503
    return jsonify(ok=True, muted=new_muted)


@bp.route("/messages/conversations/<conversation_id>/call-token", methods=["POST"])
@api_login_required
def api_call_token(conversation_id):
    """LiveKit grup sesli/görüntülü arama token'ı — messaging/group_calls.py
    call_token()'ın BİREBİR aynı mantığı (env var kontrolü, katılımcı+is_group
    doğrulaması paralel iki sorguyla, token üretimi), token-auth'a taşındı.

    - LiveKit yapılandırılmamışsa 503: {"error": "group_calls_not_configured"}
    - Katılımcı değilse VEYA is_group=false ise 404 (enumeration koruması —
      kaynak fonksiyondaki abort(404) ile AYNI durum kodu; bu dosyanın JSON
      sözleşmesine uysun diye jsonify+404 kullanıldı, abort() HTML sayfası
      döndürürdü).
    - CSRF: bu blueprint zaten Bearer token ile doğruluyor, session/CSRF'e
      tabi değil (bkz. app/__init__.py before_request api_v1. muafiyeti).
    """
    livekit_url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")

    if not livekit_url or not api_key or not api_secret:
        return jsonify(error="group_calls_not_configured"), 503

    me = request.api_user["id"]
    # .get: eski token'larda username alanı bulunmayabilir — KeyError yerine boş ad
    my_username = request.api_user.get("username") or ""
    sb = get_sb()

    def _check_participant():
        return sb.table("conversation_participants").select().eq(
            "conversation_id", conversation_id
        ).eq("user_id", me).execute().data

    def _fetch_conv_meta():
        try:
            conv = sb.table("conversations").select("is_group").eq(
                "id", conversation_id
            ).execute().data
            if conv:
                return bool(conv[0].get("is_group"))
        except Exception:
            pass
        return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        part_future = executor.submit(_check_participant)
        meta_future = executor.submit(_fetch_conv_meta)

        part = part_future.result()
        is_group = meta_future.result()

    if not part or not is_group:
        return jsonify(error="not_found"), 404

    try:
        room_name = f"grp-{conversation_id}"
        token = livekit_api.AccessToken(api_key, api_secret)
        token.with_identity(me)
        token.with_name(my_username)
        token.with_grants(
            livekit_api.VideoGrants(room_join=True, room=room_name)
        )
        token.with_ttl(timedelta(hours=1))
        jwt_token = token.to_jwt()

        return jsonify(token=jwt_token, url=livekit_url, room=room_name), 200
    except Exception:
        # Token üretim hatası nadir ama graceful fallback (group_calls.py ile AYNI)
        return jsonify(error="token_generation_failed"), 500


# Arama sayfa boyutu — views.py search_conversation_messages()/
# search_all_messages() ile AYNI (31 satır çekilir, 31.'nin varlığı has_next'i
# belirtir; MESSAGE_PAGE'den BAĞIMSIZ, kaynak fonksiyonların sabiti budur).
_SEARCH_PAGE = 30


def _serialize_search_row(r: dict, me: str, with_conversation: bool) -> dict:
    """views.py'deki iki arama fonksiyonunun AYNI satır sözleşmesi (global
    versiyonda ek olarak conversation_id) — tek yerde toplandı."""
    row = {
        "id": r["id"],
        "content": r["content"] or "",
        "created_at": r["created_at"],
        "mine": r["sender_id"] == me,
        "sender": (r.get("profiles") or {}).get("username") or "?",
    }
    if with_conversation:
        row["conversation_id"] = r["conversation_id"]
    return row


@bp.route("/messages/conversations/<conversation_id>/search")
@api_login_required
def api_search_conversation_messages(conversation_id):
    """Sohbet İÇİ mesaj arama — views.py search_conversation_messages() ile
    AYNI mantık: SADECE `content` üzerinde ILIKE '%q%' (görsel/ses/sticker
    aranmaz), katılımcı olmayan 403 değil **404** (mark-read ile aynı
    enumeration deseni), q 2 karakterden kısaysa boş sonuç, sayfa başına 30
    sonuç (?offset= ile ilerlenir), en yeniden eskiye.
    """
    sb = get_sb()
    me = request.api_user["id"]
    q = (request.args.get("q") or "").strip()
    offset = request.args.get("offset", 0, type=int) or 0

    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        return jsonify(error="not_found"), 404
    if len(q) < 2:
        return jsonify(results=[], has_next=False, next_offset=None)

    rows = sb.table("messages").select(
        "id, content, created_at, sender_id, profiles!messages_sender_id_fkey(username)"
    ).eq("conversation_id", conversation_id).ilike(
        "content", f"%{q}%"
    ).order("created_at", desc=True).limit(_SEARCH_PAGE + 1).offset(offset).execute().data

    has_next = len(rows) > _SEARCH_PAGE
    results = [_serialize_search_row(r, me, with_conversation=False) for r in rows[:_SEARCH_PAGE]]
    return jsonify(
        results=results,
        has_next=has_next,
        next_offset=offset + _SEARCH_PAGE if has_next else None,
    )


@bp.route("/messages/search")
@api_login_required
def api_search_all_messages():
    """TÜM sohbetlerde mesaj arama — views.py search_all_messages() ile AYNI
    mantık (yukarıdaki sohbet-içi aramanın global versiyonu; sonuç satırlarında
    EK OLARAK conversation_id döner ki istemci doğru sohbete gidebilsin).
    Hiç konuşması olmayan kullanıcı boş sonuç alır.
    """
    sb = get_sb()
    me = request.api_user["id"]
    q = (request.args.get("q") or "").strip()
    offset = request.args.get("offset", 0, type=int) or 0

    parts = sb.table("conversation_participants").select("conversation_id").eq(
        "user_id", me).execute().data
    cids = [p["conversation_id"] for p in parts]
    if not cids or len(q) < 2:
        return jsonify(results=[], has_next=False, next_offset=None)

    rows = sb.table("messages").select(
        "id, conversation_id, content, created_at, sender_id, "
        "profiles!messages_sender_id_fkey(username)"
    ).in_("conversation_id", cids).ilike(
        "content", f"%{q}%"
    ).order("created_at", desc=True).limit(_SEARCH_PAGE + 1).offset(offset).execute().data

    has_next = len(rows) > _SEARCH_PAGE
    results = [_serialize_search_row(r, me, with_conversation=True) for r in rows[:_SEARCH_PAGE]]
    return jsonify(
        results=results,
        has_next=has_next,
        next_offset=offset + _SEARCH_PAGE if has_next else None,
    )


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


# ----------------------- POST PAYLAŞIMI (DM'e gönder), native Android -----------------------
# sending.py share_post_multiple()'ın BİREBİR mirror'ı. Hedef seçimi için ayrı
# bir endpoint İCAT EDİLMEDİ — /messages/forward-targets (yukarıda) zaten
# kullanıcının TÜM konuşmalarını (grup adı / karşı kullanıcı adı) döndürüyor,
# native aynı listeyi post paylaşımı için de REUSE edebilir.

@bp.route("/posts/<post_id>/share", methods=["POST"])
@api_login_required
def api_share_post(post_id):
    """Postu seçili birden fazla kullanıcıya DM olarak gönderir —
    sending.py share_post_multiple() ile AYNI mantık (post önizlemesi +
    isteğe bağlı not, her hedef için get-or-create 1:1 konuşma)."""
    sb = get_sb()
    me = request.api_user["id"]
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    user_ids = data.get("user_ids", [])
    if not isinstance(user_ids, list):
        user_ids = []
    note = _str_field(data, "note")

    if not user_ids:
        return jsonify(error="no_recipients"), 400

    post = sb.table("posts").select(
        "id, content, image_url, image_urls, profiles!posts_user_id_fkey(username)"
    ).eq("id", post_id).execute().data
    if not post:
        return jsonify(error="not_found"), 404
    post_data = post[0]

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
            "image_url": post_image,
        }).execute()
        _notify_conversation(sb, cid, me)
        sent_count += 1

    return jsonify(sent=sent_count)
