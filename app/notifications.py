"""Bildirimler: beğeni, yorum, yanıt, takip, mesaj bildirimleri.

notify() diğer blueprint'lerden (social.py, messaging.py) çağrılır;
kendine bildirim gönderilmez (recipient_id == actor_id ise sessizce atlanır).
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, redirect, render_template, request, session, jsonify, url_for
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("notifications", __name__)

# (type_key, db_column, başlık, açıklama) — hem notify() filtrelemesi hem
# ayarlar sayfası bu listeden üretilir, iki yerde ayrı ayrı tanımlanmasın.
NOTIFICATION_TYPES = [
    ("like", "notify_like", "Beğeniler", "Gönderini biri beğendiğinde"),
    ("comment", "notify_comment", "Yorumlar", "Gönderine biri yorum yaptığında"),
    ("reply", "notify_reply", "Yanıtlar", "Yorumuna biri yanıt verdiğinde"),
    ("comment_like", "notify_comment_like", "Yorum beğenileri", "Yorumunu biri beğendiğinde"),
    ("comment_reaction", "notify_comment_reaction", "Yorum tepkileri", "Yorumuna biri emoji tepkisi verdiğinde"),
    ("follow", "notify_follow", "Takipçiler", "Biri seni takip etmeye başladığında"),
    ("follow_request", "notify_follow_request", "Takip İstekleri", "Gizli profiline biri takip isteği gönderdiğinde"),
    ("follow_accept", "notify_follow_accept", "İstek Kabülleri", "Takip isteğin kabul edildiğinde"),
    ("message", "notify_message", "Mesajlar", "Sana mesaj geldiğinde"),
    ("mention", "notify_mention", "Etiketlenmeler", "Bir gönderide etiketlendiğinde"),
    ("hashtag_post", "notify_hashtag_post", "Takip edilen etiketler", "Takip ettiğin bir etikette yeni paylaşım olduğunda"),
]
_TYPE_TO_COLUMN = {t[0]: t[1] for t in NOTIFICATION_TYPES}

PAGE_SIZE = 20

# Bildirimler bu süreden eski olunca silinir. Ayrı bir cron/scheduler altyapısı
# yok — liste sayfası ziyaretinde fırsatçı (opportunistic) temizlik yeterli
# (bkz. _cleanup_old_notifications), çünkü bu ölçekte bir arkadaş grubu
# uygulamasında bildirim listesini kimse hiç görmeden aylarca birikmesi olası değil.
RETENTION_DAYS = 60


def _cleanup_old_notifications(sb, me: str) -> None:
    """`me`'ye ait, RETENTION_DAYS'ten eski bildirimleri siler."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    sb.table("notifications").delete().eq("recipient_id", me).lt("created_at", cutoff).execute()

# Bildirim türüne göre yönlendirilecek hedef URL
_TARGET_BUILDERS = {
    "like": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "comment": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "reply": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "comment_like": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "comment_reaction": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "follow": lambda n: url_for("routes.profile", username=n["actor"]["username"]),
    "follow_request": lambda n: url_for("social.list_follow_requests"),
    "follow_accept": lambda n: url_for("routes.profile", username=n["actor"]["username"]),
    "message": lambda n: url_for("messaging.conversation", conversation_id=n["conversation_id"]),
    # Mesajda etiketlenme post_id taşımaz — conversation_id'ye düşer
    # (bkz. mentions.notify_mentions conversation_id parametresi).
    "mention": lambda n: (url_for("routes.post_detail", post_id=n["post_id"]) if n.get("post_id")
                           else url_for("messaging.conversation", conversation_id=n["conversation_id"])),
    "hashtag_post": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
}

_TEXT = {
    "like": "gönderini beğendi",
    "comment": "gönderine yorum yaptı",
    "reply": "yorumuna yanıt verdi",
    "comment_like": "yorumunu beğendi",
    "comment_reaction": "yorumuna tepki verdi",
    "follow": "seni takip etmeye başladı",
    "follow_request": "sana takip isteği gönderdi",
    "follow_accept": "takip isteğini kabul etti",
    "message": "sana mesaj gönderdi",
    "mention": "seni bir gönderide etiketledi",
    "hashtag_post": "takip ettiğin bir etikette yeni post paylaştı",
}


# Art arda kaç mesaj bildirimi push atsın (kullanıcı isteği: 3'ten sonra
# anlık/push bildirim sussun, ama uygulama-içi bildirim satırı sayaç olarak
# artmaya devam eder — bkz. _notify_message).
_MESSAGE_PUSH_LIMIT = 3


def notify(sb, *, recipient_id: str, actor_id: str, type_: str,
           post_id: str | None = None, comment_id: str | None = None,
           conversation_id: str | None = None, hashtag_id: str | None = None) -> None:
    """Bir kullanıcıya bildirim oluşturur. Kendine bildirim oluşturulmaz."""
    if recipient_id == actor_id:
        return

    # Alıcı bu bildirim türünü kapatmış olabilir (opt-out, #40). Tercih satırı
    # yoksa varsayılan açık (fail-open); migration henüz uygulanmamışsa tablo
    # hiç yoktur — hata yutulup normal gönderim davranışına düşülür.
    try:
        column = _TYPE_TO_COLUMN.get(type_)
        if column:
            pref = sb.table("notification_preferences").select(column).eq(
                "user_id", recipient_id
            ).execute().data
            if pref and pref[0].get(column) is False:
                return
    except Exception:
        pass

    if type_ == "message":
        # Mesaj bildirimleri sohbet başına TEK satırda toplanır ("Ali sana N
        # mesaj gönderdi") — her mesaj için ayrı satır açmak hem bildirim
        # listesini hem navbar rozetini mesaj sayısı kadar şişiriyordu
        # (kullanıcı isteği: "1-2-3-4-5 diye artmasın").
        _notify_message(sb, recipient_id, actor_id, conversation_id)
        return

    sb.table("notifications").insert({
        "recipient_id": recipient_id,
        "actor_id": actor_id,
        "type": type_,
        "post_id": post_id,
        "comment_id": comment_id,
        "conversation_id": conversation_id,
        "hashtag_id": hashtag_id,
    }).execute()
    from .cache import invalidate
    invalidate(f"unread:{recipient_id}")

    _push(sb, recipient_id, actor_id, type_, post_id=post_id, conversation_id=conversation_id)


def _notify_message(sb, recipient_id: str, actor_id: str, conversation_id: str | None) -> None:
    """Mesaj bildirimini sohbet başına upsert eder (count arttırır) + push kısıtı uygular.

    `count` kolonu migration_notification_count.sql ile eklenir; kolon henüz
    yoksa (migration uygulanmamış) düz insert'e düşülür — eski davranış.
    """
    from .cache import invalidate
    try:
        existing = sb.table("notifications").select("id, count").eq(
            "recipient_id", recipient_id
        ).eq("type", "message").eq(
            "conversation_id", conversation_id
        ).eq("is_read", False).execute().data
    except Exception:
        existing = None  # sorgu patladıysa (kolon/tablo yok) eski davranışa düş

    if existing is None:
        sb.table("notifications").insert({
            "recipient_id": recipient_id, "actor_id": actor_id, "type": "message",
            "conversation_id": conversation_id,
        }).execute()
        invalidate(f"unread:{recipient_id}")
        _push(sb, recipient_id, actor_id, "message", conversation_id=conversation_id, count=1)
        return

    if existing:
        new_count = (existing[0].get("count") or 1) + 1
        sb.table("notifications").update({
            "count": new_count, "actor_id": actor_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", existing[0]["id"]).execute()
    else:
        new_count = 1
        sb.table("notifications").insert({
            "recipient_id": recipient_id, "actor_id": actor_id, "type": "message",
            "conversation_id": conversation_id, "count": 1,
        }).execute()

    invalidate(f"unread:{recipient_id}")
    _push(sb, recipient_id, actor_id, "message", conversation_id=conversation_id, count=new_count)


def _push(sb, recipient_id: str, actor_id: str, type_: str, *,
          post_id: str | None = None, conversation_id: str | None = None,
          count: int = 1) -> None:
    """Web Push gönderir (uygulama kapalıyken de bildirim) — VAPID anahtarı yoksa
    send_push_to_user() sessizce çıkar, bu fonksiyon ASLA bildirim akışını
    kesintiye uğratmamalı (bkz. push.py docstring'i).

    Mesaj türü için `count > _MESSAGE_PUSH_LIMIT` olduğunda push ATLANIR —
    art arda gelen mesajlarda her seferinde anlık bildirim istenmiyor
    (kullanıcı isteği: "3 bildirimden sonra göndermesin").
    """
    if type_ == "message" and count > _MESSAGE_PUSH_LIMIT:
        return
    try:
        from .push import send_push_to_user, VAPID_PRIVATE_KEY
        if VAPID_PRIVATE_KEY:
            actor_row = sb.table("profiles").select("username").eq("id", actor_id).execute().data
            actor_name = actor_row[0]["username"] if actor_row else "Biri"
            if type_ == "message" and count > 1:
                body = f"{actor_name} sana {count} mesaj gönderdi"
            else:
                body = f"{actor_name} {_TEXT.get(type_, 'bir etkileşimde bulundu')}"
            builder = _TARGET_BUILDERS.get(type_)
            url = builder({
                "post_id": post_id, "conversation_id": conversation_id,
                "actor": {"username": actor_name},
            }) if builder else "/"
            send_push_to_user(sb, recipient_id, "Sosyal", body, url)
    except Exception:
        pass


def _annotate(n: dict) -> dict:
    """Bildirime metin + hedef URL ekler (liste sayfası ve slide-in panel ortak kullanır)."""
    n["text"] = _TEXT.get(n["type"], "")
    if n["type"] == "hashtag_post" and n.get("hashtag"):
        n["text"] = f"#{n['hashtag']['tag']} etiketinde yeni post paylaştı"
    # Mesaj bildirimleri sohbet başına tek satırda toplanır (bkz. notify._notify_message)
    # — count > 1 ise "sana N mesaj gönderdi" göster, migration uygulanmamışsa
    # (count kolonu yok) n.get("count") None/1 döner, tekil metin kalır.
    if n["type"] == "message" and (n.get("count") or 1) > 1:
        n["text"] = f"sana {n['count']} mesaj gönderdi"
    if n["type"] == "mention" and not n.get("post_id"):
        n["text"] = "bir mesajda seni etiketledi"
    builder = _TARGET_BUILDERS.get(n["type"])
    n["target_url"] = builder(n) if builder else url_for("routes.feed")
    return n


# Aynı hedefe (post) ait art arda gelen bildirimler tek satırda gruplanır —
# bildirim listesi kalabalıklaşmasın diye (ör. 5 kişi aynı postu beğenince).
_GROUPABLE_TYPES = {"like", "comment_like"}


def _group_notifications(rows: list[dict]) -> list[dict]:
    """('A, B ve N kişi daha gönderini beğendi' tarzı) gruplu görüntüleme listesi üretir."""
    groups: list[dict] = []
    seen: dict[tuple, dict] = {}

    for n in rows:
        key = (n["type"], n.get("post_id"))
        if n["type"] in _GROUPABLE_TYPES and key in seen:
            g = seen[key]
            if n.get("actor"):
                g["_actors"].append(n["actor"])
            g["is_read"] = g["is_read"] and n["is_read"]
            continue

        g = {
            "type": n["type"],
            "_actors": [n["actor"]] if n.get("actor") else [],
            "target_url": n["target_url"],
            "text": n["text"],
            "created_at": n["created_at"],
            "is_read": n["is_read"],
        }
        groups.append(g)
        if n["type"] in _GROUPABLE_TYPES:
            seen[key] = g

    for g in groups:
        actors = g.pop("_actors")
        names = [a["username"] for a in actors if a and a.get("username")]
        if not names:
            g["actor_summary"] = "Biri"
        elif len(names) == 1:
            g["actor_summary"] = names[0]
        elif len(names) == 2:
            g["actor_summary"] = f"{names[0]} ve {names[1]}"
        else:
            g["actor_summary"] = f"{names[0]}, {names[1]} ve {len(names) - 2} kişi daha"
        g["actor"] = actors[0] if actors else None

    return groups


def _fetch_and_mark_read(sb, me: str, limit: int, offset: int = 0) -> tuple[list[dict], bool]:
    """Bildirimleri çeker, hedef URL/metin ekler, gruplar ve görüntülenenleri okundu işaretler."""
    rows = sb.table("notifications").select(
        "*, actor:profiles!notifications_actor_id_fkey(username, avatar_url), hashtag:hashtags(tag)"
    ).eq("recipient_id", me).order(
        "created_at", desc=True
    ).range(offset, offset + limit).execute().data

    has_next = len(rows) > limit
    rows = rows[:limit]
    for n in rows:
        _annotate(n)

    unread_ids = [n["id"] for n in rows if not n["is_read"]]
    if unread_ids:
        sb.table("notifications").update({"is_read": True}).in_("id", unread_ids).execute()
        from .cache import invalidate
        invalidate(f"unread:{me}")

    return _group_notifications(rows), has_next


@bp.route("/")
@login_required
@retry_on_connection_error
def list_notifications():
    """Bildirim listesi (sayfalı). Sayfa görüntülendiğinde okunmamışlar okundu işaretlenir."""
    sb = get_sb()
    me = session["user"]["id"]
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    if page == 1:
        _cleanup_old_notifications(sb, me)

    rows, has_next = _fetch_and_mark_read(sb, me, PAGE_SIZE, offset)

    return render_template("notifications/list.html", notifications=rows,
                           page=page, has_next=has_next, me=session["user"])


PANEL_SIZE = 8


@bp.route("/panel")
@login_required
@retry_on_connection_error
def panel():
    """Navbar zilinden açılan slide-in panel için son bildirimleri JSON döner."""
    sb = get_sb()
    me = session["user"]["id"]
    rows, _ = _fetch_and_mark_read(sb, me, PANEL_SIZE)

    # _fetch_and_mark_read yalnızca GÖRÜNEN son 8 bildirimi işaretler — daha
    # eski okunmamışlar kalıyor ve sayfa yenilenince rozet "geri geliyordu"
    # (kullanıcı raporu). Paneli açmak "hepsini gördüm" niyetidir: kalan tüm
    # okunmamışlar da toplu işaretlenir.
    try:
        sb.table("notifications").update({"is_read": True}).eq(
            "recipient_id", me).eq("is_read", False).execute()
        from .cache import invalidate
        invalidate(f"unread:{me}")
    except Exception:
        pass

    return jsonify(notifications=[{
        "type": n["type"],
        "text": n["text"],
        "target_url": n["target_url"],
        "username": n["actor_summary"],
        "avatar_url": n["actor"]["avatar_url"] if n.get("actor") else None,
        "created_at": n["created_at"],
        "is_read": n["is_read"],
    } for n in rows])


@bp.route("/preferences", methods=["GET", "POST"])
@login_required
@retry_on_connection_error
def preferences():
    """Bildirim türü bazlı opt-out ayarları (#40)."""
    sb = get_sb()
    me = session["user"]["id"]
    columns = [t[1] for t in NOTIFICATION_TYPES]

    if request.method == "POST":
        try:
            # İşaretli checkbox'lar request.form'da bulunur, işaretsizler hiç
            # bulunmaz (HTML checkbox davranışı) — bu yüzden yokluk == False.
            payload = {col: (request.form.get(col) == "on") for col in columns}
            payload["user_id"] = me
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            sb.table("notification_preferences").upsert(
                payload, on_conflict="user_id"
            ).execute()
            flash("Bildirim tercihlerin kaydedildi.", "success")
        except Exception as e:
            # Kolon eksikse (migration uygulanmamışsa) veya başka DB hatası
            import sys
            print(f"Bildirim tercihleri upsert hatası: {e}", file=sys.stderr)
            flash("Bildirim tercihleri henüz kullanılamıyor, daha sonra tekrar dene.", "error")
        return redirect(url_for("notifications.preferences"))

    try:
        rows = sb.table("notification_preferences").select("*").eq(
            "user_id", me
        ).execute().data
        prefs = rows[0] if rows else {col: True for col in columns}
    except Exception:
        prefs = {col: True for col in columns}

    return render_template(
        "notifications/preferences.html",
        notification_types=NOTIFICATION_TYPES,
        prefs=prefs,
        me=session["user"],
    )


@bp.route("/unread-count")
@login_required
@retry_on_connection_error
def unread_count():
    """Navbar rozeti için JS polling ucu. 20 saniye TTL cache ile optimize edilir."""
    from .cache import get_cached
    sb = get_sb()
    me = session["user"]["id"]
    def _fetch():
        return sb.table("notifications").select(
            "id", count="exact", head=True
        ).eq("recipient_id", me).eq("is_read", False).execute().count or 0
    count = get_cached(f"unread:{me}", 20, _fetch)
    return jsonify(count=count)
