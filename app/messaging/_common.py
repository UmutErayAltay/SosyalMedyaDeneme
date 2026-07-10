"""messaging/ paketindeki alt-modüllerin paylaştığı yardımcılar (route yok)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ..supabase_client import get_sb
from ..notifications import notify


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


def unread_message_count(sb, me: str) -> int:
    """Kullanıcının TÜM 1:1 konuşmalarındaki okunmamış mesaj sayısı (navbar rozeti).

    Grup sohbetleri BİLEREK sayılmaz: read_at grup mesajlarında hiç set
    edilmiyor (bkz. views.py conversation() — `_mark_read()` SADECE
    `not is_group` durumunda çağrılıyor, çünkü tek bir read_at kolonu "N
    kişiden kim okudu" bilgisini tutamaz). Grup mesajlarını dahil etmek
    sayacı sonsuza kadar büyüyen, hiç sıfırlanmayan yanlış bir rozete
    yol açardı.
    """
    try:
        parts = sb.table("conversation_participants").select(
            "conversation_id"
        ).eq("user_id", me).execute().data
        cids = [p["conversation_id"] for p in parts]
        if not cids:
            return 0
        try:
            conv_rows = sb.table("conversations").select("id, is_group").in_("id", cids).execute().data
            dm_ids = [r["id"] for r in conv_rows if not r.get("is_group")]
        except Exception:
            dm_ids = cids  # is_group kolonu yoksa (migration_group_chat.sql uygulanmamış) hepsi 1:1
        if not dm_ids:
            return 0
        return sb.table("messages").select(
            "id", count="exact", head=True
        ).in_("conversation_id", dm_ids).neq("sender_id", me).is_("read_at", "null").execute().count or 0
    except Exception:
        return 0


def _notify_conversation(sb, conversation_id: str, sender_id: str) -> None:
    """Konuşmadaki diğer katılımcı(lar)a yeni mesaj bildirimi gönderir."""
    others = sb.table("conversation_participants").select("user_id").eq(
        "conversation_id", conversation_id
    ).neq("user_id", sender_id).execute().data
    for o in others:
        notify(sb, recipient_id=o["user_id"], actor_id=sender_id,
               type_="message", conversation_id=conversation_id)


def _get_or_create_conversation(me_id: str, target_id: str) -> str:
    """İki kullanıcı arasındaki 1:1 konuşmayı bulur veya yenisini oluşturur, ID'sini döner."""
    sb = get_sb()

    # Paralel: her iki kullanıcının konuşma ID'lerini çek
    def _fetch_my_convs():
        try:
            return sb.table("conversation_participants").select("conversation_id").eq("user_id", me_id).execute().data
        except Exception:
            return []

    def _fetch_target_convs():
        try:
            return sb.table("conversation_participants").select("conversation_id").eq("user_id", target_id).execute().data
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=2) as executor:
        my_convs_future = executor.submit(_fetch_my_convs)
        target_convs_future = executor.submit(_fetch_target_convs)

        my_convs = my_convs_future.result()
        target_convs = target_convs_future.result()

    my_ids = {c["conversation_id"] for c in my_convs}
    target_ids = {c["conversation_id"] for c in target_convs}
    shared = my_ids & target_ids

    if shared:
        # İki kullanıcı ortak bir GRUP sohbetinin de üyesi olabilir (örn. aynı
        # arkadaş grubunda) — bu fonksiyon her zaman 1:1 DM aradığından, grup
        # conversation'ları kesişimden elenir (yoksa start_conversation() 1:1
        # yerine yanlışlıkla gruba yönlendirir). migration_group_chat.sql henüz
        # uygulanmamışsa is_group kolonu yoktur; bu durumda zaten TÜM konuşmalar
        # 1:1'dir, filtreye gerek yok — sorgu patlarsa eski (filtresiz) davranışa düş.
        try:
            rows = sb.table("conversations").select("id, is_group").in_("id", list(shared)).execute().data
            group_ids = {r["id"] for r in rows if r.get("is_group")}
            shared = shared - group_ids
        except Exception:
            pass

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
        # Grup meta, katılımcılar ve son mesajlar paralel çek — 3 sorgu birbirinden bağımsız
        def _fetch_conv_meta():
            try:
                rows = sb.table("conversations").select("id, is_group, name").in_("id", cids).execute().data
                return {r["id"]: r for r in rows}
            except Exception:
                return {}

        def _fetch_others():
            # Tek geçici sorgu hatası TÜM listeyi isimsiz ('Bilinmeyen')
            # bırakıyordu (kullanıcı raporu: "bazen hepsi Bilinmeyen") —
            # bir kez yeniden dene, ancak ikinci hatada pes et
            for attempt in (0, 1):
                try:
                    others = sb.table("conversation_participants").select(
                        "conversation_id, profiles!conversation_participants_user_id_fkey(id, username, avatar_url)"
                    ).in_("conversation_id", cids).neq("user_id", me).execute().data
                    others_by_cid: dict = {}
                    for o in others:
                        others_by_cid.setdefault(o["conversation_id"], []).append(o.get("profiles"))
                    return others_by_cid
                except Exception:
                    if attempt:
                        return {}

        def _fetch_messages():
            try:
                msgs = sb.table("messages").select(
                    "*, profiles!messages_sender_id_fkey(username, avatar_url)"
                ).in_("conversation_id", cids).order(
                    "created_at", desc=True
                ).limit(max(len(cids) * 30, 300)).execute().data
                last_by_cid = {}
                for m in msgs:
                    last_by_cid.setdefault(m["conversation_id"], m)
                return last_by_cid
            except Exception:
                return {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            meta_future = executor.submit(_fetch_conv_meta)
            others_future = executor.submit(_fetch_others)
            messages_future = executor.submit(_fetch_messages)

            conv_meta = meta_future.result()
            others_by_cid = others_future.result()
            last_by_cid = messages_future.result()

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
