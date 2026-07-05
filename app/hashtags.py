"""Hashtag çıkarma, ilişkilendirme, güvenli render ve keşfet sayfası.

Post paylaşılırken içerikten #etiket'ler çıkarılıp hashtags/post_hashtags
tablolarına işlenir (sync_post_hashtags). Post içeriği gösterilirken
linkify_hashtags jinja filtresiyle #etiket'ler tıklanabilir link olur —
önce tüm içerik HTML-escape edilir, SONRA linkleme yapılır (XSS'e karşı
güvenli sıralama).
"""
import re
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

    Migration henüz uygulanmamışsa post paylaşımı bundan etkilenmesin diye
    sessizce atlanır (post zaten kaydedildi, hashtag indexleme ek bir adım).
    """
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
                _attach_post_metrics(sb, posts, me)
    except Exception:
        pass  # sql/migration_hashtags.sql henüz uygulanmamışsa boş liste gösterilir

    return render_template("hashtag.html", tag=tag, posts=posts, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb))
