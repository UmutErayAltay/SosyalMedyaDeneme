"""Bildirimler: beğeni, yorum, yanıt, takip, mesaj bildirimleri.

notify() diğer blueprint'lerden (social.py, messaging.py) çağrılır;
kendine bildirim gönderilmez (recipient_id == actor_id ise sessizce atlanır).
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request, session, jsonify, url_for
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("notifications", __name__)

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
    "follow": lambda n: url_for("routes.profile", username=n["actor"]["username"]),
    "message": lambda n: url_for("messaging.conversation", conversation_id=n["conversation_id"]),
    "mention": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
    "hashtag_post": lambda n: url_for("routes.post_detail", post_id=n["post_id"]),
}

_TEXT = {
    "like": "gönderini beğendi",
    "comment": "gönderine yorum yaptı",
    "reply": "yorumuna yanıt verdi",
    "comment_like": "yorumunu beğendi",
    "follow": "seni takip etmeye başladı",
    "message": "sana mesaj gönderdi",
    "mention": "seni bir gönderide etiketledi",
    "hashtag_post": "takip ettiğin bir etikette yeni post paylaştı",
}


def notify(sb, *, recipient_id: str, actor_id: str, type_: str,
           post_id: str | None = None, comment_id: str | None = None,
           conversation_id: str | None = None, hashtag_id: str | None = None) -> None:
    """Bir kullanıcıya bildirim oluşturur. Kendine bildirim oluşturulmaz."""
    if recipient_id == actor_id:
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


def _annotate(n: dict) -> dict:
    """Bildirime metin + hedef URL ekler (liste sayfası ve slide-in panel ortak kullanır)."""
    n["text"] = _TEXT.get(n["type"], "")
    if n["type"] == "hashtag_post" and n.get("hashtag"):
        n["text"] = f"#{n['hashtag']['tag']} etiketinde yeni post paylaştı"
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

    return jsonify(notifications=[{
        "type": n["type"],
        "text": n["text"],
        "target_url": n["target_url"],
        "username": n["actor_summary"],
        "avatar_url": n["actor"]["avatar_url"] if n.get("actor") else None,
        "created_at": n["created_at"],
        "is_read": n["is_read"],
    } for n in rows])


@bp.route("/unread-count")
@login_required
@retry_on_connection_error
def unread_count():
    """Navbar rozeti için JS polling ucu."""
    sb = get_sb()
    me = session["user"]["id"]
    count = sb.table("notifications").select(
        "id", count="exact", head=True
    ).eq("recipient_id", me).eq("is_read", False).execute().count or 0
    return jsonify(count=count)
