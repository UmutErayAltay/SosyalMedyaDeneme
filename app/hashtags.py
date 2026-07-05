"""Hashtag çıkarma, ilişkilendirme, güvenli render ve keşfet sayfası.

Post paylaşılırken içerikten #etiket'ler çıkarılıp hashtags/post_hashtags
tablolarına işlenir (sync_post_hashtags). Post içeriği gösterilirken
linkify_hashtags jinja filtresiyle #etiket'ler tıklanabilir link olur —
önce tüm içerik HTML-escape edilir, SONRA linkleme yapılır (XSS'e karşı
güvenli sıralama).
"""
import re
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, session, url_for
from markupsafe import Markup, escape
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error

bp = Blueprint("hashtags", __name__)

# Python 3'te \w zaten Unicode farkında (ç, ğ, ı, ö, ş, ü dahil)
HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


def extract_hashtags(content: str) -> list[str]:
    """İçerikten benzersiz, küçük harfli hashtag'leri (sırayı koruyarak) çıkarır."""
    if not content:
        return []
    seen: list[str] = []
    for m in HASHTAG_RE.finditer(content):
        tag = m.group(1).lower()
        if tag not in seen:
            seen.append(tag)
    return seen


def sync_post_hashtags(sb, post_id: str, content: str) -> None:
    """Post içeriğindeki hashtag'leri hashtags/post_hashtags tablolarına işler.

    Önce bu posta ait ESKİ eşleşmeler silinir, sonra içerikteki güncel
    hashtag'ler yeniden eklenir — bu fonksiyon hem post oluşturulunca hem
    DÜZENLENİNCE çağrılabilir (edit_post()) olsun diye idempotent: eski
    çağrı deseni (sadece insert) tekrar çağrılınca aynı (post_id, hashtag_id)
    çiftinde PK ihlali verip TÜM döngüyü sessizce iptal ederdi.

    Migration henüz uygulanmamışsa post paylaşımı bundan etkilenmesin diye
    sessizce atlanır (post zaten kaydedildi, hashtag indexleme ek bir adım).
    """
    try:
        sb.table("post_hashtags").delete().eq("post_id", post_id).execute()
    except Exception:
        return

    tags = extract_hashtags(content)
    if not tags:
        return
    try:
        for tag in tags:
            existing = sb.table("hashtags").select("id").eq("tag", tag).execute().data
            hashtag_id = existing[0]["id"] if existing else (
                sb.table("hashtags").insert({"tag": tag}).execute().data[0]["id"]
            )
            sb.table("post_hashtags").insert({
                "post_id": post_id, "hashtag_id": hashtag_id,
            }).execute()
    except Exception:
        pass


def linkify_hashtags(content: str):
    """Post içeriğini render eder, #etiket'leri tıklanabilir linke çevirir.

    Regex ÖNCE HAM (escape edilmemiş) metin üzerinde çalışır; her eşleşmeden
    önceki düz metin parçası AYRI AYRI escape edilir. Bunun tersini yapmak
    (önce tümünü escape edip regex'i escape edilmiş metinde çalıştırmak)
    hatalıdır: `"` karakteri escape'te `&#34;` olur ve regex bunun içindeki
    "#34"ü sahte bir hashtag sanıp HTML'i bozar.
    """
    if not content:
        return ""

    parts = []
    last_end = 0
    for m in HASHTAG_RE.finditer(content):
        parts.append(escape(content[last_end:m.start()]))
        tag = m.group(1)
        url = url_for("hashtags.hashtag_posts", tag=tag.lower())
        parts.append(Markup('<a href="{}" class="hashtag-link">#{}</a>').format(url, tag))
        last_end = m.end()
    parts.append(escape(content[last_end:]))

    return Markup("").join(parts)


@bp.route("/hashtag/<tag>")
@login_required
@retry_on_connection_error
def hashtag_posts(tag):
    from .routes import _attach_post_metrics  # döngüsel import'u önlemek için lazy
    from .mentions import get_valid_usernames
    from .visibility import followed_and_self_ids, filter_visible
    from .blocks import blocked_user_ids, filter_not_blocked
    from .polls import attach_polls

    sb = get_sb()
    me = session["user"]["id"]
    tag = tag.lower()

    posts = []
    try:
        ht = sb.table("hashtags").select("id").eq("tag", tag).execute().data
        if ht:
            rows = sb.table("post_hashtags").select("post_id").eq(
                "hashtag_id", ht[0]["id"]
            ).execute().data
            post_ids = [r["post_id"] for r in rows]
            if post_ids:
                posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", post_ids).order("created_at", desc=True).execute().data
                posts = filter_visible(posts, followed_and_self_ids(sb, me))
                posts = filter_not_blocked(posts, blocked_user_ids(sb, me))
                _attach_post_metrics(sb, posts, me)
                attach_polls(sb, posts, me)
    except Exception:
        pass  # sql/migration_hashtags.sql henüz uygulanmamışsa boş liste gösterilir

    return render_template("hashtag.html", tag=tag, posts=posts, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb))


def _trending_hashtags(sb, hours: int = 24, limit: int = 10) -> list[dict]:
    """Son `hours` saat içinde en çok kullanılan hashtag'ler.

    Sadece HERKESE AÇIK postlar sayılır — bir 'sadece takipçiler' postunun
    etiketi herkese açık gündem listesine sızmamalı. Engelleme ilişkileri
    hesaba KATILMIYOR (gündem viewer'a özel değil, paylaşılan/global bir
    liste — tam kişiselleştirme bu özelliğin amacını bozardı).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        recent_posts = sb.table("posts").select("id").gte(
            "created_at", cutoff
        ).eq("visibility", "public").execute().data
        post_ids = [p["id"] for p in recent_posts]
        if not post_ids:
            return []

        rows = sb.table("post_hashtags").select("hashtag_id").in_("post_id", post_ids).execute().data
        counts: dict = {}
        for r in rows:
            counts[r["hashtag_id"]] = counts.get(r["hashtag_id"], 0) + 1
        if not counts:
            return []

        top_ids = sorted(counts, key=lambda hid: counts[hid], reverse=True)[:limit]
        tags = sb.table("hashtags").select("id, tag").in_("id", top_ids).execute().data
        tag_by_id = {t["id"]: t["tag"] for t in tags}
        return [
            {"tag": tag_by_id[hid], "count": counts[hid]}
            for hid in top_ids if hid in tag_by_id
        ]
    except Exception:
        return []  # migration_hashtags.sql veya migration_post_visibility.sql henüz uygulanmamış olabilir
