"""routes/ paketindeki tüm alt-modüllerin paylaştığı küçük yardımcılar.

Bu dosya sadece bp'ye route TANIMLAMAZ — sadece paylaşılan helper'ları
barındırır (bkz. `app/routes/__init__.py`'nin bunları neden re-export ettiği:
`hashtags.py` `_attach_post_metrics`'i döngüsel import'u önlemek için lazy
import ediyor, `from .routes import _attach_post_metrics` şeklinde — bu paket
haline gelince de aynı import yolu çalışmaya devam etmeli).
"""
from concurrent.futures import ThreadPoolExecutor
from flask import session
from ..supabase_client import get_sb

PAGE_SIZE = 20


def fetch_stats_and_bio(sb, me: str) -> tuple[dict, str | None]:
    """post/takipçi/takip sayısı + bio — TEK RPC çağrısı (sql/migration_sidebar_stats_rpc.sql).

    Önceden feed()/fetch_sidebar_context() içinde 4 ayrı Supabase sorgusuydu
    (paralel de olsa 4 ayrı round-trip); RPC henüz uygulanmamışsa veya
    başarısız olursa eski çok-sorgulu yola (fallback) düşülür. feed(),
    discover() ve post_detail() sidebar'ları HEPSİ bu tek helper'ı kullanır.
    """
    try:
        data = sb.rpc("sidebar_stats", {"p_me": me}).execute().data or {}
        return {
            "posts": data.get("posts_count", 0),
            "followers": data.get("followers_count", 0),
            "following": data.get("following_count", 0),
        }, data.get("bio")
    except Exception:
        def _posts_count():
            try:
                return sb.table("posts").select(
                    "id", count="exact", head=True
                ).eq("user_id", me).eq("is_draft", False).execute().count or 0
            except Exception:
                return 0

        def _followers_count():
            try:
                return sb.table("follows").select(
                    "follower_id", count="exact", head=True
                ).eq("following_id", me).execute().count or 0
            except Exception:
                return 0

        def _following_count():
            try:
                return sb.table("follows").select(
                    "following_id", count="exact", head=True
                ).eq("follower_id", me).execute().count or 0
            except Exception:
                return 0

        def _bio():
            try:
                rows = sb.table("profiles").select("bio").eq("id", me).execute().data
                return (rows[0].get("bio") if rows else None) or None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=4) as fb_executor:
            pc_fut = fb_executor.submit(_posts_count)
            fc_fut = fb_executor.submit(_followers_count)
            gc_fut = fb_executor.submit(_following_count)
            bio_fut = fb_executor.submit(_bio)
            return {
                "posts": pc_fut.result(),
                "followers": fc_fut.result(),
                "following": gc_fut.result(),
            }, bio_fut.result()


def fetch_sidebar_context(sb, me: str, include_activity: bool = True) -> dict:
    """Feed/keşfet/post detay yan panellerinin ortak verisi — hepsi paralel.

    Döner: my_stats, my_bio, close_friends_preview, suggested_users,
    trending_tags, recent_activity. Feed kendi FAZ B bloğunu kullanmaya devam
    eder (ekstra alanları var, `fetch_stats_and_bio` ortak); bu helper keşfet
    + post detay içindir.
    """
    # Lazy import: hashtags.py routes'tan lazy import yapıyor, ters yönde
    # top-level import döngü riskini sıfırlamak için burada da lazy tutulur.
    from ..hashtags import _trending_hashtags
    from ..blocks import blocked_user_ids

    def _close_friends():
        try:
            cf_rows = sb.table("close_friends").select(
                "profiles!close_friends_friend_id_fkey(id, username, avatar_url)"
            ).eq("owner_id", me).order("created_at", desc=True).limit(6).execute().data
            return [r["profiles"] for r in cf_rows if r.get("profiles")]
        except Exception:
            return []

    def _following_ids():
        try:
            return {f["following_id"] for f in sb.table("follows").select("following_id")
                    .eq("follower_id", me).execute().data}
        except Exception:
            return set()

    def _recent_activity():
        if not include_activity:
            return []
        try:
            return sb.table("notifications").select(
                "type, post_id, created_at, profiles!notifications_actor_id_fkey(username, avatar_url)"
            ).eq("recipient_id", me).order("created_at", desc=True).limit(5).execute().data
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as executor:
        stats_bio_fut = executor.submit(fetch_stats_and_bio, sb, me)
        close_friends_fut = executor.submit(_close_friends)
        trending_fut = executor.submit(_trending_hashtags, sb, hours=24, limit=10)
        following_ids_fut = executor.submit(_following_ids)
        blocked_fut = executor.submit(blocked_user_ids, sb, me)
        activity_fut = executor.submit(_recent_activity)

        my_stats, my_bio = stats_bio_fut.result()
        close_friends_preview = close_friends_fut.result()
        trending_tags = trending_fut.result()
        following_ids = following_ids_fut.result()
        blocked_ids = blocked_fut.result()
        recent_activity = activity_fut.result()

        def _suggested_users():
            exclude_ids = following_ids | blocked_ids | {me}
            query = sb.table("profiles").select(
                "id, username, avatar_url, full_name"
            ).eq("is_banned", False)
            if exclude_ids:
                query = query.not_.in_("id", list(exclude_ids))
            try:
                return query.order("created_at", desc=True).limit(5).execute().data
            except Exception:
                return []

        suggested_users = executor.submit(_suggested_users).result()

    return {
        "my_stats": my_stats,
        "my_bio": my_bio,
        "close_friends_preview": close_friends_preview,
        "suggested_users": suggested_users,
        "trending_tags": trending_tags,
        "recent_activity": recent_activity,
    }


def _profile(username: str | None = None, uid: str | None = None) -> dict | None:
    """Verilen username veya id ile profil döndürür."""
    sb = get_sb()
    if uid:
        res = sb.table("profiles").select("*").eq("id", uid).execute()
    elif username:
        res = sb.table("profiles").select("*").eq("username", username).execute()
    else:
        return None
    return res.data[0] if res.data else None


def _my_id() -> str:
    return session["user"]["id"]


def _can_view_post(sb, post: dict, me: str) -> bool:
    """Post görüntülemeye izin verilen postları kontrol eder.

    - Sahibi ise True
    - Arşivlenmiş ise False (sahibi dışında kimse göremez)
    - İki-yönlü engel varsa False
    - Yazarın profili gizli (is_private=True) ve viewer accepted-takipçi değilse False
    - visibility="followers" ve viewer accepted-takipçi değilse False
    - visibility="close_friends" ve viewer yakın arkadaş değilse False
    - Aksi halde True
    """
    from ..blocks import is_blocked_either_way

    # Sahibi görebilir
    if post.get("user_id") == me:
        return True

    # Arşivlenmiş post sadece sahibe görünür
    if post.get("is_archived"):
        return False

    # Engelleme kontrolü
    if is_blocked_either_way(sb, me, post.get("user_id")):
        return False

    # Yazarın profili gizli mi kontrol et
    try:
        author_profile = sb.table("profiles").select("is_private").eq(
            "id", post.get("user_id")
        ).execute().data
        if author_profile and author_profile[0].get("is_private"):
            # Yazarın profili gizli — accepted takipçi mi kontrol et
            follow = sb.table("follows").select("status").eq(
                "follower_id", me
            ).eq("following_id", post.get("user_id")).execute().data
            if not follow or follow[0].get("status") != "accepted":
                return False
    except Exception:
        pass

    # Visibility kontrolleri
    visibility = post.get("visibility", "public")

    if visibility == "followers":
        # Sadece accepted takipçiler görebilir
        follow = sb.table("follows").select("status").eq(
            "follower_id", me
        ).eq("following_id", post.get("user_id")).execute().data
        if not follow or follow[0].get("status") != "accepted":
            return False

    elif visibility == "close_friends":
        # Sadece yakın arkadaşlar görebilir
        is_close = sb.table("close_friends").select("owner_id").eq(
            "owner_id", post.get("user_id")
        ).eq("friend_id", me).execute().data
        if not is_close:
            return False

    return True


def _attach_post_metrics(sb, posts: list, me: str) -> None:
    """Postlara like_count / comment_count / liked_by_me / my_reaction /
    bookmarked_by_me ekler.

    Sayılar embedded count ile tek sorguda gelir; kullanıcıya özel alanlar için
    tüm postlar üzerinden tek birer IN sorgusu yapılır (N+1 önlenir).
    """
    post_ids = [p["id"] for p in posts]
    my_reactions: dict = {}
    my_bookmarks: set = set()
    if post_ids:
        my_reactions = {
            l["post_id"]: l.get("reaction_type") or "like"
            for l in sb.table("likes").select("post_id, reaction_type")
            .eq("user_id", me).in_("post_id", post_ids).execute().data
        }
        try:
            my_bookmarks = {
                b["post_id"] for b in sb.table("bookmarks").select("post_id")
                .eq("user_id", me).in_("post_id", post_ids).execute().data
            }
        except Exception:
            pass  # sql/migration_bookmarks.sql henüz uygulanmamışsa sessizce atla
    for p in posts:
        p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
        p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
        p["liked_by_me"] = p["id"] in my_reactions
        p["my_reaction"] = my_reactions.get(p["id"])
        p["bookmarked_by_me"] = p["id"] in my_bookmarks
