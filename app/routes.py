"""Ana rotalar: feed, post paylaşma, profil, post detayı."""
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image, upload_images, upload_video
from .hashtags import sync_post_hashtags
from .mentions import notify_mentions, get_valid_usernames, extract_mentions
from .visibility import followed_and_self_ids, filter_visible, visible_or_filter
from .blocks import blocked_user_ids, filter_not_blocked, is_blocked_either_way, has_blocked
from .polls import create_poll, attach_polls

bp = Blueprint("routes", __name__)


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


PAGE_SIZE = 20


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


@bp.route("/")
@login_required
@retry_on_connection_error
def feed():
    """Ana akış: postlar (yeni → eski) yazar + etkileşim sayılarıyla, sayfalı."""
    sb = get_sb()
    me = _my_id()
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * PAGE_SIZE

    # Post + yazar + beğeni/yorum sayıları tek sorguda.
    # Bir fazla satır çekilir: sonraki sayfa var mı bilgisi için.
    select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url), "
                   "likes(count), comments(count)")
    blocked_ids = blocked_user_ids(sb, me)
    try:
        # Görünürlük + engelleme + taslak filtreleri SQL seviyesinde uygulanır
        # (sayfalama sonrası Python'da süzmek PAGE_SIZE'ı tutarsız hale getirirdi).
        query = sb.table("posts").select(select_cols).or_(visible_or_filter(sb, me)).eq("is_draft", False)
        if blocked_ids:
            query = query.not_.in_("user_id", list(blocked_ids))
        posts = query.order("created_at", desc=True).range(offset, offset + PAGE_SIZE).execute().data
    except Exception:
        # sql/migration_post_visibility.sql henüz uygulanmamışsa 'visibility'
        # kolonu yok — filtresiz eski davranışa düş (feed asla kırılmasın)
        posts = sb.table("posts").select(select_cols).order(
            "created_at", desc=True
        ).range(offset, offset + PAGE_SIZE).execute().data

    has_next = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]
    _attach_post_metrics(sb, posts, me)
    attach_polls(sb, posts, me)

    return render_template("feed.html", posts=posts, me=session.get("user"),
                           page=page, has_next=has_next,
                           valid_usernames=get_valid_usernames(sb))


@bp.route("/post/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_post():
    content = request.form.get("content", "").strip()
    image_files = request.files.getlist("images")
    video_file = request.files.get("video")
    has_video = bool(video_file and video_file.filename)

    # Anket seçenekleri (en az 2 dolu olmalı) — anket de video/görsel gibi
    # TEK "ek içerik" türü/post kuralına tabi, en yüksek önceliğe sahip
    # (anket varsa görsel/video yok sayılır).
    poll_options_raw = [request.form.get(f"poll_option_{i}", "").strip() for i in range(1, 5)]
    poll_options = [o for o in poll_options_raw if o]
    has_poll = len(poll_options) >= 2

    # Video ve görsel BİRLİKTE desteklenmiyor (tek medya türü/post) — video
    # veya anket varsa görseller yok sayılır (form zaten JS ile bunu engelliyor,
    # bkz. postModal.js, ama backend de aynı kuralı uygular).
    valid_files = [] if (has_video or has_poll) else [f for f in image_files if f and f.filename]

    if has_poll and not content:
        flash("Anket için bir soru yazmalısın.", "error")
        return redirect(url_for("routes.feed"))

    if not content and not valid_files and not has_video and not has_poll:
        flash("Boş post paylaşılamaz.", "error")
        return redirect(url_for("routes.feed"))

    image_urls = []
    video_url = None
    if has_poll:
        pass  # görsel/video yok sayılır, aşağıda create_poll() ile anket eklenir
    elif has_video:
        video_url = upload_video(video_file, folder="posts")
        if not video_url:
            flash("Video yüklenemedi (geçersiz format veya 25MB'tan büyük).", "error")
            return redirect(url_for("routes.feed"))
    elif valid_files:
        # Çoklu görsel yükle (maksimum 4)
        image_urls = upload_images(valid_files, folder="posts", max_count=4)
        if not image_urls:
            flash("Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük).", "error")
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
    if visibility not in ("public", "followers"):
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
        if visibility not in ("public", "followers"):
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

    return render_template("post_detail.html", post=post, comments=comments,
                           me=session.get("user"), valid_usernames=get_valid_usernames(sb))


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
    if is_self:
        try:
            bm_rows = sb.table("bookmarks").select("post_id").eq(
                "user_id", me
            ).order("created_at", desc=True).execute().data
            bm_ids = [b["post_id"] for b in bm_rows]
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


@bp.route("/search")
@login_required
@retry_on_connection_error
def search():
    q = request.args.get("q", "").strip()
    sb = get_sb()
    if len(q) < 2:
        return render_template("search.html", q=q, users=[], posts=[], me=session.get("user"),
                               valid_usernames=get_valid_usernames(sb))

    me = _my_id()
    blocked_ids = blocked_user_ids(sb, me)

    # Kullanıcı ara (username ILIKE)
    users = sb.table("profiles").select(
        "id, username, full_name, avatar_url"
    ).ilike("username", f"%{q}%").limit(20).execute().data
    users = [u for u in users if u["id"] not in blocked_ids]

    # Post ara (content ILIKE) — beğeni/yorum sayıları feed ile aynı desende.
    # "*" kullanılıyor (açık kolon listesi değil) çünkü visibility/video_url
    # gibi opsiyonel kolonlar henüz migration'ı çalıştırılmamışsa bile PostgREST
    # hata vermez — açık isimle istenen var olmayan bir kolon HATA verirdi.
    posts = sb.table("posts").select(
        "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
    ).ilike("content", f"%{q}%").order("created_at", desc=True).limit(50).execute().data
    posts = [p for p in posts if not p.get("is_draft")]  # taslaklar aramada görünmez
    posts = filter_visible(posts, followed_and_self_ids(sb, me))
    posts = filter_not_blocked(posts, blocked_ids)
    _attach_post_metrics(sb, posts, me)
    attach_polls(sb, posts, me)

    return render_template("search.html", q=q, users=users, posts=posts, me=session.get("user"),
                           valid_usernames=get_valid_usernames(sb))