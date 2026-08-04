"""JSON REST API — Repost (yeniden paylaşım), native Android.

app/routes/posts.py create_repost()'un BİREBİR mirror'ı — form yerine JSON
body kabul eder (`content` opsiyonel), aynı 6 kontrol AYNI SIRAYLA çalışır.
Ayrı bir dosyada tutulmasının nedeni: interactions.py zaten beğeni/yorum/post
oluşturmayla dolu, repost kavramsal olarak "yeni bir post türü" — kendi
dosyasında konuya göre ayrışma (api_v1/__init__.py'deki paket organizasyonu
deseniyle tutarlı).
"""
from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..blocks import is_blocked_either_way
from ..notifications import notify
from ..cache import invalidate


@bp.route("/posts/<post_id>/repost", methods=["POST"])
@api_login_required
def api_create_repost(post_id):
    """Bir postu yeniden paylaşır — posts.py create_repost() ile AYNI mantık
    (zincir düzleştirme, içeriksiz-repost tekrarı engeli, blocked/private/
    not_public/not_available kontrolleri), sadece JSON body/response."""
    sb = get_sb()
    me = request.api_user["id"]

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    content = (data.get("content") or "").strip() if isinstance(data.get("content"), str) else ""

    # 1) Orijinal postu çek
    original = sb.table("posts").select(
        "id, user_id, visibility, is_draft, is_archived, repost_of_id, content"
    ).eq("id", post_id).execute().data
    if not original:
        return jsonify(error="not_found"), 404
    original = original[0]

    # 2) Repost kısıtlarını kontrol et — create_repost()'daki AYNI sıra
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
    invalidate(f"sidebar:{me}")  # repost da posts_count'a giren yeni bir satır

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
