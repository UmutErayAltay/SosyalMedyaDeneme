"""Feed + post yaşam döngüsü: paylaşma, düzenleme, silme, taslak, sabitleme."""
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import time as _time
from flask import render_template, request, redirect, url_for, session, abort, flash, make_response, jsonify
from . import bp
from ._common import (_my_id, _attach_post_metrics, attach_repost_of, fetch_sidebar_context,
                      fetch_stats_and_bio, PAGE_SIZE, _can_view_post)
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..storage_helper import upload_images, upload_video
from ..hashtags import sync_post_hashtags, _trending_hashtags, notify_hashtag_followers, extract_hashtags
from ..stories import active_stories_bar
from ..mentions import notify_mentions, get_valid_usernames, extract_mentions
from ..visibility import visible_or_filter
from ..blocks import blocked_user_ids, is_blocked_either_way
from ..mutes import muted_user_ids
from ..polls import create_poll, attach_polls
from ..memories import get_memories
from ..post_views import record_view, get_view_count
from ..cache import invalidate
from ..notifications import notify

# Planlanmış post yayıncısı: 60sn throttle (çok sık çalışmaz, fakat zamanı gelenleri yapar)
_last_sched_publish = 0


@bp.route("/")
@login_required
@retry_on_connection_error
def feed():
    """Ana akış: postlar (yeni → eski) yazar + etkileşim sayılarıyla, sayfalı."""
    sb = get_sb()
    me = _my_id()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    # --- Planlanmış post yayıncısı (60sn throttle'lı)
    global _last_sched_publish
    now = _time.time()
    if now - _last_sched_publish >= 60:
        _last_sched_publish = now
        try:
            due = sb.table("posts").select("id, user_id, content").eq("is_draft", True)\
                  .not_.is_("scheduled_at", "null").lte("scheduled_at", datetime.now(timezone.utc).isoformat()).execute().data
            for p in due:
                sb.table("posts").update({"is_draft": False, "scheduled_at": None,
                                          "created_at": datetime.now(timezone.utc).isoformat()}).eq("id", p["id"]).execute()
                if p.get("content"):
                    sync_post_hashtags(sb, p["id"], p["content"])
                    notify_mentions(sb, actor_id=p["user_id"], content=p["content"], post_id=p["id"])
                    notify_hashtag_followers(sb, actor_id=p["user_id"], post_id=p["id"], tags=extract_hashtags(p["content"]))
        except Exception:
            pass

    # --- FAZ A: hem tam sayfa hem sonsuz kaydırma (AJAX partial) isteği için
    # ORTAK/ZORUNLU minimum iş. infiniteScroll.js EN SIK tetiklenen istek —
    # kenar çubuğu/hikaye/öneri/profil-kartı sorgularının hepsi FAZ B'ye
    # (partial erken dönüşünden SONRA) bırakıldı. Bir önceki optimizasyon
    # turunda bunların hepsi yanlışlıkla FAZ A'ya (tek büyük executor'a)
    # karışmıştı — bu, her scroll sayfasının ~15 gereksiz sorgu yapmasına
    # yol açan bir regresyondu, burada düzeltildi.
    # Kritik yol tek RPC'ye indirildi (Sprint 53): engel + görünürlük + post
    # + sayaçlar + anket tek round-trip. RPC henüz uygulanmamışsa (migration
    # bekliyor olabilir) None döner ve eski çok-sorgulu yola düşülür.
    def _fetch_posts_rpc():
        try:
            return sb.rpc("feed_page_posts", {
                "p_me": me, "p_offset": offset, "p_limit": PAGE_SIZE + 1,
            }).execute().data or []
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        posts_fut = executor.submit(_fetch_posts_rpc)
        usernames_fut = executor.submit(get_valid_usernames, sb)
        posts = posts_fut.result()
        valid_usernames = usernames_fut.result()

    if posts is None:
        # Fallback: eski çok-sorgulu yol (davranış birebir aynı)
        select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url), "
                       "likes(count), comments(count)")
        blocked_ids_fb = blocked_user_ids(sb, me)
        muted_ids_fb = muted_user_ids(sb, me)
        try:
            # Görünürlük + engelleme + taslak + arşiv filtreleri SQL seviyesinde uygulanır
            # (sayfalama sonrası Python'da süzmek PAGE_SIZE'ı tutarsız hale getirirdi).
            query = sb.table("posts").select(select_cols).or_(visible_or_filter(sb, me)).eq("is_draft", False).eq("is_archived", False)
            if blocked_ids_fb:
                query = query.not_.in_("user_id", list(blocked_ids_fb))
            if muted_ids_fb:
                query = query.not_.in_("user_id", list(muted_ids_fb))
            posts = query.order("created_at", desc=True).range(offset, offset + PAGE_SIZE).execute().data
        except Exception:
            # sql/migration_post_visibility.sql henüz uygulanmamışsa 'visibility'
            # kolonu yok — filtresiz eski davranışa düş (feed asla kırılmasın)
            posts = sb.table("posts").select(select_cols).order(
                "created_at", desc=True
            ).range(offset, offset + PAGE_SIZE).execute().data

        # Gizli profil kontrolü (Python fallback): is_private=true ve viewer accepted değilse gösterme
        visible_author_ids = followed_and_self_ids(sb, me)
        if posts:
            # Yazar is_private durumunu toplu çek
            author_ids = {p.get("user_id") for p in posts if p.get("user_id")}
            is_private_map = {}
            if author_ids:
                try:
                    profiles = sb.table("profiles").select("id, is_private").in_("id", list(author_ids)).execute().data
                    is_private_map = {p["id"]: p.get("is_private", False) for p in profiles}
                except Exception:
                    pass
            # is_private filtresi: gizli profil ve viewer accepted değilse ele
            posts = [p for p in posts if not (is_private_map.get(p.get("user_id"), False) and p.get("user_id") != me and p.get("user_id") not in visible_author_ids)]

        has_next = len(posts) > PAGE_SIZE
        posts = posts[:PAGE_SIZE]
        with ThreadPoolExecutor(max_workers=2) as executor:
            metrics_fut = executor.submit(_attach_post_metrics, sb, posts, me)
            polls_fut = executor.submit(attach_polls, sb, posts, me)
            metrics_fut.result()
            polls_fut.result()
    else:
        has_next = len(posts) > PAGE_SIZE
        posts = posts[:PAGE_SIZE]
        # RPC yolu: sayaçlar/anket RPC'den hazır, sadece repost orijinali eklenir
        attach_repost_of(sb, posts)

    if request.headers.get("X-Requested-With") == "fetch":
        html = render_template("_feed_posts.html", posts=posts, me=session.get("user"),
                               valid_usernames=valid_usernames)
        resp = make_response(html)
        resp.headers["X-Has-Next"] = "1" if has_next else "0"
        return resp

    # --- FAZ B: SADECE tam sayfa render'ında gerekli — hikaye çubuğu, gündem,
    # öneriler, sol profil kartı istatistikleri. Hepsi birbirinden (Faz A'nın
    # sonucu olan blocked_ids dışında) bağımsız, tek pool'da paralel.
    # post/takipçi/takip sayısı + bio: fetch_stats_and_bio() (TEK RPC, bkz.
    # _common.py) — önceden burada 4 ayrı sorgu vardı, discover()/post_detail()
    # ile aynı helper'a birleştirildi (Sprint 59).

    def _fetch_recent_media():
        """Sol profil kartı için kendi son medyaların mini önizlemesi (3 görsel)."""
        media = []
        try:
            rows = sb.table("posts").select("id, image_url, image_urls").eq(
                "user_id", me
            ).eq("is_draft", False).eq("is_archived", False).order("created_at", desc=True).limit(6).execute().data
            for p in rows:
                img = (p.get("image_urls") or [None])[0] or p.get("image_url")
                if img:
                    media.append({"post_id": p["id"], "image_url": img})
                if len(media) == 3:
                    break
        except Exception:
            pass
        return media

    def _fetch_close_friends():
        """Sağ kenar çubuğu için yakın arkadaşlar preview'ı (6 kişi)."""
        try:
            cf_rows = sb.table("close_friends").select(
                "profiles!close_friends_friend_id_fkey(id, username, avatar_url)"
            ).eq("owner_id", me).order("created_at", desc=True).limit(6).execute().data
            return [r["profiles"] for r in cf_rows if r.get("profiles")]
        except Exception:
            return []

    def _fetch_following_ids():
        try:
            return {f["following_id"] for f in sb.table("follows").select("following_id")
                    .eq("follower_id", me).execute().data}
        except Exception:
            return set()

    def _fetch_recent_activity():
        """Sağ sidebar için viewer'ın son 5 bildirimi."""
        try:
            return sb.table("notifications").select(
                "type, post_id, created_at, profiles!notifications_actor_id_fkey(username, avatar_url)"
            ).eq("recipient_id", me).order("created_at", desc=True).limit(5).execute().data
        except Exception:
            return []

    def _fetch_my_week_stats():
        """Bu hafta post ve like sayıları."""
        week_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        def _fetch_week_posts():
            try:
                return sb.table("posts").select(
                    "id", count="exact", head=True
                ).eq("user_id", me).eq("is_draft", False).gte("created_at", week_cutoff).execute().count or 0
            except Exception:
                return 0

        def _fetch_week_likes():
            try:
                # Benim postlarıma bu hafta yapılan likes
                return sb.table("likes").select(
                    "post_id, posts!inner(user_id)", count="exact", head=True
                ).eq("posts.user_id", me).gte("created_at", week_cutoff).execute().count or 0
            except Exception:
                # Fallback: inner-join sözdizimi çalışmazsa, '0' döner
                return 0

        with ThreadPoolExecutor(max_workers=2) as inner_executor:
            posts_fut = inner_executor.submit(_fetch_week_posts)
            likes_fut = inner_executor.submit(_fetch_week_likes)
            posts_count = posts_fut.result()
            likes_count = likes_fut.result()

        return {"posts": posts_count, "likes": likes_count}

    def _fetch_my_is_private():
        """Viewer'ın kendi is_private durumu — post/hikaye paylaşma modallarında
        görünürlük varsayılanını belirlemek için (gizli hesap -> varsayılan
        'Sadece takipçilerim', açık hesap -> varsayılan 'Herkese açık')."""
        try:
            prof = sb.table("profiles").select("is_private").eq("id", me).execute().data
            return bool(prof[0].get("is_private")) if prof else False
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        blocked_fut = executor.submit(blocked_user_ids, sb, me)
        memories_fut = executor.submit(get_memories, sb, me) if page == 1 else None
        trending_fut = executor.submit(_trending_hashtags, sb, hours=24, limit=10)
        stats_bio_fut = executor.submit(fetch_stats_and_bio, sb, me)
        recent_media_fut = executor.submit(_fetch_recent_media)
        close_friends_fut = executor.submit(_fetch_close_friends)
        following_ids_fut = executor.submit(_fetch_following_ids)
        my_is_private_fut = executor.submit(_fetch_my_is_private)
        recent_activity_fut = executor.submit(_fetch_recent_activity)
        my_week_stats_fut = executor.submit(_fetch_my_week_stats)

        # blocked_ids'i bekle, ardından stories submit et
        blocked_ids = blocked_fut.result()
        stories_fut = executor.submit(active_stories_bar, sb, me, blocked_ids)
        following_ids = following_ids_fut.result()

        memories = memories_fut.result() if memories_fut else []
        trending_tags = trending_fut.result()
        my_stats, my_bio = stats_bio_fut.result()
        my_recent_media = recent_media_fut.result()
        close_friends_preview = close_friends_fut.result()
        stories_bar = stories_fut.result()
        recent_activity = recent_activity_fut.result()
        my_week_stats = my_week_stats_fut.result()
        my_is_private = my_is_private_fut.result()

        # following_ids/blocked_ids bekledikten sonra: "kimi takip etmeli" önerisi
        def _fetch_suggested_users():
            exclude_ids = following_ids | blocked_ids | {me}
            query = sb.table("profiles").select("id, username, avatar_url, full_name").eq("is_banned", False)
            if exclude_ids:
                query = query.not_.in_("id", list(exclude_ids))
            try:
                return query.order("created_at", desc=True).limit(5).execute().data
            except Exception:
                return []

        suggested_users = executor.submit(_fetch_suggested_users).result()

    # explore_suggestions: akış zayıfsa sayfa 1'de keşfet önerileri
    explore_suggestions = []
    if page == 1 and len(posts) < 5:
        try:
            explore_suggestions = sb.rpc("discover_page_posts", {"p_me": me, "p_limit": 5}).execute().data or []
            # Sayaçlar/anket RPC'den HAZIR gelir — _attach_post_metrics ÇAĞRILMAZ
            # (sayıları sıfırlardı, bkz. attach_repost_of docstring'i)
            attach_repost_of(sb, explore_suggestions)
        except Exception:
            explore_suggestions = []

    return render_template("feed.html", posts=posts, me=session.get("user"),
                           my_is_private=my_is_private,
                           page=page, has_next=has_next, trending_tags=trending_tags,
                           suggested_users=suggested_users, stories_bar=stories_bar,
                           memories=memories, valid_usernames=valid_usernames,
                           my_stats=my_stats,
                           my_recent_media=my_recent_media, close_friends_preview=close_friends_preview,
                           my_bio=my_bio, recent_activity=recent_activity, my_week_stats=my_week_stats,
                           explore_suggestions=explore_suggestions)


@bp.route("/post/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_post():
    content = request.form.get("content", "").strip()
    image_files = request.files.getlist("images")
    video_file = request.files.get("video")
    has_video = bool(video_file and video_file.filename)

    # Görsel, video ve anket artık AYNI postta birlikte eklenebilir (kullanıcı
    # isteğiyle mutual-exclusive kısıtlama kaldırıldı, ör. görsel + anket).
    poll_options_raw = [request.form.get(f"poll_option_{i}", "").strip() for i in range(1, 5)]
    poll_options = [o for o in poll_options_raw if o]
    has_poll = len(poll_options) >= 2

    valid_files = [f for f in image_files if f and f.filename]

    if has_poll and not content:
        flash("Anket için bir soru yazmalısın.", "error")
        return redirect(url_for("routes.feed"))

    if not content and not valid_files and not has_video and not has_poll:
        flash("Boş post paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    image_urls = []
    video_url = None
    gif_url = request.form.get("gif_url", "").strip()
    if valid_files:
        # Çoklu görsel yükle (maksimum 4)
        image_urls = upload_images(valid_files, folder="posts", max_count=4)
        if not image_urls:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))
    elif gif_url and not video_file:
        # GIF URL'si: Klipy CDN'inden olmalı (SSRF/keyfi URL engeli)
        if gif_url.startswith("https://static.klipy.com/"):
            image_urls = [gif_url]
    if has_video:
        video_url = upload_video(video_file, folder="posts")
        if not video_url:
            flash("Video yüklenemedi (geçersiz format veya 25MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))

    # Geriye dönük uyumluluk: image_url (ilk görsel) + image_urls (array)
    insert_data = {
        "user_id": _my_id(),
        "content": content,
        "image_urls": image_urls,
    }
    if image_urls:
        insert_data["image_url"] = image_urls[0]

    visibility = request.form.get("visibility", "public")
    if visibility not in ("public", "followers", "close_friends"):
        visibility = "public"

    # Taslak veya planlanmış post olarak kaydet
    action = request.form.get("action", "")
    is_draft = action == "draft"
    is_scheduled = action == "schedule"
    scheduled_at = None
    if is_scheduled:
        scheduled_at_str = request.form.get("scheduled_at", "").strip()
        if scheduled_at_str:
            is_draft = True  # Planlanmış post de taslak gibi gizlidir
            scheduled_at = scheduled_at_str

    # Konum alanları (try/except ile — kolon yoksa atlanır)
    location_name = request.form.get("location_name", "").strip()[:80]
    location_lat_str = request.form.get("location_lat", "").strip()
    location_lng_str = request.form.get("location_lng", "").strip()
    location_lat = None
    location_lng = None
    if location_lat_str and location_lng_str:
        try:
            location_lat = float(location_lat_str)
            location_lng = float(location_lng_str)
            if not (-90 <= location_lat <= 90 and -180 <= location_lng <= 180):
                location_lat = location_lng = None
        except ValueError:
            pass

    sb = get_sb()
    try:
        # sql/migration_post_visibility.sql, migration_video_posts.sql,
        # migration_drafts.sql, migration_post_scheduling.sql,
        # migration_post_location.sql henüz uygulanmamışsa ilgili kolon(lar) yok —
        # post paylaşımı bundan etkilenmesin diye kolonsuz (eski) haliyle dene
        full_data = {**insert_data, "visibility": visibility, "is_draft": is_draft}
        if video_url:
            full_data["video_url"] = video_url
        if scheduled_at:
            full_data["scheduled_at"] = scheduled_at
        if location_name or (location_lat is not None and location_lng is not None):
            if location_name:
                full_data["location_name"] = location_name
            if location_lat is not None:
                full_data["location_lat"] = location_lat
            if location_lng is not None:
                full_data["location_lng"] = location_lng
        inserted = sb.table("posts").insert(full_data).execute()
    except Exception:
        inserted = sb.table("posts").insert(insert_data).execute()
    post_id = inserted.data[0]["id"] if inserted.data else None
    # Hashtag/mention işlemleri SADECE yayınlanmış (ve planlı değil) postta yapılır
    if post_id and content and not is_draft:
        sync_post_hashtags(sb, post_id, content)
        invalidate("trending:")  # Gündem cache'i güncelle
        notify_mentions(sb, actor_id=_my_id(), content=content, post_id=post_id)
        notify_hashtag_followers(sb, actor_id=_my_id(), post_id=post_id, tags=extract_hashtags(content))
    if post_id and has_poll:
        create_poll(sb, poll_options, post_id=post_id)

    if is_draft:
        if is_scheduled:
            flash("Post planlandı.", "success")
        else:
            flash("Taslak kaydedildi.", "success")
        return redirect(url_for("routes.drafts_list"))

    flash("Post paylaşıldı.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/post/<post_id>/edit", methods=["GET", "POST"])
@login_required
@retry_on_connection_error
def edit_post(post_id):
    """Post içeriğini düzenler — SADECE metin (görsel/video değiştirilemez,
    kapsam dışı bırakıldı). Düzenlenince edited_at damgalanır, hashtag'ler
    yeniden senkronlanır, SADECE YENİ eklenen @mention'lara bildirim gider
    (zaten var olan mention'lar tekrar bildirim üretmesin diye eski/yeni
    içerik farkı alınır)."""
    sb = get_sb()
    me = _my_id()

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        abort(403)
    post = post[0]

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        has_media = post.get("image_urls") or post.get("image_url") or post.get("video_url")
        if not content and not has_media:
            flash("Boş post olamaz.", "error")
            return redirect(url_for("routes.edit_post", post_id=post_id))

        visibility = request.form.get("visibility", post.get("visibility") or "public")
        if visibility not in ("public", "followers", "close_friends"):
            visibility = "public"

        old_mentions = set(extract_mentions(post.get("content") or ""))
        new_mentions = set(extract_mentions(content))
        added_mentions = new_mentions - old_mentions

        update_data = {"content": content, "edited_at": datetime.now(timezone.utc).isoformat()}
        try:
            # 'visibility' kolonu yoksa (migration henüz yok) kolonsuz güncelle
            sb.table("posts").update({**update_data, "visibility": visibility}).eq("id", post_id).execute()
        except Exception:
            sb.table("posts").update(update_data).eq("id", post_id).execute()

        # Taslak henüz yayınlanmadıysa hashtag senkronu/mention bildirimi
        # ERTELENİR (bkz. publish_draft()) — içerik herkese açık değilken
        # bunları tetiklemek yanlış olurdu.
        if not post.get("is_draft"):
            sync_post_hashtags(sb, post_id, content)
            invalidate("trending:")  # Gündem cache'i güncelle
            if added_mentions:
                # Sentetik bir "@kullanıcı @kullanıcı2" metni: notify_mentions zaten
                # extract_mentions ile ayrıştırıyor, sadece YENİ eklenenleri bildirir
                notify_mentions(sb, actor_id=me, content=" ".join(f"@{u}" for u in added_mentions), post_id=post_id)

        flash("Post güncellendi.", "success")
        if post.get("is_draft"):
            return redirect(url_for("routes.drafts_list"))
        return redirect(url_for("routes.post_detail", post_id=post_id))

    return render_template("post_edit.html", post=post, me=session.get("user"))


@bp.route("/post/<post_id>")
@login_required
@retry_on_connection_error
def post_detail(post_id):
    sb = get_sb()
    res = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), "
        "likes(count), comments(count)"
    ).eq("id", post_id).execute()
    if not res.data:
        abort(404)
    post = res.data[0]

    me = _my_id()

    # Taslak: sadece sahibi görebilir (bkz. drafts_list()/publish_draft())
    if post.get("is_draft") and post["user_id"] != me:
        abort(404)

    # Kapsamlı görüntüleme izin kontrolleri: engelleme, arşiv, gizli profil, visibility
    if not _can_view_post(sb, post, me):
        abort(404)

    # Görüntüleme sayısını kaydet (tüm access kontrolleri geçtikten sonra)
    record_view(sb, post_id, me, post["user_id"])

    _attach_post_metrics(sb, [post], me)
    attach_polls(sb, [post], me)

    # Yorumlar + beğeni sayıları tek sorguda
    comments = sb.table("comments").select(
        "*, profiles!comments_user_id_fkey(username, avatar_url), comment_likes(count)"
    ).eq("post_id", post_id).order("created_at", desc=False).execute().data

    # Benim beğendiğim yorumlar tek IN sorgusuyla (N+1 önlenir)
    comment_ids = [c["id"] for c in comments]
    my_comment_likes = set()
    if comment_ids:
        my_comment_likes = {
            l["comment_id"] for l in sb.table("comment_likes").select("comment_id")
            .eq("user_id", me).in_("comment_id", comment_ids).execute().data
        }
    for c in comments:
        c["like_count"] = c["comment_likes"][0]["count"] if c.get("comment_likes") else 0
        c["liked_by_me"] = c["id"] in my_comment_likes

    # Sticker'ları bağla — tek IN sorgusu (N+1 yok), tablo yoksa sessizce None
    try:
        sticker_ids = [c["sticker_id"] for c in comments if c.get("sticker_id")]
        stickers_by_id = {}
        if sticker_ids:
            for s in sb.table("stickers").select("id, image_url").in_("id", sticker_ids).execute().data:
                stickers_by_id[s["id"]] = {"id": s["id"], "image_url": s["image_url"]}
        for c in comments:
            c["sticker"] = stickers_by_id.get(c.get("sticker_id"))
    except Exception:
        for c in comments:
            c["sticker"] = None

    # Emoji tepkileri bağla — tek IN sorgusu, gruplayıp count+mine hesapla
    # (messaging/views.py'deki mesaj reaksiyon enrichment'ıyla aynı desen).
    # comment_reactions tablosu yoksa (migration henüz uygulanmadıysa) sessizce boş kalır.
    try:
        reactions_by_comment = {}
        if comment_ids:
            rows = sb.table("comment_reactions").select("comment_id, user_id, reaction") \
                .in_("comment_id", comment_ids).execute().data
            for r in rows:
                bucket = reactions_by_comment.setdefault(r["comment_id"], {})
                bucket.setdefault(r["reaction"], []).append(r["user_id"])
        for c in comments:
            grouped = reactions_by_comment.get(c["id"], {})
            c["reactions"] = [
                {"reaction": emoji, "count": len(users), "mine": me in users}
                for emoji, users in grouped.items()
            ]
    except Exception:
        for c in comments:
            c["reactions"] = []

    # Hiyerarşik yapı: ana yorumlar + cevaplar
    top_comments = [c for c in comments if not c.get("parent_comment_id")]
    for tc in top_comments:
        tc["replies"] = [c for c in comments if c.get("parent_comment_id") == tc["id"]]
    comments = top_comments

    # Görüntüleme sayısı SADECE yazarsa hesaplanır (gereksiz sorgu + bilgi sızıntısı önlenir)
    view_count = get_view_count(sb, post_id) if post["user_id"] == me else None

    # --- Sidebar verileri: keşfet ile ortak helper (paralel; sıra ve içerik
    # feed ile aynı) — aktivite kartı bu sayfada kullanılmıyor, sorgusu atlanır
    sidebar = fetch_sidebar_context(sb, me, include_activity=False)

    return render_template("post_detail.html", post=post, comments=comments,
                           me=session.get("user"), valid_usernames=get_valid_usernames(sb),
                           view_count=view_count, **sidebar)


@bp.route("/post/<post_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_post(post_id):
    # Uygulama katmanı güvenliği: sadece kendi postunu sil
    get_sb().table("posts").delete().eq("id", post_id).eq(
        "user_id", _my_id()
    ).execute()
    flash("Post silindi.", "success")
    return redirect(url_for("routes.feed"))


@bp.route("/post/<post_id>/repost", methods=["POST"])
@login_required
@retry_on_connection_error
def create_repost(post_id):
    """Bir postu yeniden paylaşır (repost/alıntı). İçeriksiz repost=boost,
    içerikli repost=alıntılı yorum. Zincir düzleştirme: repost'un repost'u
    orijinale işaret eder (Twitter davranışı)."""
    sb = get_sb()
    me = _my_id()
    content = request.form.get("content", "").strip()

    # 1) Orijinal postu çek
    original = sb.table("posts").select(
        "id, user_id, visibility, is_draft, is_archived, repost_of_id, content"
    ).eq("id", post_id).execute().data
    if not original:
        abort(404)
    original = original[0]

    # 2) Repost kısıtlarını kontrol et
    if original.get("visibility") != "public":
        return jsonify(error="not_public"), 403

    if original.get("is_draft") or original.get("is_archived"):
        return jsonify(error="not_available"), 400

    # Yazarın profili gizli mi kontrol et
    try:
        author_profile = sb.table("profiles").select("is_private").eq(
            "id", original.get("user_id")
        ).execute().data
        if author_profile and author_profile[0].get("is_private"):
            return jsonify(error="private_account"), 403
    except Exception:
        pass

    # İki yönlü engel kontrolü
    if is_blocked_either_way(sb, me, original.get("user_id")):
        return jsonify(error="blocked"), 403

    # 3) Zincir düzleştirme: hedef post kendisi içeriksiz repost ise
    # orijinale işaret et — bildirim de GERÇEK orijinalin yazarına gider
    # (aradaki repost'çuya değil)
    repost_target_id = original.get("id")
    notify_author_id = original.get("user_id")
    if original.get("repost_of_id") and not original.get("content"):
        # İçeriksiz repost'un repost'u — orijinaline git
        repost_target_id = original.get("repost_of_id")
        try:
            true_original = sb.table("posts").select("user_id").eq(
                "id", repost_target_id).execute().data
            if true_original:
                notify_author_id = true_original[0]["user_id"]
        except Exception:
            pass

    # 4) Aynı kullanıcının aynı orijinali içeriksiz olarak 2 kez
    # repost etmesini engelle (içerikli alıntılar tekrarlanabilir)
    if not content:
        existing = sb.table("posts").select("id").eq(
            "user_id", me
        ).eq("repost_of_id", repost_target_id).eq("content", "").execute().data
        if existing:
            return jsonify(error="already_reposted"), 409

    # 5) Repost'u oluştur
    try:
        insert_data = {
            "user_id": me,
            "content": content,
            "repost_of_id": repost_target_id,
            "visibility": "public",
        }
        inserted = sb.table("posts").insert(insert_data).execute()
    except Exception:
        return jsonify(error="unavailable"), 503

    if not inserted.data:
        return jsonify(error="unavailable"), 503

    new_post_id = inserted.data[0]["id"]

    # 6) Bildirim gönder (orijinal yazarım değilsem)
    if notify_author_id != me:
        notify(
            sb,
            recipient_id=notify_author_id,
            actor_id=me,
            type_="repost",
            post_id=repost_target_id,
        )

    return jsonify(ok=True, post_id=new_post_id)


@bp.route("/post/<post_id>/archive", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_archive(post_id):
    """Postu arşivle/arşivden çıkar (toggle). Sadece sahibi yapabilir."""
    sb = get_sb()
    me = _my_id()

    # Sahiplik kontrolü: sadece kendi postunu arşivle
    post = sb.table("posts").select("id, is_archived").eq("id", post_id).eq(
        "user_id", me
    ).execute()
    if not post.data:
        abort(404)

    post = post.data[0]
    is_currently_archived = post.get("is_archived", False)

    # Toggle: arşivlenmiş ise çıkar, değilse arşivle
    update_data = {"is_archived": not is_currently_archived}
    if not is_currently_archived:
        # Arşivlerken archived_at damgasını ayarla
        update_data["archived_at"] = datetime.now(timezone.utc).isoformat()
    else:
        # Arşivden çıkarırken archived_at temizle
        update_data["archived_at"] = None

    sb.table("posts").update(update_data).eq("id", post_id).execute()

    if not is_currently_archived:
        flash("Post arşivlendi.", "success")
    else:
        flash("Post arşivden çıkarıldı.", "success")

    return redirect(request.referrer or url_for("routes.feed"))


@bp.route("/taslaklar")
@login_required
@retry_on_connection_error
def drafts_list():
    """Kendi taslaklarımın listesi — SADECE burada görünürler (feed/profil/
    arama/hashtag'te asla görünmezler)."""
    sb = get_sb()
    me = _my_id()
    drafts = []
    try:
        drafts = sb.table("posts").select("*").eq("user_id", me).eq(
            "is_draft", True
        ).order("created_at", desc=True).execute().data
    except Exception:
        pass  # migration_drafts.sql henüz uygulanmamışsa boş liste gösterilir
    return render_template("drafts.html", drafts=drafts, me=session.get("user"))


@bp.route("/post/<post_id>/publish", methods=["POST"])
@login_required
@retry_on_connection_error
def publish_draft(post_id):
    """Bir taslağı yayınlar — artık is_draft=false olur, TAM O ANDA hashtag
    senkronu + mention bildirimi tetiklenir (taslakken içerik herkese açık
    olmadığı için bunlar bilerek ERTELENMİŞTİ). Planlanmış post ise scheduled_at
    silinir (yayın zamanı geçmiş anlamına gelir)."""
    sb = get_sb()
    me = _my_id()

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        abort(403)
    post = post[0]

    try:
        sb.table("posts").update({"is_draft": False, "scheduled_at": None}).eq("id", post_id).execute()
    except Exception:
        sb.table("posts").update({"is_draft": False}).eq("id", post_id).execute()
    if post.get("content"):
        sync_post_hashtags(sb, post_id, post["content"])
        invalidate("trending:")  # Gündem cache'i güncelle
        notify_mentions(sb, actor_id=me, content=post["content"], post_id=post_id)
        notify_hashtag_followers(sb, actor_id=me, post_id=post_id, tags=extract_hashtags(post["content"]))

    flash("Post yayınlandı.", "success")
    return redirect(url_for("routes.post_detail", post_id=post_id))


@bp.route("/post/<post_id>/pin", methods=["POST"])
@login_required
@retry_on_connection_error
def toggle_pin(post_id):
    """Profilde en üstte gösterilecek 1 post seç/kaldır. profiles.pinned_post_id
    TEK bir kolon olduğu için "en fazla 1 sabit post" kısıtı otomatik sağlanır —
    yeni bir post sabitlemek eskisinin yerini alır."""
    sb = get_sb()
    me = _my_id()

    post = sb.table("posts").select("user_id").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        abort(403)

    try:
        prof = sb.table("profiles").select("pinned_post_id, username").eq("id", me).execute().data
        current = prof[0].get("pinned_post_id") if prof else None
        if current == post_id:
            sb.table("profiles").update({"pinned_post_id": None}).eq("id", me).execute()
            flash("Sabitleme kaldırıldı.", "success")
        else:
            sb.table("profiles").update({"pinned_post_id": post_id}).eq("id", me).execute()
            flash("Post profilinin en üstüne sabitlendi.", "success")
        username = prof[0]["username"] if prof else None
    except Exception:
        flash("Sabitleme özelliği henüz aktif değil (migration uygulanmamış).", "error")
        username = None

    if username:
        return redirect(url_for("routes.profile", username=username))
    return redirect(request.referrer or url_for("routes.feed"))
