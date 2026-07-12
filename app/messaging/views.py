"""Konuşma listesi (inbox) + tek bir konuşmanın görüntülenmesi."""
from concurrent.futures import ThreadPoolExecutor
from flask import render_template, request, session, abort, jsonify
from . import bp
from ._common import (_mark_read, _build_convos, unread_message_count,
                      mark_active, is_active_in, _mark_message_notifications_read)
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..auth import refresh_session_tokens

# Sohbet açılışında çekilen son mesaj sayısı — üzeri "Daha eski mesajları
# yükle" butonuyla (?all=1) gelir. 150→50 küçültüldü (2. hız turu): hem
# sorgu payload'ı hem 147 mesajlık Jinja render'ı açılışı yavaşlatıyordu;
# WhatsApp Web de ~50 ile açılır. Bkz. conversation() FAZ 1 yorumu.
MESSAGE_PAGE = 50

# Okundu/bildirim YAZMALARI yanıtı bekletmesin diye kalıcı arka plan havuzu —
# render bu yazmalara bağlı değil (first_unread_id bellek içi listeden
# hesaplanır, rozetler zaten 20-25sn'lik polling/cache ile geliyor). Havuz
# `with` bloğuna alınmaz: bloktan çıkış join edip beklerdi, amaç beklememek.
_write_pool = ThreadPoolExecutor(max_workers=2)


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
    show_all = request.args.get("all") == "1"
    # Ön-yükleme (hover prefetch, messagesPanel.js) YAN-ETKİSİZ olmalı:
    # kullanıcı sohbeti hiç AÇMADAN mesajlar okundu işaretlenirse karşı
    # tarafa yanlış ✓✓ gider. Gerçek açılışta istemci swap sonrası
    # /mark-read POST'unu atar (o uç bildirimleri de okur).
    is_prefetch = request.headers.get("X-Prefetch") == "1"

    # --- FAZ 1: doğrulama + meta + katılımcılar + mesajlar TEK paralel dalga ---
    # Önceden katılımcı doğrulaması AYRI (seri) bir turdu, ardından 3'lü paralel
    # tur, sonra tepki + sticker + okundu yazmaları da hep SERİ koşuyordu —
    # sohbet açılışı ~6 ardışık Supabase turu bekliyordu (kullanıcı isteği:
    # "mesajlar daha hızlı yüklensin"). Doğrulama sorgusu diğerlerinden bağımsız
    # olduğu için birlikte koşturulur, SONUCU her şeyden önce denetlenir —
    # katılımcı değilse çekilen veri çöpe gider ama asla sızmaz (403).
    def _check_participant():
        return sb.table("conversation_participants").select().eq(
            "conversation_id", conversation_id
        ).eq("user_id", me).execute().data

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
        # Son MESSAGE_PAGE mesaj (uzun sohbetlerde tüm geçmişi çekmek hem
        # sorguyu hem HTML render'ını yavaşlatıyordu); +1 satır "daha eskisi
        # var mı" bilgisini verir. ?all=1 tüm geçmişi getirir (panel içindeki
        # 'Daha eski mesajları yükle' butonu — bkz. _conversation_panel.html).
        #
        # Tepkiler + sticker'lar AYRI sorgular yerine PostgREST embed ile
        # AYNI sorguda gelir (sohbet açılışında koca bir tur tasarrufu —
        # Supabase'e tek tur ~300ms; embed canlıda doğrulandı). Embed'li
        # select herhangi bir sebeple patlarsa sade select'e düşülür —
        # sohbet render'ı ASLA embed yüzünden kırılmaz (tepki/sticker o
        # durumda boş görünür, mesajlar görünmeye devam eder).
        base = "*, profiles!messages_sender_id_fkey(username, avatar_url)"
        selects = (base + ", sticker:stickers(id, image_url), message_reactions(reaction, user_id)",
                   base)
        for sel in selects:
            try:
                q = sb.table("messages").select(sel).eq(
                    "conversation_id", conversation_id).order("created_at", desc=True)
                if not show_all:
                    q = q.limit(MESSAGE_PAGE + 1)
                rows = q.execute().data
                rows.reverse()  # şablon + okunmamış-çapa mantığı ARTAN sıra bekler
                return rows
            except Exception:
                continue
        return []

    with ThreadPoolExecutor(max_workers=4) as executor:
        part_future = executor.submit(_check_participant)
        meta_future = executor.submit(_fetch_conv_meta)
        others_future = executor.submit(_fetch_others)
        messages_future = executor.submit(_fetch_messages)

        part = part_future.result()
        (is_group, group_name) = meta_future.result()
        other_profiles = others_future.result()
        messages = messages_future.result()

    # Kullanıcı bu konuşmada mı? (sonuç her şeyden önce denetlenir)
    if not part:
        abort(403)
    # is_admin migration uygulanmamışsa kolon dict'te yok — güvenli varsayılan False
    my_is_admin = bool(part[0].get("is_admin"))
    # "Aktif" işareti bellek içi, anlık (chat.js periyodik ping ile tazeler) —
    # bu sohbetten gelen mesaj bildirimi/push'u üretilmesin (bkz. _notify_conversation).
    # Ön-yüklemede İŞARETLENMEZ (hover ≠ sohbeti açmak).
    if not is_prefetch:
        mark_active(me, conversation_id)

    # Limit fazlası satır = daha eski mesajlar var (reverse sonrası en başta)
    has_older = False
    if not show_all and len(messages) > MESSAGE_PAGE:
        has_older = True
        messages = messages[1:]

    # Okunmamış ilk mesaj — read_at güncellenmeden ÖNCE, bellek içi listeden
    # hesaplanır (bu yüzden _mark_read'in paralel koşması güvenli); template
    # bu mesaja çapa koyar, chat.js sohbeti oradan başlatır (yoksa en alttan)
    first_unread_id = None
    if not is_group:
        for m in messages:
            if m.get("sender_id") != me and not m.get("read_at"):
                first_unread_id = m["id"]
                break

    # --- FAZ 2: okundu YAZMALARI arka planda (yanıt bunları BEKLEMEZ) ---
    # first_unread_id yukarıda bellek içi listeden hesaplandığı için yazmaların
    # ne zaman bittiği render'ı etkilemez; rozetler zaten polling/cache'li.
    def _write_reads():
        # Grupta "okundu" bilgisi anlamsız (bkz. modül docstring'i) — sadece 1:1'de
        if not is_group:
            _mark_read(sb, conversation_id, me, messages)
        # Sohbeti açmak = o sohbetin mesaj bildirimlerini de okumak (zil rozeti
        # bayat kalmasın, gruplama sayacı sıfırlansın — bkz. _common docstring'i)
        _mark_message_notifications_read(sb, me, conversation_id)

    if not is_prefetch:
        _write_pool.submit(_write_reads)

    # Embed'den gelen tepkileri şablonun beklediği şekle çevir; embed'siz
    # fallback yolunda anahtarlar hiç yoktur — boş/None varsayılır
    for m in messages:
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

    other_user = None if is_group else (other_profiles[0] if other_profiles else None)
    # Supabase Realtime INSERT payload'ı sadece ham satırı verir (join yok) —
    # grup sohbetinde yeni mesajın kimden geldiğini göstermek için client-side
    # bir id→username haritası gerekiyor (bkz. chat.js, data-member-map).
    member_map = {p["id"]: p["username"] for p in other_profiles if p.get("id")}

    ctx = dict(messages=messages, first_unread_id=first_unread_id,
               other_user=other_user, is_group=is_group,
               group_name=group_name, group_members=other_profiles, member_map=member_map,
               conversation_id=conversation_id, me=session["user"],
               my_is_admin=my_is_admin if is_group else False,
               has_older=has_older)

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
        # Rozet cache'i düşür — bkz. _common._mark_read'deki not
        from ..cache import invalidate
        invalidate(f"unread_msgs:{me}")
    except Exception:
        pass  # read_at migration'ı yoksa sessizce atla (_mark_read ile aynı tavır)
    # Sohbet AÇIKKEN düşen mesajın bildirimi de (varsa) okundu sayılır
    _mark_message_notifications_read(sb, me, conversation_id)
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
    # TÜM katılımcılar tek sorguda: hem benim üyelik doğrulamam hem
    # diğerlerinin aktiflik sayımı için (çevrimiçi göstergesi — chat.js
    # yanıttaki `here` ile başlıkta "şu anda burada" gösterir)
    parts = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).execute().data
    ids = [p["user_id"] for p in parts]
    if me not in ids:
        abort(404)
    mark_active(me, conversation_id)
    here = sum(1 for uid in ids if uid != me and is_active_in(uid, conversation_id))
    return jsonify(ok=True, here=here)


@bp.route("/<conversation_id>/search")
@login_required
@retry_on_connection_error
def search_conversation_messages(conversation_id):
    """Sohbet içi mesaj arama — sadece metin (content) alanında ILIKE.

    Katılımcı olmayan 404 (enumeration koruması, mark-read/active ile aynı
    desen). Sonuçlar en yeniden eskiye, `id` chat.js'in DOM'da (`[data-msg-id]`)
    arayıp kaydırması veya bulunamazsa `?all=1#msg-<id>`'ye gitmesi için döner.
    """
    sb = get_sb()
    me = session["user"]["id"]
    q = request.args.get("q", "").strip()
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        abort(404)
    if len(q) < 2:
        return jsonify(results=[])

    rows = sb.table("messages").select(
        "id, content, created_at, sender_id, profiles!messages_sender_id_fkey(username)"
    ).eq("conversation_id", conversation_id).ilike(
        "content", f"%{q}%"
    ).order("created_at", desc=True).limit(30).execute().data

    results = [{
        "id": r["id"],
        "content": r["content"] or "",
        "created_at": r["created_at"],
        "mine": r["sender_id"] == me,
        "sender": (r.get("profiles") or {}).get("username") or "?",
    } for r in rows]
    return jsonify(results=results)


@bp.route("/<conversation_id>/media")
@login_required
@retry_on_connection_error
def conversation_media(conversation_id):
    """Sohbette paylaşılan tüm görsel/ses medyasını TEK listede döner
    (galeri paneli — kullanıcı isteği: eski medyaya hızlı erişim). Sayfalama
    yok — bu ölçekteki (arkadaş grubu) bir sohbette medya sayısı sınırlı,
    diğer basit listelerle (grup üye listesi vb.) aynı yaklaşım."""
    sb = get_sb()
    me = session["user"]["id"]
    part = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id).eq("user_id", me).execute().data
    if not part:
        abort(404)

    rows = sb.table("messages").select(
        "id, image_url, audio_url, created_at"
    ).eq("conversation_id", conversation_id).order("created_at", desc=True).execute().data

    images = [{"id": r["id"], "url": r["image_url"], "created_at": r["created_at"]}
              for r in rows if r.get("image_url")]
    audios = [{"id": r["id"], "url": r["audio_url"], "created_at": r["created_at"]}
              for r in rows if r.get("audio_url")]
    return jsonify(images=images, audios=audios)
