"""Profil sayfası, takipçi/takip listeleri, profil düzenleme, istatistikler."""
from datetime import datetime, timedelta, timezone
from flask import render_template, request, redirect, url_for, session, abort, flash
from . import bp
from ._common import _my_id, _profile, _attach_post_metrics
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..storage_helper import upload_image
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids, filter_visible
from ..blocks import blocked_user_ids, filter_not_blocked, has_blocked
from ..polls import attach_polls


@bp.route("/u/<username>")
@login_required
@retry_on_connection_error
def profile(username):
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

    # 'Sadece takipçiler' postlarını görebilecek yazar kümesi (ben + takip
    # ettiklerim) — profildeki HER sekmede (Gönderiler/Medya/Beğenilenler/
    # Kaydedilenler) aynı süzme mantığı geçerli, çünkü liked/bookmarked
    # postlar BAŞKA yazarlara ait olabilir.
    visible_author_ids = followed_and_self_ids(sb, me)
    blocked_ids = blocked_user_ids(sb, me)

    # Postlar + etkileşim sayıları tek sorguda
    posts = sb.table("posts").select(
        "*, likes(count), comments(count)"
    ).eq("user_id", prof["id"]).order("created_at", desc=True).execute().data
    posts = [p for p in posts if not p.get("is_draft")]  # taslaklar SADECE /taslaklar'da görünür
    posts = filter_visible(posts, visible_author_ids)
    posts = filter_not_blocked(posts, blocked_ids)
    # Sabitlenmiş post en üste taşınır (görünür değilse zaten listede yok,
    # bu durumda pinned_post_id eşleşmesi olmaz — sessizce yok sayılır)
    pinned_id = prof.get("pinned_post_id")
    if pinned_id:
        posts.sort(key=lambda p: 0 if p["id"] == pinned_id else 1)
    _attach_post_metrics(sb, posts, me)
    attach_polls(sb, posts, me)

    # Medya sekmesi: görsel içeren postlar (ek sorgu yok, mevcut listeden süzülür)
    media_posts = [p for p in posts if p.get("image_urls") or p.get("image_url")]

    # Beğenilenler sekmesi: bu profilin beğendiği postlar (beğeni sırasına göre yeni→eski)
    liked_rows = sb.table("likes").select("post_id").eq(
        "user_id", prof["id"]
    ).order("created_at", desc=True).execute().data
    liked_ids = [l["post_id"] for l in liked_rows]
    liked_posts = []
    if liked_ids:
        liked_posts = sb.table("posts").select(
            "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
        ).in_("id", liked_ids).execute().data
        liked_posts = filter_visible(liked_posts, visible_author_ids)
        liked_posts = filter_not_blocked(liked_posts, blocked_ids)
        _attach_post_metrics(sb, liked_posts, me)
        attach_polls(sb, liked_posts, me)
        # .in_() sırayı garanti etmez; beğeni sırasına (liked_ids) göre yeniden diz
        order = {pid: i for i, pid in enumerate(liked_ids)}
        liked_posts.sort(key=lambda p: order.get(p["id"], 0))

    # Kaydedilenler sekmesi: sadece kendi profilini görüntülerken (kişisel liste,
    # bkz. sql/migration_bookmarks.sql RLS — başkasının kaydettikleri görünmez)
    bookmarked_posts = []
    bookmark_collections = []
    if is_self:
        try:
            bookmark_collections = sb.table("bookmark_collections").select("id, name").eq(
                "user_id", me
            ).order("created_at").execute().data
        except Exception:
            pass  # sql/migration_bookmark_collections.sql henüz uygulanmamış olabilir
        try:
            bm_rows = sb.table("bookmarks").select("post_id, collection_id").eq(
                "user_id", me
            ).order("created_at", desc=True).execute().data
            bm_ids = [b["post_id"] for b in bm_rows]
            collection_by_post = {b["post_id"]: b.get("collection_id") for b in bm_rows}
            if bm_ids:
                bookmarked_posts = sb.table("posts").select(
                    "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
                ).in_("id", bm_ids).execute().data
                bookmarked_posts = filter_visible(bookmarked_posts, visible_author_ids)
                bookmarked_posts = filter_not_blocked(bookmarked_posts, blocked_ids)
                _attach_post_metrics(sb, bookmarked_posts, me)
                attach_polls(sb, bookmarked_posts, me)
                bm_order = {pid: i for i, pid in enumerate(bm_ids)}
                bookmarked_posts.sort(key=lambda p: bm_order.get(p["id"], 0))
                for p in bookmarked_posts:
                    p["bookmark_collection_id"] = collection_by_post.get(p["id"])
        except Exception:
            pass  # migration henüz uygulanmamışsa sekme boş görünür, sayfa kırılmaz

    # --- Profil istatistikleri (count='exact' ile satır çekmeden say) ---
    followers_count = sb.table("follows").select(
        "follower_id", count="exact", head=True
    ).eq("following_id", prof["id"]).execute().count or 0
    following_count = sb.table("follows").select(
        "following_id", count="exact", head=True
    ).eq("follower_id", prof["id"]).execute().count or 0
    # Toplam beğeni (kullanıcının tüm postlarına gelen)
    total_likes = sum(p["like_count"] for p in posts)

    is_following = False
    is_blocked_by_me = False
    if not is_self:
        f = sb.table("follows").select().eq("follower_id", me).eq(
            "following_id", prof["id"]
        ).execute()
        is_following = bool(f.data)
        is_blocked_by_me = prof["id"] in blocked_ids

    return render_template("profile.html", profile=prof, posts=posts,
                           media_posts=media_posts, liked_posts=liked_posts,
                           bookmarked_posts=bookmarked_posts,
                           bookmark_collections=bookmark_collections,
                           is_self=is_self, is_following=is_following,
                           is_blocked_by_me=is_blocked_by_me, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb),
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


@bp.route("/insights")
@login_required
@retry_on_connection_error
def insights():
    """Kendi postlarının beğeni/yorum trendini gösteren basit bir sayfa —
    yeni bir tablo gerekmez, mevcut likes/comments/posts üzerinden hesaplanır."""
    sb = get_sb()
    me = _my_id()

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
    days = 14
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
    max_daily = max([d["count"] for d in likes_by_day + comments_by_day] + [1])

    top_posts = sorted(posts, key=lambda p: p["engagement"], reverse=True)[:5]

    return render_template("insights.html", me=session.get("user"),
                           total_posts=total_posts, total_likes=total_likes,
                           total_comments=total_comments, likes_by_day=likes_by_day,
                           comments_by_day=comments_by_day, max_daily=max_daily,
                           top_posts=top_posts)
