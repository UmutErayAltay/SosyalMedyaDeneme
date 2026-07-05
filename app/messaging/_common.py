"""messaging/ paketindeki alt-modüllerin paylaştığı yardımcılar (route yok)."""
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
