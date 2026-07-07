"""Profil sayfası, takipçi/takip listeleri, profil düzenleme, istatistikler."""
from datetime import datetime, timedelta, timezone
from flask import render_template, request, redirect, url_for, session, abort, flash
from . import bp
from ._common import _my_id, _profile, _attach_post_metrics
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..storage_helper import upload_image
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids, close_friend_author_ids, filter_visible
from ..blocks import blocked_user_ids, filter_not_blocked, has_blocked
from ..polls import attach_polls
from ..stories import _get_highlights


@bp.route("/u/<username>")
@login_required
@retry_on_connection_error
def profile(username):
    from concurrent.futures import ThreadPoolExecutor

    sb = get_sb()
    prof = sb.table("profiles").select("*").eq("username", username).execute()
    if not prof.data:
        abort(404)
    prof = prof.data[0]

    me = _my_id()
    is_self = me == prof["id"]

    # Engelleme: ONLAR beni engellemişse profil hiç yokmuş gibi davran
    # (enumeration önleme). BEN onları engellemişsem profili YİNE DE
    # gösteririm (engeli kaldırabilmem için gerekli) — sadece içerik
    # aşağıdaki filter_not_blocked ile gizlenir.
    if not is_self and has_blocked(sb, prof["id"], me):
        abort(404)

    # Performans: visibility filtreleri ve engagement metrikleri paralel çekilir.
    # Bağımlılık: followed/close_friend/blocked_ids → posts'tan SONRA kullanılır.
    # posts → metrics/polls/liked-bookmarked işleri.

    def _fetch_visible_author_ids():
        return followed_and_self_ids(sb, me)

    def _fetch_close_friend_ids():
        return close_friend_author_ids(sb, me)

    def _fetch_blocked_ids():
        return blocked_user_ids(sb, me)

    def _fetch_liked_rows():
        try:
            return sb.table("likes").select("post_id").eq(
                "user_id", prof["id"]
            ).order("created_at", desc=True).execute().data
        except Exception:
            return []

    def _fetch_bookmark_collections():
        if not is_self:
            return []
        try:
            return sb.table("bookmark_collections").select("id, name").eq(
                "user_id", me
            ).order("created_at").execute().data
        except Exception:
            return []

    def _fetch_bookmarks_raw():
        if not is_self:
            return []
        try:
            return sb.table("bookmarks").select("post_id, collection_id").eq(
                "user_id", me
            ).order("created_at", desc=True).execute().data
        except Exception:
            return []

    def _fetch_followers_count():
        try:
            return sb.table("follows").select(
                "follower_id", count="exact", head=True
            ).eq("following_id", prof["id"]).execute().count or 0
        except Exception:
            return 0

    def _fetch_following_count():
        try:
            return sb.table("follows").select(
                "following_id", count="exact", head=True
            ).eq("follower_id", prof["id"]).execute().count or 0
        except Exception:
            return 0

    def _fetch_is_following():
        if is_self:
            return False
        try:
            f = sb.table("follows").select().eq("follower_id", me).eq(
                "following_id", prof["id"]
            ).execute()
            return bool(f.data)
        except Exception:
            return False

    def _fetch_highlights():
        return _get_highlights(sb, prof["id"])

    with ThreadPoolExecutor(max_workers=10) as executor:
        # Level 1: Filtreleme işleri (posts'tan bağımsız başlanabilir)
        visible_fut = executor.submit(_fetch_visible_author_ids)
        close_friend_fut = executor.submit(_fetch_close_friend_ids)
        blocked_fut = executor.submit(_fetch_blocked_ids)

        # Level 1b: posts'tan bağımsız engagement işleri
        liked_rows_fut = executor.submit(_fetch_liked_rows)
        collections_fut = executor.submit(_fetch_bookmark_collections)
        bookmarks_raw_fut = executor.submit(_fetch_bookmarks_raw)
        followers_fut = executor.submit(_fetch_followers_count)
        following_fut = executor.submit(_fetch_following_count)
        is_following_fut = executor.submit(_fetch_is_following)
        highlights_fut = executor.submit(_fetch_highlights)
        usernames_fut = executor.submit(get_valid_usernames, sb)

        # Filtreleri bekle (posts sorgusu için gerekli)
        visible_author_ids = visible_fut.result()
        close_friend_ids = close_friend_fut.result()
        blocked_ids = blocked_fut.result()

        # Level 2 (filtreleri bekledikten sonra): posts sorgusu
        def _fetch_posts_filtered():
            posts_data = sb.table("posts").select(
                "*, likes(count), comments(count)"
            ).eq("user_id", prof["id"]).order("created_at", desc=True).execute().data
            posts_data = [p for p in posts_data if not p.get("is_draft")]
            posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
            posts_data = filter_not_blocked(posts_data, blocked_ids)
            # Sabitlenmiş post en üste taşınır
            pinned_id = prof.get("pinned_post_id")
            if pinned_id:
                posts_data.sort(key=lambda p: 0 if p["id"] == pinned_id else 1)
            return posts_data

        posts_fut = executor.submit(_fetch_posts_filtered)

        # Level 1b sonuçlarını topla (çoğu hazır)
        liked_rows = liked_rows_fut.result()
        liked_ids = [l["post_id"] for l in liked_rows]
        bookmark_collections = collections_fut.result()
        bookmarks_raw = bookmarks_raw_fut.result()
        followers_count = followers_fut.result()
        following_count = following_fut.result()
        is_following = is_following_fut.result()
        highlights = highlights_fut.result()
        valid_usernames = usernames_fut.result()

        # Level 2 sonucunu topla
        posts = posts_fut.result()

        # Level 3 (posts bitince): metrics ve polls
        metrics_fut = executor.submit(_attach_post_metrics, sb, posts, me)
        polls_fut = executor.submit(attach_polls, sb, posts, me)

        # Level 3+ (liked_rows + filtreler bitince): liked_posts ve bookmarked_posts
        def _fetch_liked_posts():
            if not liked_ids:
                return []
            posts_data = sb.table("posts").select(
                "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
            ).in_("id", liked_ids).execute().data
            posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
            posts_data = filter_not_blocked(posts_data, blocked_ids)
            _attach_post_metrics(sb, posts_data, me)
            attach_polls(sb, posts_data, me)
            # .in_() sırayı garanti etmez; beğeni sırasına (liked_ids) göre yeniden diz
            order = {pid: i for i, pid in enumerate(liked_ids)}
            posts_data.sort(key=lambda p: order.get(p["id"], 0))
            return posts_data

        def _fetch_bookmarked_posts():
            if not bookmarks_raw:
                return []
            bm_ids = [b["post_id"] for b in bookmarks_raw]
            collection_by_post = {b["post_id"]: b.get("collection_id") for b in bookmarks_raw}
            if not bm_ids:
                return []
            posts_data = sb.table("posts").select(
                "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
            ).in_("id", bm_ids).execute().data
            posts_data = filter_visible(posts_data, visible_author_ids, close_friend_ids)
            posts_data = filter_not_blocked(posts_data, blocked_ids)
            _attach_post_metrics(sb, posts_data, me)
            attach_polls(sb, posts_data, me)
            bm_order = {pid: i for i, pid in enumerate(bm_ids)}
            posts_data.sort(key=lambda p: bm_order.get(p["id"], 0))
            for p in posts_data:
                p["bookmark_collection_id"] = collection_by_post.get(p["id"])
            return posts_data

        liked_posts_fut = executor.submit(_fetch_liked_posts)
        bookmarked_posts_fut = executor.submit(_fetch_bookmarked_posts)

        # Level 3 sonuçlarını topla (metrics ve polls posts'u modifiye ediyor)
        metrics_fut.result()
        polls_fut.result()

        # Level 3+ sonuçlarını topla
        liked_posts = liked_posts_fut.result()
        bookmarked_posts = bookmarked_posts_fut.result()

    # Medya sekmesi: görsel içeren postlar (ek sorgu yok, mevcut listeden süzülür)
    media_posts = [p for p in posts if p.get("image_urls") or p.get("image_url")]

    # Toplam beğeni (kullanıcının tüm postlarına gelen)
    total_likes = sum(p["like_count"] for p in posts)

    is_blocked_by_me = False
    if not is_self:
        is_blocked_by_me = prof["id"] in blocked_ids

    return render_template("profile.html", profile=prof, posts=posts,
                           media_posts=media_posts, liked_posts=liked_posts,
                           bookmarked_posts=bookmarked_posts,
                           bookmark_collections=bookmark_collections,
                           is_self=is_self, is_following=is_following,
                           is_blocked_by_me=is_blocked_by_me, me=session.get("user"),
                           valid_usernames=valid_usernames,
                           highlights=highlights,
                           stats={
                               "posts": len(posts),
                               "followers": followers_count,
                               "following": following_count,
                               "likes": total_likes,
                           })


def _follow_list(username: str, kind: str):
    """Takipçi ('followers') veya takip edilen ('following') listesi ortak render'ı."""
    sb = get_sb()
    prof = _profile(username=username)
    if not prof:
        abort(404)
    me = _my_id()

    if kind == "followers":
        rows = sb.table("follows").select(
            "profiles!follows_follower_id_fkey(id, username, avatar_url, full_name)"
        ).eq("following_id", prof["id"]).execute().data
        title = "Takipçiler"
    else:
        rows = sb.table("follows").select(
            "profiles!follows_following_id_fkey(id, username, avatar_url, full_name)"
        ).eq("follower_id", prof["id"]).execute().data
        title = "Takip Edilenler"

    users = [r["profiles"] for r in rows if r.get("profiles")]

    # Ben bu kullanıcıları takip ediyor muyum? (her satırdaki takip butonu için)
    user_ids = [u["id"] for u in users]
    following_ids = set()
    if user_ids:
        following_ids = {
            f["following_id"] for f in sb.table("follows").select("following_id")
            .eq("follower_id", me).in_("following_id", user_ids).execute().data
        }
    for u in users:
        u["is_following"] = u["id"] in following_ids
        u["is_self"] = u["id"] == me

    return render_template("follow_list.html", profile=prof, users=users,
                           title=title, kind=kind, me=session.get("user"))


@bp.route("/u/<username>/followers")
@login_required
@retry_on_connection_error
def followers_list(username):
    return _follow_list(username, "followers")


@bp.route("/u/<username>/following")
@login_required
@retry_on_connection_error
def following_list(username):
    return _follow_list(username, "following")


@bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
@retry_on_connection_error
def profile_edit():
    sb = get_sb()
    me = _my_id()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        bio = request.form.get("bio", "").strip()
        username = request.form.get("username", "").strip()
        avatar_file = request.files.get("avatar")

        if not username or len(username) < 3:
            flash("Kullanıcı adı en az 3 karakter olmalı.", "error")
            return redirect(url_for("routes.profile_edit"))

        # Kullanıcı adı başkası tarafından kullanılıyor mu?
        if username != session["user"].get("username", ""):
            taken = sb.table("profiles").select("id").eq("username", username).neq(
                "id", me
            ).execute()
            if taken.data:
                flash("Bu kullanıcı adı zaten alınmış.", "error")
                return redirect(url_for("routes.profile_edit"))

        # Güncellenecek alanlar
        update_data = {"full_name": full_name or None, "bio": bio or None, "username": username}

        # Avatar yüklendiyse
        if avatar_file and avatar_file.filename:
            avatar_url = upload_image(avatar_file, folder="avatars")
            if avatar_url:
                update_data["avatar_url"] = avatar_url
            else:
                flash("Avatar yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
                return redirect(url_for("routes.profile_edit"))

        # Profili güncelle
        sb.table("profiles").update(update_data).eq("id", me).execute()

        # Session'daki kullanıcı bilgilerini senkronize et (navbar avatarı için)
        session["user"]["username"] = username
        if "avatar_url" in update_data:
            session["user"]["avatar_url"] = update_data["avatar_url"]
        session.modified = True

        flash("Profil güncellendi.", "success")
        return redirect(url_for("routes.profile", username=username))

    # GET: mevcut profil bilgilerini göster
    prof = sb.table("profiles").select("*").eq("id", me).execute()
    if not prof.data:
        abort(404)
    return render_template("profile_edit.html", profile=prof.data[0], me=session["user"])


def _daily_counts(rows: list, days: int) -> list[dict]:
    """`rows` (her biri 'created_at' ISO string alanı olan) listesini son
    `days` gün için günlük sayıma çevirir (en eski→en yeni, boş günler 0)."""
    counts: dict = {}
    for r in rows:
        day = r["created_at"][:10]
        counts[day] = counts.get(day, 0) + 1
    today = datetime.now(timezone.utc).date()
    return [
        {"date": (today - timedelta(days=i)).isoformat(),
         "count": counts.get((today - timedelta(days=i)).isoformat(), 0)}
        for i in range(days - 1, -1, -1)
    ]


_GUN_ADLARI = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _day_of_week_counts(rows: list) -> list[dict]:
    """`rows` (her biri 'created_at' ISO string alanı olan) listesini haftanın
    günlerine göre sayıma çevirir (Pazartesi→Pazar sırayla, TÜM zamanlar,
    days penceresinden bağımsız — 'hangi gün daha çok paylaşım yapıyorsun' sabit bir alışkanlık sorusu)."""
    counts = [0] * 7
    for r in rows:
        wd = datetime.fromisoformat(r["created_at"]).weekday()  # 0=Pazartesi
        counts[wd] += 1
    return [{"day": _GUN_ADLARI[i], "count": counts[i]} for i in range(7)]


@bp.route("/insights")
@login_required
@retry_on_connection_error
def insights():
    """Kendi postlarının beğeni/yorum trendini gösteren basit bir sayfa —
    yeni bir tablo gerekmez, mevcut likes/comments/posts/follows üzerinden
    hesaplanır."""
    sb = get_sb()
    me = _my_id()

    # Sadece 7/14/30 kabul edilir; kullanıcı URL'yi elle bozarsa (örn.
    # ?days=999) sessizce 14'e düşülür — bu bir istatistik sayfası, hata
    # sayfası göstermeye değmez.
    days = request.args.get("days", 14, type=int)
    if days not in (7, 14, 30):
        days = 14

    posts = sb.table("posts").select(
        "id, content, created_at, likes(count), comments(count)"
    ).eq("user_id", me).order("created_at", desc=True).execute().data
    for p in posts:
        p["like_count"] = p["likes"][0]["count"] if p.get("likes") else 0
        p["comment_count"] = p["comments"][0]["count"] if p.get("comments") else 0
        p["engagement"] = p["like_count"] + p["comment_count"]

    total_posts = len(posts)
    total_likes = sum(p["like_count"] for p in posts)
    total_comments = sum(p["comment_count"] for p in posts)

    post_ids = [p["id"] for p in posts]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).isoformat()
    likes_recent, comments_recent = [], []
    if post_ids:
        likes_recent = sb.table("likes").select("created_at").in_(
            "post_id", post_ids
        ).gte("created_at", cutoff).execute().data
        comments_recent = sb.table("comments").select("created_at").in_(
            "post_id", post_ids
        ).gte("created_at", cutoff).execute().data

    likes_by_day = _daily_counts(likes_recent, days)
    comments_by_day = _daily_counts(comments_recent, days)

    follows_recent = sb.table("follows").select("created_at").eq(
        "following_id", me
    ).gte("created_at", cutoff).execute().data
    followers_by_day = _daily_counts(follows_recent, days)

    max_daily = max(
        [d["count"] for d in likes_by_day + comments_by_day + followers_by_day] + [1]
    )

    total_followers = sb.table("follows").select(
        "follower_id", count="exact", head=True
    ).eq("following_id", me).execute().count or 0
    total_following = sb.table("follows").select(
        "following_id", count="exact", head=True
    ).eq("follower_id", me).execute().count or 0

    avg_engagement = round((total_likes + total_comments) / total_posts, 1) if total_posts else 0

    # Haftanın günü dağılımı TÜM postlar üzerinden hesaplanır (days
    # penceresinden bağımsız) — "hangi gün daha çok paylaşım yapıyorsun"
    # sabit bir alışkanlık sorusu, son N günle sınırlı olmamalı.
    day_of_week_stats = _day_of_week_counts(posts)
    most_active_day = (
        max(day_of_week_stats, key=lambda d: d["count"])["day"]
        if any(d["count"] > 0 for d in day_of_week_stats) else None
    )

    top_posts = sorted(posts, key=lambda p: p["engagement"], reverse=True)[:5]

    return render_template("insights.html", me=session.get("user"),
                           days=days,
                           total_posts=total_posts, total_likes=total_likes,
                           total_comments=total_comments, likes_by_day=likes_by_day,
                           comments_by_day=comments_by_day, followers_by_day=followers_by_day,
                           max_daily=max_daily, top_posts=top_posts,
                           total_followers=total_followers, total_following=total_following,
                           avg_engagement=avg_engagement, day_of_week_stats=day_of_week_stats,
                           most_active_day=most_active_day)
