"""JSON REST API — Post yönetimi (düzenle/sil/arşivle/sabitle), native Android.

app/routes/posts.py'deki edit_post/delete_post/toggle_archive/toggle_pin
mirror'ı — form+redirect+flash yerine JSON body/response, iş mantığı BİREBİR
aynı (yeni mantık eklenmedi). Ayrı bir dosyada tutulmasının nedeni:
interactions.py beğeni/yorum/post-oluşturma ile dolu, "post yönetimi" (sahibinin
kendi postuna uyguladığı yaşam döngüsü işlemleri) kavramsal olarak ayrı bir
konu — reposts.py ile AYNI dosya-başına-konu deseni.

URL şekli: reposts.py'nin `/posts/<post_id>/repost` desenini izler (web'de
`/post/` tekil, api_v1'de `/posts/` çoğul — api_v1 içindeki tutarlılık esas).

Sahiplik: get_sb() service-role RLS'i bypass ettiği için sahiplik kontrolü
uygulama katmanında AÇIKÇA yapılır. delete'te web sürümü `.eq("user_id", me)`
ile sessiz no-op yapıyor (başkasının postunu silmeye çalışan "başarılı" cevap
alıyor); API'de bu yetersiz — önce satır çekilir ve sahibi değilse 404 döner
(403 değil: 403 "bu post var ama senin değil" bilgisini sızdırıp enumeration'a
izin verirdi, bkz. post_detail'daki visibility kontrolü).
"""
from datetime import datetime, timezone

from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..hashtags import sync_post_hashtags
from ..mentions import notify_mentions, extract_mentions
from ..cache import invalidate


@bp.route("/posts/<post_id>/edit", methods=["POST"])
@api_login_required
def api_edit_post(post_id):
    """Post metnini düzenler — posts.py edit_post() ile AYNI mantık.

    SADECE metin (+opsiyonel visibility) değişir; görsel/video kapsam dışı,
    media alanlarına hiç dokunulmaz. Düzenlenince edited_at damgalanır,
    hashtag'ler yeniden senkronlanır ve SADECE YENİ eklenen @mention'lara
    bildirim gider (eski içerikte zaten var olanlar tekrar bildirim üretmesin).
    """
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("*").eq("id", post_id).execute().data
    if not post:
        return jsonify(error="not_found"), 404
    post = post[0]
    if post["user_id"] != me:
        # Sahibi değilim: postun varlığını doğrulamamak için 404 (403 değil)
        return jsonify(error="not_found"), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    content = data.get("content").strip() if isinstance(data.get("content"), str) else ""

    has_media = post.get("image_urls") or post.get("image_url") or post.get("video_url")
    if not content and not has_media:
        return jsonify(error="empty_post"), 400

    visibility = data.get("visibility") if isinstance(data.get("visibility"), str) else None
    if not visibility:
        visibility = post.get("visibility") or "public"
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

    # Taslak henüz yayınlanmadıysa hashtag senkronu/mention bildirimi ERTELENİR
    # (bkz. publish_draft()) — içerik herkese açık değilken tetiklemek yanlış olurdu.
    if not post.get("is_draft"):
        sync_post_hashtags(sb, post_id, content)
        invalidate("trending:")  # Gündem cache'i güncelle
        if added_mentions:
            # Sentetik "@a @b" metni: notify_mentions zaten extract_mentions ile
            # ayrıştırıyor, böylece SADECE yeni eklenenler bildirilir
            notify_mentions(sb, actor_id=me, content=" ".join(f"@{u}" for u in added_mentions), post_id=post_id)

    return jsonify(
        ok=True,
        post_id=post_id,
        content=content,
        visibility=visibility,
        edited_at=update_data["edited_at"],
    )


@bp.route("/posts/<post_id>/delete", methods=["POST"])
@api_login_required
def api_delete_post(post_id):
    """Postu siler — sadece sahibi. Web sürümünden FARKI: sahiplik sorgu
    filtresine gömülü değil, önden kontrol edilir ve başkasının postunda 404
    döner (web'deki sessiz no-op, native client'a yanıltıcı "silindi" cevabı
    verirdi)."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("id, user_id").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        return jsonify(error="not_found"), 404

    sb.table("posts").delete().eq("id", post_id).eq("user_id", me).execute()
    invalidate(f"sidebar:{me}")  # posts_count bayatlamasın
    return jsonify(ok=True, post_id=post_id)


@bp.route("/posts/<post_id>/archive", methods=["POST"])
@api_login_required
def api_toggle_archive(post_id):
    """Postu arşivle/arşivden çıkar (toggle) — posts.py toggle_archive() aynısı.
    Sahiplik kontrolü sorguya gömülü: başkasının postu bulunamaz sayılır (404).
    Yeni durum JSON'da döner ki native UI ek istek atmadan güncellensin."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("id, is_archived").eq("id", post_id).eq(
        "user_id", me
    ).execute().data
    if not post:
        return jsonify(error="not_found"), 404

    is_currently_archived = bool(post[0].get("is_archived", False))

    update_data = {"is_archived": not is_currently_archived}
    # Arşivlerken damgala, çıkarken temizle
    update_data["archived_at"] = (
        None if is_currently_archived else datetime.now(timezone.utc).isoformat()
    )

    sb.table("posts").update(update_data).eq("id", post_id).execute()

    return jsonify(ok=True, post_id=post_id, is_archived=not is_currently_archived)


@bp.route("/posts/<post_id>/pin", methods=["POST"])
@api_login_required
def api_toggle_pin(post_id):
    """Profilde en üstte gösterilecek 1 postu seç/kaldır (toggle).

    profiles.pinned_post_id TEK bir kolon olduğu için "en fazla 1 sabit post"
    kısıtı otomatik sağlanır — yeni bir post sabitlemek eskisinin yerini alır,
    ayrı bir unpin adımı gerekmez. Migration uygulanmadıysa (kolon yok)
    503 unavailable döner (web'de flash'la bildiriliyordu)."""
    sb = get_sb()
    me = request.api_user["id"]

    post = sb.table("posts").select("user_id").eq("id", post_id).execute().data
    if not post or post[0]["user_id"] != me:
        # Sahibi değilim / yok: enumeration'ı önlemek için 404
        return jsonify(error="not_found"), 404

    try:
        prof = sb.table("profiles").select("pinned_post_id").eq("id", me).execute().data
        current = prof[0].get("pinned_post_id") if prof else None
        if current == post_id:
            sb.table("profiles").update({"pinned_post_id": None}).eq("id", me).execute()
            pinned = False
        else:
            sb.table("profiles").update({"pinned_post_id": post_id}).eq("id", me).execute()
            pinned = True
    except Exception:
        # pinned_post_id kolonu yok (migration uygulanmamış)
        return jsonify(error="unavailable"), 503

    return jsonify(ok=True, post_id=post_id, pinned=pinned)
