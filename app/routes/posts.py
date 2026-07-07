"""Feed + post yaşam döngüsü: paylaşma, düzenleme, silme, taslak, sabitleme."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from flask import render_template, request, redirect, url_for, session, abort, flash, make_response
from . import bp
from ._common import _my_id, _attach_post_metrics, PAGE_SIZE
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..storage_helper import upload_images, upload_video
from ..hashtags import sync_post_hashtags, _trending_hashtags, notify_hashtag_followers, extract_hashtags
from ..stories import active_stories_bar
from ..mentions import notify_mentions, get_valid_usernames, extract_mentions
from ..visibility import visible_or_filter
from ..blocks import blocked_user_ids, is_blocked_either_way
from ..polls import create_poll, attach_polls
from ..memories import get_memories
from ..post_views import record_view, get_view_count


@bp.route("/")
@login_required
@retry_on_connection_error
def feed():
    """Ana akış: postlar (yeni → eski) yazar + etkileşim sayılarıyla, sayfalı."""
    sb = get_sb()
    me = _my_id()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

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
        try:
            # Görünürlük + engelleme + taslak filtreleri SQL seviyesinde uygulanır
            # (sayfalama sonrası Python'da süzmek PAGE_SIZE'ı tutarsız hale getirirdi).
            query = sb.table("posts").select(select_cols).or_(visible_or_filter(sb, me)).eq("is_draft", False)
            if blocked_ids_fb:
                query = query.not_.in_("user_id", list(blocked_ids_fb))
            posts = query.order("created_at", desc=True).range(offset, offset + PAGE_SIZE).execute().data
        except Exception:
            # sql/migration_post_visibility.sql henüz uygulanmamışsa 'visibility'
            # kolonu yok — filtresiz eski davranışa düş (feed asla kırılmasın)
            posts = sb.table("posts").select(select_cols).order(
                "created_at", desc=True
            ).range(offset, offset + PAGE_SIZE).execute().data
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

    if request.headers.get("X-Requested-With") == "fetch":
        html = render_template("_feed_posts.html", posts=posts, me=session.get("user"),
                               valid_usernames=valid_usernames)
        resp = make_response(html)
        resp.headers["X-Has-Next"] = "1" if has_next else "0"
        return resp

    # --- FAZ B: SADECE tam sayfa render'ında gerekli — hikaye çubuğu, gündem,
    # öneriler, sol profil kartı istatistikleri. Hepsi birbirinden (Faz A'nın
    # sonucu olan blocked_ids dışında) bağımsız, tek pool'da paralel.
    def _fetch_posts_count():
        try:
            return sb.table("posts").select(
                "id", count="exact", head=True
            ).eq("user_id", me).eq("is_draft", False).execute().count or 0
        except Exception:
            return 0

    def _fetch_followers_count():
        try:
            return sb.table("follows").select(
                "follower_id", count="exact", head=True
            ).eq("following_id", me).execute().count or 0
        except Exception:
            return 0

    def _fetch_following_count():
        try:
            return sb.table("follows").select(
                "following_id", count="exact", head=True
            ).eq("follower_id", me).execute().count or 0
        except Exception:
            return 0

    def _fetch_bio():
        try:
            my_bio_row = sb.table("profiles").select("bio").eq("id", me).execute().data
            return (my_bio_row[0].get("bio") if my_bio_row else None) or None
        except Exception:
            return None

    def _fetch_recent_media():
        """Sol profil kartı için kendi son medyaların mini önizlemesi (3 görsel)."""
        media = []
        try:
            rows = sb.table("posts").select("id, image_url, image_urls").eq(
                "user_id", me
            ).eq("is_draft", False).order("created_at", desc=True).limit(6).execute().data
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

    with ThreadPoolExecutor(max_workers=10) as executor:
        blocked_fut = executor.submit(blocked_user_ids, sb, me)
        memories_fut = executor.submit(get_memories, sb, me) if page == 1 else None
        trending_fut = executor.submit(_trending_hashtags, sb, hours=24, limit=10)
        posts_count_fut = executor.submit(_fetch_posts_count)
        followers_count_fut = executor.submit(_fetch_followers_count)
        following_count_fut = executor.submit(_fetch_following_count)
        bio_fut = executor.submit(_fetch_bio)
        recent_media_fut = executor.submit(_fetch_recent_media)
        close_friends_fut = executor.submit(_fetch_close_friends)
        following_ids_fut = executor.submit(_fetch_following_ids)

        # blocked_ids'i bekle, ardından stories submit et
        blocked_ids = blocked_fut.result()
        stories_fut = executor.submit(active_stories_bar, sb, me, blocked_ids)
        following_ids = following_ids_fut.result()

        memories = memories_fut.result() if memories_fut else []
        trending_tags = trending_fut.result()
        my_posts_count = posts_count_fut.result()
        my_followers_count = followers_count_fut.result()
        my_following_count = following_count_fut.result()
        my_bio = bio_fut.result()
        my_recent_media = recent_media_fut.result()
        close_friends_preview = close_friends_fut.result()
        stories_bar = stories_fut.result()

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

    return render_template("feed.html", posts=posts, me=session.get("user"),
                           page=page, has_next=has_next, trending_tags=trending_tags,
                           suggested_users=suggested_users, stories_bar=stories_bar,
                           memories=memories, valid_usernames=valid_usernames,
                           my_stats={"posts": my_posts_count, "followers": my_followers_count, "following": my_following_count},
                           my_recent_media=my_recent_media, close_friends_preview=close_friends_preview,
                           my_bio=my_bio)


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
    if valid_files:
        # Çoklu görsel yükle (maksimum 4)
        image_urls = upload_images(valid_files, folder="posts", max_count=4)
        if not image_urls:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))
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

    # Taslak olarak kaydet: post DB'de oluşur ama feed/profil/arama/hashtag'te
    # hiç görünmez, hashtag senkronu/mention bildirimi de YAYINLANMADAN
    # tetiklenmez (içerik henüz herkese açık değil) — bkz. publish_draft().
    is_draft = request.form.get("action") == "draft"

    sb = get_sb()
    try:
        # sql/migration_post_visibility.sql, migration_video_posts.sql veya
        # migration_drafts.sql henüz uygulanmamışsa ilgili kolon(lar) yok —
        # post paylaşımı bundan etkilenmesin diye kolonsuz (eski) haliyle dene
        full_data = {**insert_data, "visibility": visibility, "is_draft": is_draft}
        if video_url:
            full_data["video_url"] = video_url
        inserted = sb.table("posts").insert(full_data).execute()
    except Exception:
        inserted = sb.table("posts").insert(insert_data).execute()
    post_id = inserted.data[0]["id"] if inserted.data else None
    if post_id and content and not is_draft:
        sync_post_hashtags(sb, post_id, content)
        notify_mentions(sb, actor_id=_my_id(), content=content, post_id=post_id)
        notify_hashtag_followers(sb, actor_id=_my_id(), post_id=post_id, tags=extract_hashtags(content))
    if post_id and has_poll:
        create_poll(sb, post_id, poll_options)

    if is_draft:
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

    # Engelleme (iki yönlü): post sahibiyle aramda herhangi bir yönde engelleme
    # varsa post hiç yokmuş gibi davran.
    if post["user_id"] != me and is_blocked_either_way(sb, me, post["user_id"]):
        abort(404)

    # Sadece takipçilere özel post: yazar değilsen ve yazarı takip etmiyorsan
    # 404 (var olmadığı gibi davran — erişim reddi ayrı bir mesajla "bu post
    # var ama gizli" sinyali vermez, enumeration'ı önler).
    if post.get("visibility") == "followers" and post["user_id"] != me:
        following = sb.table("follows").select("follower_id").eq(
            "follower_id", me
        ).eq("following_id", post["user_id"]).execute().data
        if not following:
            abort(404)

    # Sadece yakın arkadaşlara özel post: yazar değilsen ve seni yakın arkadaş
    # listesine eklememişse 404.
    if post.get("visibility") == "close_friends" and post["user_id"] != me:
        is_close = sb.table("close_friends").select("owner_id").eq(
            "owner_id", post["user_id"]
        ).eq("friend_id", me).execute().data
        if not is_close:
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

    # Hiyerarşik yapı: ana yorumlar + cevaplar
    top_comments = [c for c in comments if not c.get("parent_comment_id")]
    for tc in top_comments:
        tc["replies"] = [c for c in comments if c.get("parent_comment_id") == tc["id"]]
    comments = top_comments

    # Görüntüleme sayısı SADECE yazarsa hesaplanır (gereksiz sorgu + bilgi sızıntısı önlenir)
    view_count = get_view_count(sb, post_id) if post["user_id"] == me else None

    return render_template("post_detail.html", post=post, comments=comments,
                           me=session.get("user"), valid_usernames=get_valid_usernames(sb),
                           view_count=view_count)


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
    olmadığı için bunlar bilerek ERTELENMİŞTİ)."""
    sb = get_sb()
    me = _my_id()

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        abort(403)
    post = post[0]

    sb.table("posts").update({"is_draft": False}).eq("id", post_id).execute()
    if post.get("content"):
        sync_post_hashtags(sb, post_id, post["content"])
        notify_mentions(sb, actor_id=me, content=post["content"], post_id=post_id)

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
