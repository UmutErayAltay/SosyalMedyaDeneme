"""Çıkartmalar (stickers) — kullanıcıların oluşturduğu veya tasarımcıdan aldığı resimler
mesajlara ve yorumlara eklenmek üzere."""
from flask import Blueprint, request, session, abort, jsonify
from .decorators import login_required
from .supabase_client import get_sb, retry_on_connection_error
from .storage_helper import upload_image

bp = Blueprint("stickers", __name__, url_prefix="/stickers")


@bp.route("/mine", methods=["GET"])
@login_required
@retry_on_connection_error
def get_my_stickers():
    """Kullanıcının çıkartmalarını döner: kendi oluşturduğu + kaydettiği.

    Returns:
        JSON: {"stickers": [{"id", "image_url", "mine_created": bool}]}
        Sticker yoksa veya tablo oluşturulmamışsa: {"stickers": []}
    """
    sb = get_sb()
    me = session["user"]["id"]

    try:
        # user_stickers ile JOIN: kullanıcının listesindeki sticker'lar
        data = sb.table("user_stickers").select(
            "stickers(id, image_url, creator_id)"
        ).eq("user_id", me).execute().data

        stickers = []
        for row in data:
            sticker = row.get("stickers")
            if sticker:
                stickers.append({
                    "id": sticker["id"],
                    "image_url": sticker["image_url"],
                    "mine_created": sticker["creator_id"] == me
                })

        return jsonify({"stickers": stickers})
    except Exception as e:
        # Tablo henüz oluşturulmamış
        if "does not exist" in str(e):
            return jsonify({"stickers": []}), 200
        raise


@bp.route("/<sticker_id>", methods=["GET"])
@login_required
@retry_on_connection_error
def get_sticker(sticker_id):
    """Tek bir çıkartmanın görselini döner — Realtime'dan gelen mesaj INSERT
    payload'ı ham satır olduğu için JOIN içermez (bkz. chat.js); karşı tarafın
    gönderdiği sticker'ı panel yenilenmeden göstermek için kullanılır.
    Sticker'lar global okunabilir (save_sticker() ile de aynı varsayım).

    Returns:
        JSON: {"id", "image_url"} — bulunamazsa 404, tablo yoksa 503
    """
    sb = get_sb()
    try:
        row = sb.table("stickers").select("id, image_url").eq("id", sticker_id).execute()
        if not row.data:
            abort(404)
        return jsonify(row.data[0])
    except Exception as e:
        if "does not exist" in str(e):
            abort(503)
        raise


@bp.route("/new", methods=["POST"])
@login_required
@retry_on_connection_error
def create_sticker():
    """Yeni bir çıkartma oluştur ve yükle.

    Form data:
        image (file): Çıkartma görseli (5MB, görsel formatı)

    Returns:
        JSON: {"id", "image_url"} — başarılıysa 201
        {"error"}: başarısızsa 400 veya 503
    """
    sb = get_sb()
    me = session["user"]["id"]

    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify({"error": "Görsel seçilmedi"}), 400

    # Görseli yükle
    image_url = upload_image(image_file, folder="stickers")
    if not image_url:
        return jsonify({"error": "Görsel yüklenemedi (geçersiz format veya 5MB'tan büyük)"}), 400

    try:
        # Sticker oluştur
        res = sb.table("stickers").insert({
            "creator_id": me,
            "image_url": image_url,
        }).execute()
        if not res.data:
            return jsonify({"error": "Veritabanı hatası"}), 500
        sticker_id = res.data[0]["id"]

        # Kullanıcının listesine ekle
        sb.table("user_stickers").insert({
            "user_id": me,
            "sticker_id": sticker_id,
        }).execute()

        return jsonify({"id": sticker_id, "image_url": image_url}), 201
    except Exception as e:
        # Tablo henüz oluşturulmamış
        if "does not exist" in str(e) or "stickers" in str(e):
            return jsonify({"error": "Özellik henüz aktif değil"}), 503
        raise


@bp.route("/<sticker_id>/save", methods=["POST"])
@login_required
@retry_on_connection_error
def save_sticker(sticker_id):
    """Başkasının çıkartmasını kendi listesine ekle.

    Returns:
        JSON: {"ok": true} — çıkartma listede varsa da ok
        404: çıkartma bulunamadı
    """
    sb = get_sb()
    me = session["user"]["id"]

    try:
        # Çıkartma var mı? (enumeration koruması)
        sticker = sb.table("stickers").select("id").eq("id", sticker_id).execute()
        if not sticker.data:
            abort(404)

        # user_stickers'a ekle — zaten varsa PK ihlali, ignore
        try:
            sb.table("user_stickers").insert({
                "user_id": me,
                "sticker_id": sticker_id,
            }).execute()
        except Exception as e:
            # Çift ekleme hatası (UNIQUE constraint) — sessizce kabullen
            if "duplicate" in str(e).lower():
                pass
            else:
                raise

        return jsonify({"ok": True}), 200
    except Exception as e:
        if "does not exist" in str(e):
            abort(503)
        raise


@bp.route("/<sticker_id>/remove", methods=["POST"])
@login_required
@retry_on_connection_error
def remove_sticker(sticker_id):
    """Çıkartmayı kendi listesinden kaldır (sadece user_stickers satırı silinir).

    Returns:
        JSON: {"ok": true}
    """
    sb = get_sb()
    me = session["user"]["id"]

    try:
        sb.table("user_stickers").delete().eq("user_id", me).eq(
            "sticker_id", sticker_id
        ).execute()
        return jsonify({"ok": True}), 200
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify({"ok": True}), 200  # Tablo yoksa başarılı kabul et
        raise
