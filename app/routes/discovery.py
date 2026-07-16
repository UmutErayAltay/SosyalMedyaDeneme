"""Arama ve algoritmik keşfet."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from flask import render_template, request, session, redirect, url_for
from . import bp
from ._common import _my_id, _attach_post_metrics, attach_repost_of, fetch_sidebar_context
from ..decorators import login_required
from ..supabase_client import get_sb, retry_on_connection_error
from ..mentions import get_valid_usernames
from ..visibility import followed_and_self_ids, close_friend_author_ids, filter_visible
from ..blocks import blocked_user_ids, filter_not_blocked
from ..polls import attach_polls


def _recent_searches(sb, me):
    """Son aramalar — search_history migration'ı henüz uygulanmamışsa boş liste
    döner, sayfa render'ı kırılmaz."""
    try:
        return sb.table("search_history").select("*").eq(
            "user_id", me
        ).order("created_at", desc=True).limit(10).execute().data
    except Exception:
        return []


@bp.route("/search")
@login_required
@retry_on_connection_error
def search():
    q = request.args.get("q", "").strip()
    search_type = request.args.get("type", "all")
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    sb = get_sb()
    me = _my_id()

    if len(q) < 2:
        return render_template(
            "search.html", q=q, users=[], posts=[], hashtags=[],
            search_type=search_type, date_from=date_from, date_to=date_to,
            recent_searches=_recent_searches(sb, me), me=session.get("user"),
            valid_usernames=get_valid_usernames(sb),
        )

    blocked_ids = blocked_user_ids(sb, me)

    # type filtresine göre gereksiz sorgu atlanır.
    users = []
    if search_type in ("all", "users"):
        # Kullanıcı ara — username VEYA full_name ILIKE (önceden sadece
        # username aranıyordu, "Ahmet" gibi tam adla arayan kullanıcı sonuç
        # bulamıyordu; PostgREST or_ syntax'ı virgülle ayrılmış koşullar alır).
        q_escaped = q.replace(",", "").replace(")", "")
        users = sb.table("profiles").select(
            "id, username, full_name, avatar_url"
        ).or_(
            f"username.ilike.%{q_escaped}%,full_name.ilike.%{q_escaped}%"
        ).limit(20).execute().data
        users = [u for u in users if u["id"] not in blocked_ids]

    posts = []
    if search_type in ("all", "posts"):
        # Post ara (content ILIKE) — beğeni/yorum sayıları feed ile aynı desende.
        # "*" kullanılıyor (açık kolon listesi değil) çünkü visibility/video_url
        # gibi opsiyonel kolonlar henüz migration'ı çalıştırılmamışsa bile PostgREST
        # hata vermez — açık isimle istenen var olmayan bir kolon HATA verirdi.
        posts_query = sb.table("posts").select(
            "*, profiles!posts_user_id_fkey(username, avatar_url), likes(count), comments(count)"
        ).ilike("content", f"%{q}%").eq("is_draft", False).eq("is_archived", False)
        if date_from:
            posts_query = posts_query.gte("created_at", date_from)
        if date_to:
            # gün sonu dahil edilsin diye 23:59:59'a kadar genişletilir
            posts_query = posts_query.lte("created_at", f"{date_to}T23:59:59")
        posts = posts_query.order("created_at", desc=True).limit(50).execute().data
        posts = [p for p in posts if not p.get("is_draft")]  # taslaklar aramada görünmez (fallback koruma)
        posts = filter_visible(sb, posts, followed_and_self_ids(sb, me), close_friend_author_ids(sb, me), me)
        posts = filter_not_blocked(posts, blocked_ids)
        _attach_post_metrics(sb, posts, me)
        attach_polls(sb, posts, me)

    hashtags = []
    if search_type in ("all", "hashtags"):
        try:
            tag_q = q[1:] if q.startswith("#") else q
            tag_rows = sb.table("hashtags").select("id, tag").ilike(
                "tag", f"%{tag_q}%"
            ).limit(20).execute().data
            # N+1 düzeltme: tüm hashtag'lerin post count'unu tek sorguda al
            if tag_rows:
                tag_ids = [h["id"] for h in tag_rows]
                post_counts = sb.table("post_hashtags").select(
                    "hashtag_id"
                ).in_("hashtag_id", tag_ids).execute().data
                counts = {}
                for pc in post_counts:
                    counts[pc["hashtag_id"]] = counts.get(pc["hashtag_id"], 0) + 1
                for h in tag_rows:
                    hashtags.append({"tag": h["tag"], "count": counts.get(h["id"], 0)})
        except Exception:
            hashtags = []  # migration_hashtags.sql henüz uygulanmamışsa boş liste

    # Arama geçmişine kaydet: aynı sorgu varsa eskisi silinir (tekilleşip
    # en üste taşınsın diye), migration henüz uygulanmamışsa sessizce atlanır.
    try:
        sb.table("search_history").delete().eq("user_id", me).eq("query", q).execute()
        sb.table("search_history").insert({"user_id": me, "query": q}).execute()
    except Exception:
        pass

    return render_template(
        "search.html", q=q, users=users, posts=posts, hashtags=hashtags,
        search_type=search_type, date_from=date_from, date_to=date_to,
        recent_searches=_recent_searches(sb, me), me=session.get("user"),
        valid_usernames=get_valid_usernames(sb),
    )


@bp.route("/search/history/<item_id>/delete", methods=["POST"])
@login_required
@retry_on_connection_error
def delete_search_history_item(item_id):
    me = _my_id()
    # Uygulama katmanı sahiplik kontrolü: sadece kendi geçmiş satırını sil
    get_sb().table("search_history").delete().eq("id", item_id).eq("user_id", me).execute()
    return redirect(url_for("routes.search", q=request.form.get("q", "")))


@bp.route("/search/history/clear", methods=["POST"])
@login_required
@retry_on_connection_error
def clear_search_history():
    me = _my_id()
    get_sb().table("search_history").delete().eq("user_id", me).execute()
    return redirect(url_for("routes.search", q=request.form.get("q", "")))


@bp.route("/kesfet")
@login_required
@retry_on_connection_error
def discover():
    """Algoritmik keşfet: takip ETMEDİĞİN kişilerin son 7 gündeki HERKESE AÇIK
    postlarından beğeni+yorum toplamına göre en popüler ~20'si. Gündem
    (trending hashtag) sayfasının post versiyonu — burada da engelleme
    ilişkileri viewer'a özel süzülür ama gündem/keşfet listesi kişiselleştirme
    açısından basit tutuldu (takip grafiği dışında bir öneri algoritması yok)."""
    from ._common import PAGE_SIZE

    sb = get_sb()
    me = _my_id()

    # Sayfalama: 1-index, geçersizse 1'e sabitle. Üst sınır: aşırı büyük bir
    # page değeri offset'i Postgres int4 sınırına yaklaştırıp RPC'nin
    # "integer out of range" hatasıyla patlamasına, bu da geniş except'in
    # bunu "migration uygulanmamış" sanıp PAHALI tam-tablo fallback'ine
    # düşmesine yol açabilirdi (kaynak tüketimi/DoS vektörü) — 100_000
    # sayfa (7 günlük veri için pratikte asla ulaşılamayacak kadar geniş
    # bir üst sınır) offset'i her zaman güvenli aralıkta tutar.
    page = max(1, min(request.args.get("page", 1, type=int), 100_000))
    offset = (page - 1) * PAGE_SIZE

    # RPC kritik yol: görünürlük + engelleme + 7 gün + skor RPC'de yapılır.
    # p_limit=PAGE_SIZE+1 istenir — feed()'deki aynı desen (bkz. posts.py):
    # tam PAGE_SIZE dönerse "daha fazla var mı" belirsiz kalırdı (off-by-one),
    # +1 fazladan satır çekilip has_more hesabından sonra atılır.
    def _fetch_discover_rpc():
        try:
            return sb.rpc("discover_page_posts", {
                "p_me": me, "p_limit": PAGE_SIZE + 1, "p_offset": offset
            }).execute().data or []
        except Exception:
            return None

    posts = _fetch_discover_rpc()

    if posts is not None:
        # RPC başarılı — sayaçlar/anket RPC'den HAZIR gelir, _attach_post_metrics
        # ÇAĞRILMAZ (olmayan `likes` embed'inden yeniden hesaplayıp sayıları
        # sıfırlardı). Sadece repost orijinalleri eklenir.
        attach_repost_of(sb, posts)
    else:
        # Fallback: eski çok-sorgulu yol (davranış birebir aynı) — migration
        # henüz uygulanmamışsa veya RPC başarısızsa çalışır.
        # NOT: Fallback yolu TÜM 7 günlük veriyi Python'a çeker, bellekte sıralama
        # yapar — ölçek büyürse bu yavaş kalır ama fallback zaten "migration henüz
        # uygulanmamışsa" durumu için var, kabul edilebilir.
        exclude_ids = followed_and_self_ids(sb, me)
        blocked_ids = blocked_user_ids(sb, me)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        select_cols = ("*, profiles!posts_user_id_fkey(username, avatar_url), "
                       "likes(count), comments(count)")
        try:
            posts = sb.table("posts").select(select_cols).gte(
                "created_at", cutoff
            ).eq("visibility", "public").eq("is_draft", False).eq("is_archived", False).execute().data
        except Exception:
            posts = sb.table("posts").select(select_cols).gte("created_at", cutoff).execute().data

        posts = [p for p in posts if p["user_id"] not in exclude_ids]
        close_friend_ids = close_friend_author_ids(sb, me)
        posts = filter_visible(sb, posts, exclude_ids, close_friend_ids, me)
        posts = filter_not_blocked(posts, blocked_ids)

        # Gizli profil kontrolü (Python fallback): is_private=true ve viewer accepted değilse gösterme
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
            posts = [p for p in posts if not (is_private_map.get(p.get("user_id"), False) and p.get("user_id") != me and p.get("user_id") not in exclude_ids)]

        # Paralel: post metrics ve polls çek
        def _attach_metrics():
            _attach_post_metrics(sb, posts, me)

        def _attach_polls_fn():
            attach_polls(sb, posts, me)

        with ThreadPoolExecutor(max_workers=2) as executor:
            metrics_future = executor.submit(_attach_metrics)
            polls_future = executor.submit(_attach_polls_fn)

            metrics_future.result()
            polls_future.result()

        for p in posts:
            p["_score"] = (p.get("like_count") or 0) + (p.get("comment_count") or 0)
        posts.sort(key=lambda p: p["_score"], reverse=True)
        posts = posts[offset:offset + PAGE_SIZE + 1]

    # has_more: PAGE_SIZE+1 istenip PAGE_SIZE'dan fazla dönmüşse daha fazla
    # var demektir (feed()'deki has_next ile aynı desen) — tam katlarda
    # (örn. toplam post sayısı tam 20/40 ise) yanlış "daha fazla var" sinyali
    # veren eski `len(posts) == PAGE_SIZE` off-by-one'ı düzeltir.
    has_more = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]

    # Yan paneller feed ile aynı (sol: profil özeti + yakın arkadaşlar,
    # sağ: öneri + gündem + aktivite) — kullanıcı isteği, Sprint 58
    sidebar = fetch_sidebar_context(sb, me)

    return render_template("discover.html", posts=posts, me=session.get("user"),
                           page=page, has_more=has_more,
                           valid_usernames=get_valid_usernames(sb), **sidebar)
