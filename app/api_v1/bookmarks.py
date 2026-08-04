from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb


# ----------------------- KAYDEDİLENLER (bookmarks) -----------------------
# app/social.py toggle_bookmark()/list_collections()/create_collection()/
# delete_collection()/set_bookmark_collection()'ın AYNI mantığı JSON'a taşınır
# (Faz 5, native Android — bkz. sql/migration_bookmarks.sql ve
# sql/migration_bookmark_collections.sql). Herkese açık değil, sadece sahibi
# görebilir. collection_id NULL = "Genel" (klasörsüz), çoklu koleksiyon YOK
# (bir post en fazla bir koleksiyona ait olabilir).

@bp.route("/posts/<post_id>/bookmark", methods=["POST"])
@api_login_required
def api_toggle_bookmark(post_id):
    """Kaydet/kaldır toggle — toggle_bookmark()'ın AYNI mantığı (form/redirect
    dalı YOK, native zaten JSON bekliyor)."""
    sb = get_sb()
    me = request.api_user["id"]
    body = request.get_json(silent=True) or {}

    try:
        existing = sb.table("bookmarks").select("post_id").eq(
            "post_id", post_id
        ).eq("user_id", me).execute()
        if existing.data:
            sb.table("bookmarks").delete().eq("post_id", post_id).eq("user_id", me).execute()
            bookmarked = False
        else:
            collection_id = body.get("collection_id") or None
            try:
                sb.table("bookmarks").insert({
                    "post_id": post_id, "user_id": me, "collection_id": collection_id
                }).execute()
                bookmarked = True
            except Exception:
                # migration_bookmark_collections henüz uygulanmamışsa,
                # collection_id kolonu olmayabilir
                sb.table("bookmarks").insert({"post_id": post_id, "user_id": me}).execute()
                bookmarked = True
    except Exception:
        return jsonify(error="bookmarks_not_available"), 503

    return jsonify(ok=True, bookmarked=bookmarked)


@bp.route("/collections")
@api_login_required
def api_list_collections():
    """Kullanıcının kaydetme klasörlerini döner (id, name) — list_collections()
    ile AYNI sorgu."""
    sb = get_sb()
    me = request.api_user["id"]
    try:
        cols = sb.table("bookmark_collections").select("id, name").eq(
            "user_id", me).order("created_at").execute().data
    except Exception:
        cols = []
    return jsonify(collections=cols)


@bp.route("/collections", methods=["POST"])
@api_login_required
def api_create_collection():
    """Yeni kaydetme klasörü — create_collection() ile AYNI mantık (40 karakter
    kırp)."""
    sb = get_sb()
    me = request.api_user["id"]
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:40]

    if not name:
        return jsonify(error="name_required"), 400

    try:
        col = sb.table("bookmark_collections").insert(
            {"user_id": me, "name": name}
        ).execute().data[0]
    except Exception:
        return jsonify(error="collections_not_available"), 503

    return jsonify(id=col["id"], name=col["name"])


@bp.route("/collections/<collection_id>/delete", methods=["POST"])
@api_login_required
def api_delete_collection(collection_id):
    """Klasör sil — delete_collection() ile AYNI mantık (sahiplik .eq ile
    zorunlu kılınır, içindeki bookmark'lar collection_id NULL'a düşer yani
    "Genel"e geri döner — ayrı bir taşıma adımı GEREKMEZ, FK ON DELETE SET
    NULL zaten bunu yapar)."""
    sb = get_sb()
    me = request.api_user["id"]
    try:
        sb.table("bookmark_collections").delete().eq(
            "id", collection_id).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="collections_not_available"), 503

    return jsonify(ok=True)


@bp.route("/posts/<post_id>/bookmark/collection", methods=["POST"])
@api_login_required
def api_set_bookmark_collection(post_id):
    """Kaydedilmiş bir postu başka bir klasöre taşı — set_bookmark_collection()
    ile AYNI mantık."""
    sb = get_sb()
    me = request.api_user["id"]
    body = request.get_json(silent=True) or {}
    collection_id = body.get("collection_id") or None

    try:
        sb.table("bookmarks").update({"collection_id": collection_id}).eq(
            "post_id", post_id).eq("user_id", me).execute()
    except Exception:
        return jsonify(error="bookmarks_not_available"), 503

    return jsonify(ok=True)
