"""Hashtag çıkarma, ilişkilendirme, güvenli render ve keşfet sayfası.

Post paylaşılırken içerikten #etiket'ler çıkarılıp hashtags/post_hashtags
tablolarına işlenir (sync_post_hashtags). Post içeriği gösterilirken
linkify_hashtags jinja filtresiyle #etiket'ler tıklanabilir link olur —
önce tüm içerik HTML-escape edilir, SONRA linkleme yapılır (XSS'e karşı
güvenli sıralama).
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for
from markupsafe import Markup, escape
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .notifications import notify
from .visibility import close_friend_author_ids

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
        # N+1 düzeltme: tüm hashtag'leri TÜM sorguda al
        existing_tags = sb.table("hashtags").select("id, tag").in_("tag", tags).execute().data
        tag_to_id = {t["tag"]: t["id"] for t in existing_tags}

        # Yok olanları toplu insert et (supabase-py batch insert desteği)
        missing_tags = [t for t in tags if t not in tag_to_id]
        if missing_tags:
            inserted = sb.table("hashtags").insert(
                [{"tag": t} for t in missing_tags]
            ).execute().data
            for row in inserted:
                tag_to_id[row["tag"]] = row["id"]

        # post_hashtags'ı toplu insert et
        post_hashtags_rows = [
            {"post_id": post_id, "hashtag_id": tag_to_id[t]}
            for t in tags
        ]
        sb.table("post_hashtags").insert(post_hashtags_rows).execute()
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
    is_following = False
    try:
        ht = sb.table("hashtags").select("id").eq("tag", tag).execute().data
        if ht:
            hashtag_id = ht[0]["id"]
            rows = sb.table("post_hashtags").select("post_id").eq(
                "hashtag_id", hashtag_id
            ).execute().data
            post_ids = [r["post_id"] for r in rows]
            if post_ids:
                posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", post_ids).eq("is_archived", False).eq("is_draft", False).order("created_at", desc=True).execute().data

                # Paralel: blocked_ids, followed_ids, close_friend_ids çek
                def _fetch_blocked():
                    return blocked_user_ids(sb, me)

                def _fetch_followed():
                    return followed_and_self_ids(sb, me)

                def _fetch_close_friends():
                    return close_friend_author_ids(sb, me)

                with ThreadPoolExecutor(max_workers=3) as executor:
                    blocked_future = executor.submit(_fetch_blocked)
                    followed_future = executor.submit(_fetch_followed)
                    close_future = executor.submit(_fetch_close_friends)

                    blocked_ids = blocked_future.result()
                    followed_ids = followed_future.result()
                    close_friend_ids = close_future.result()

                posts = filter_visible(sb, posts, followed_ids, close_friend_ids, me)
                posts = filter_not_blocked(posts, blocked_ids)
                _attach_post_metrics(sb, posts, me)
                attach_polls(sb, posts, me)

            is_following = bool(sb.table("hashtag_follows").select("user_id")
                                 .eq("user_id", me).eq("hashtag_id", hashtag_id).execute().data)
    except Exception:
        pass  # sql/migration_hashtags.sql henüz uygulanmamışsa boş liste gösterilir

    return render_template("hashtag.html", tag=tag, posts=posts, is_following=is_following,
                           me=session.get("user"), valid_usernames=get_valid_usernames(sb))


@bp.route("/hashtag/<tag>/follow", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_hashtag_follow(tag):
    """Bir etiketi takip et/bırak — takip edilen etikette yeni post paylaşılınca
    bildirim gider (bkz. notify_hashtag_followers, routes/posts.py create_post()'tan
    çağrılır)."""
    sb = get_sb()
    me = session["user"]["id"]
    tag = tag.lower()

    existing_tag = sb.table("hashtags").select("id").eq("tag", tag).execute().data
    hashtag_id = existing_tag[0]["id"] if existing_tag else (
        sb.table("hashtags").insert({"tag": tag}).execute().data[0]["id"]
    )

    existing = sb.table("hashtag_follows").select("user_id").eq(
        "user_id", me).eq("hashtag_id", hashtag_id).execute().data
    if existing:
        sb.table("hashtag_follows").delete().eq("user_id", me).eq("hashtag_id", hashtag_id).execute()
        following = False
    else:
        sb.table("hashtag_follows").insert({"user_id": me, "hashtag_id": hashtag_id}).execute()
        following = True

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(following=following)
    return redirect(url_for("hashtags.hashtag_posts", tag=tag))


def notify_hashtag_followers(sb, actor_id: str, post_id: str, tags: list[str]) -> None:
    """Yeni paylaşılan bir posttaki etiketleri takip eden kullanıcılara bildirim
    gönderir. SADECE post OLUŞTURULUNCA çağrılır (edit_post() çağırmaz) —
    mention bildirimlerindeki "sadece yeni eklenenler" ayrımı burada yok,
    basitlik için: bir postu düzenleyip etiket eklemek ekstra bildirim
    üretmez (küçük ölçekli bir arkadaş grubu uygulamasında kabul edilebilir
    bir sınır)."""
    if not tags:
        return
    try:
        hashtag_rows = sb.table("hashtags").select("id").in_("tag", tags).execute().data
    except Exception:
        return

    if not hashtag_rows:
        return

    # N+1 düzeltme: tüm hashtag'ler için followers'ı tek sorguda al
    hashtag_ids = [h["id"] for h in hashtag_rows]
    try:
        all_follows = sb.table("hashtag_follows").select("hashtag_id, user_id").in_(
            "hashtag_id", hashtag_ids
        ).execute().data
    except Exception:
        return

    # hashtag_id -> [user_id, ...] haritası oluştur
    follows_by_hashtag = {}
    for f in all_follows:
        follows_by_hashtag.setdefault(f["hashtag_id"], []).append(f["user_id"])

    for h in hashtag_rows:
        followers = follows_by_hashtag.get(h["id"], [])
        for follower_id in followers:
            notify(sb, recipient_id=follower_id, actor_id=actor_id, type_="hashtag_post",
                   post_id=post_id, hashtag_id=h["id"])


@bp.route("/gundem")
@login_required
@retry_on_connection_error
def trending_all():
    """Gündemdeki TÜM etiketler — feed sidebar'ındaki ilk 5'in "Tümünü gör" hedefi."""
    sb = get_sb()
    tags = _trending_hashtags(sb, hours=24, limit=50)
    return render_template("trending.html", tags=tags, me=session.get("user"))


def _trending_hashtags(sb, hours: int = 24, limit: int = 10) -> list[dict]:
    """Son `hours` saat içinde en çok kullanılan hashtag'ler.

    Sadece HERKESE AÇIK postlar sayılır — bir 'sadece takipçiler' postunun
    etiketi herkese açık gündem listesine sızmamalı. Engelleme ilişkileri
    hesaba KATILMIYOR (gündem viewer'a özel değil, paylaşılan/global bir
    liste — tam kişiselleştirme bu özelliğin amacını bozardı). 120 saniye
    TTL ile cache'lenir.
    """
    from .cache import get_cached
    # Cache anahtarı limit'ten BAĞIMSIZ: her sayfa farklı limit istiyor
    # (feed 10, post detay 5, /gundem 50) — anahtar limit içerince her
    # sayfa ayrı anda hesaplanan ayrı kopyalar görüyordu ve biri boşken
    # diğeri dolu olabiliyordu (kullanıcı raporu). Tek liste (50) cache'lenir,
    # limit sadece dilimler.
    cache_key = f"trending:{hours}"

    def _fetch():
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        # try/except BİLEREK yok: geçici bir sorgu hatası [] dönerse o boş
        # liste 120sn cache'lenip gündemi "rastgele kayboluyor" gösteriyordu —
        # exception yukarı çıkar, get_cached cache'e yazmaz, dışarıda yakalanır.
        recent_posts = sb.table("posts").select("id").gte(
            "created_at", cutoff
        ).eq("visibility", "public").eq("is_archived", False).eq("is_draft", False).execute().data
        post_ids = [p["id"] for p in recent_posts]
        if not post_ids:
            return []

        rows = sb.table("post_hashtags").select("hashtag_id").in_("post_id", post_ids).execute().data
        counts: dict = {}
        for r in rows:
            counts[r["hashtag_id"]] = counts.get(r["hashtag_id"], 0) + 1
        if not counts:
            return []

        top_ids = sorted(counts, key=lambda hid: counts[hid], reverse=True)[:50]
        tags = sb.table("hashtags").select("id, tag").in_("id", top_ids).execute().data
        tag_by_id = {t["id"]: t["tag"] for t in tags}
        return [
            {"tag": tag_by_id[hid], "count": counts[hid]}
            for hid in top_ids if hid in tag_by_id
        ]

    try:
        return get_cached(cache_key, 120, _fetch)[:limit]
    except Exception:
        return []  # migration eksik veya geçici hata — sayfa kırılmasın, cache'lenmez
