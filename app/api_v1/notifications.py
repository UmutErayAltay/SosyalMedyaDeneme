from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..notifications import (
    _annotate, _cleanup_old_notifications,
    _GROUPABLE_TYPES, PAGE_SIZE as NOTIF_PAGE_SIZE,
)
from ..cache import invalidate, get_cached


# ----------------------- BİLDİRİMLER (Faz 4, native Android) -----------------------
# app/notifications.py list_notifications()/unread_count()'un AYNI iş mantığı
# JSON'a taşınır. KRİTİK SAPMA: web'in _TARGET_BUILDERS'ı url_for() ile bir WEB
# ROUTE STRING'i (`/post/<id>`, `/u/<username>`...) üretiyor — native bunu
# KULLANAMAZ (kendi Kotlin route'ları var). Bu yüzden `target_url` yerine
# native'in kendi navigasyon kararını verebileceği HAM alanlar (post_id/
# username/conversation_id/hashtag) döner; `text`/`actor_summary` AYNEN
# (notifications.py'nin ürettiği Türkçe metin, burada ayrı bir metin sözlüğü
# İCAT EDİLMEZ — _annotate() reuse edilir).
#
# app/notifications.py'nin `_group_notifications()`'ı target_url üretip ham
# id'leri attığı için doğrudan reuse edilemedi (web davranışını BOZMADAN, o
# fonksiyon DEĞİŞTİRİLMEDİ) — bu yüzden AYNI gruplama algoritmasının ham
# id'leri koruyan bir kopyası (_group_notifications_native) burada tutulur.
# Benzer şekilde `_fetch_and_mark_read()` de _group_notifications()'ı içeride
# çağırdığı için reuse edilemedi; sorgu+okundu-işaretleme mantığı
# (_fetch_notifications_native) burada BİREBİR aynı şekilde tekrarlanır.

# username alanı sadece TEK aktörlü türlerde anlamlı — like/comment_like
# gruplu olabilir (birden fazla aktör), o zaman hangi profile gidileceği
# belirsizleşir; zaten bu türler post_id'ye gider, profile'a değil.
# follow_request BİLİNÇLİ olarak dışarıda: web'de bu tür bir profile değil,
# social.list_follow_requests()'e gidiyor — native'de zaten var olan
# GET /api/v1/follow-requests ekranına karşılık gelir, type=="follow_request"
# bilgisi yeterli, ekstra bir username alanı gerekmez.
_NATIVE_USERNAME_TYPES = {"follow", "follow_accept", "story_reaction"}


def _group_notifications_native(rows: list[dict]) -> list[dict]:
    """notifications._group_notifications ile AYNI gruplama algoritması — web'i
    ETKİLEMEMEK için ayrı bir kopya (bkz. bölüm başındaki not)."""
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
            "text": n["text"],
            "created_at": n["created_at"],
            "is_read": n["is_read"],
            "post_id": n.get("post_id"),
            "conversation_id": n.get("conversation_id"),
            "hashtag": n["hashtag"]["tag"] if n.get("hashtag") else None,
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
        actor = actors[0] if actors else None
        g["avatar_url"] = actor.get("avatar_url") if actor else None
        g["username"] = actor.get("username") if (actor and g["type"] in _NATIVE_USERNAME_TYPES) else None

    return groups


def _fetch_notifications_native(sb, me: str, limit: int, offset: int = 0) -> tuple[list[dict], bool]:
    """notifications._fetch_and_mark_read ile AYNI sorgu + okundu-işaretleme
    mantığı (bkz. bölüm başındaki not — reuse edilemediği için tekrarlanır)."""
    rows = sb.table("notifications").select(
        "*, actor:profiles!notifications_actor_id_fkey(username, avatar_url), hashtag:hashtags(tag)"
    ).eq("recipient_id", me).order(
        "created_at", desc=True
    ).range(offset, offset + limit).execute().data

    has_next = len(rows) > limit
    rows = rows[:limit]
    for n in rows:
        _annotate(n)  # text alanını (target_url burada kullanılmıyor) doldurur

    unread_ids = [n["id"] for n in rows if not n["is_read"]]
    if unread_ids:
        sb.table("notifications").update({"is_read": True}).in_("id", unread_ids).execute()
        invalidate(f"unread:{me}")

    return rows, has_next


@bp.route("/notifications")
@api_login_required
def api_list_notifications():
    """Bildirim listesi — notifications.py list_notifications()'ın AYNI mantığı
    (page=1'de eski bildirim temizliği, görüntülenenler okundu işaretlenir).
    Alan sözleşmesi/mimari sapma için bölüm başındaki nota bak."""
    sb = get_sb()
    me = request.api_user["id"]
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * NOTIF_PAGE_SIZE

    if page == 1:
        _cleanup_old_notifications(sb, me)

    rows, has_next = _fetch_notifications_native(sb, me, NOTIF_PAGE_SIZE, offset)
    groups = _group_notifications_native(rows)

    return jsonify(
        notifications=[{
            "type": g["type"],
            "actor_summary": g["actor_summary"],
            "avatar_url": g["avatar_url"],
            "text": g["text"],
            "created_at": g["created_at"],
            "is_read": g["is_read"],
            "post_id": g["post_id"],
            "username": g["username"],
            "conversation_id": g["conversation_id"],
            "hashtag": g["hashtag"],
        } for g in groups],
        has_next=has_next,
    )


@bp.route("/notifications/unread-count")
@api_login_required
def api_unread_notifications_count():
    """Okunmamış bildirim sayısı — notifications.py unread_count()'un AYNI
    20sn TTL cache'li mantığı."""
    sb = get_sb()
    me = request.api_user["id"]

    def _fetch():
        return sb.table("notifications").select(
            "id", count="exact", head=True
        ).eq("recipient_id", me).eq("is_read", False).execute().count or 0

    count = get_cached(f"unread:{me}", 20, _fetch)
    return jsonify(count=count)
