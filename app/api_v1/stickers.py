"""/stickers/* — çıkartmalar (native Android, Faz 5).

app/stickers.py'nin (web) AYNI 5 route'unun JSON/bearer-token karşılığı.
Web tarafına HİÇ DOKUNULMAZ; buradaki tek fark auth mekanizması
(`@api_login_required` + `request.api_user["id"]`) ve `abort(404/503)` yerine
native'in beklediği `{"error": "..."} + status` biçimi.

Migration-toleransı (stickers/user_stickers tabloları henüz yoksa `"does not
exist"` yakalanıp boş liste/503 dönme) web'den AYNEN korundu — özellik
kademeli açılabilir, istek 500 ile kırılmaz (blocks.py'deki AYNI desen).

Bu modül SADECE bağımsız sticker endpoint'lerini içerir; mesaja/yoruma
sticker İLİŞTİRME (api_send_message/api_add_comment'a `sticker_id` eklenmesi)
ve seçici UI AYRI bir dalganın işidir.
"""
from flask import request, jsonify

from . import bp
from ._common import api_login_required
from ..supabase_client import get_sb
from ..storage_helper import upload_image


@bp.route("/stickers/mine")
@api_login_required
def api_my_stickers():
    """Kullanıcının çıkartmaları (kendi oluşturdukları + kaydettikleri) —
    stickers.py get_my_stickers()'ın AYNI embed-join sorgusu. `mine_created`
    sunucuda hesaplanır (creator_id == me), native creator_id'yi hiç görmez."""
    sb = get_sb()
    me = request.api_user["id"]

    try:
        rows = sb.table("user_stickers").select(
            "stickers(id, image_url, creator_id)"
        ).eq("user_id", me).execute().data
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(stickers=[])
        raise

    stickers = []
    for row in rows:
        sticker = row.get("stickers")
        if sticker:
            stickers.append({
                "id": sticker["id"],
                "image_url": sticker["image_url"],
                "mine_created": sticker["creator_id"] == me,
            })

    return jsonify(stickers=stickers)


@bp.route("/stickers/<sticker_id>")
@api_login_required
def api_get_sticker(sticker_id):
    """Tek bir çıkartmanın görseli — stickers.py get_sticker() ile AYNI gerekçe:
    Realtime'dan gelen mesaj INSERT payload'ı ham satırdır (JOIN içermez), bu
    yüzden karşı tarafın gönderdiği sticker'ın görseli sonradan çekilir.
    Çıkartmalar TÜM kimlik-doğrulanmış kullanıcılara okunabilir (save_sticker()
    ile AYNI varsayım) — sahiplik kontrolü BİLEREK yok."""
    sb = get_sb()

    try:
        rows = sb.table("stickers").select("id, image_url").eq("id", sticker_id).execute().data
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(error="stickers_not_available"), 503
        raise

    if not rows:
        return jsonify(error="not_found"), 404
    return jsonify(rows[0])


@bp.route("/stickers/new", methods=["POST"])
@api_login_required
def api_create_sticker():
    """Yeni çıkartma yükle — multipart `image` (JSON DEĞİL, dosya taşıyor).
    upload_image() 5MB sınırı + magic-byte format doğrulamasını KENDİ içinde
    yapar (app/storage_helper.py), geçersizse None döner."""
    sb = get_sb()
    me = request.api_user["id"]

    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify(error="missing_image"), 400

    image_url = upload_image(image_file, folder="stickers")
    if not image_url:
        return jsonify(error="invalid_image"), 400

    try:
        res = sb.table("stickers").insert({
            "creator_id": me,
            "image_url": image_url,
        }).execute()
        if not res.data:
            return jsonify(error="create_failed"), 500
        sticker_id = res.data[0]["id"]

        # Oluşturan kişinin KENDİ listesine de eklenir — aksi halde yeni
        # çıkartma /stickers/mine'da görünmezdi (create_sticker() ile AYNI).
        sb.table("user_stickers").insert({
            "user_id": me,
            "sticker_id": sticker_id,
        }).execute()
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(error="stickers_not_available"), 503
        raise

    return jsonify(id=sticker_id, image_url=image_url), 201


@bp.route("/stickers/<sticker_id>/save", methods=["POST"])
@api_login_required
def api_save_sticker(sticker_id):
    """Başkasının çıkartmasını kendi listene ekle — save_sticker()'ın AYNI
    mantığı. Çıkartmanın GERÇEKTEN var olduğu ÖNDEN doğrulanır (yoksa 404):
    doğrulamasız bir insert, var olmayan bir id'ye de "ok" dönerek geçerli
    sticker id'lerinin taranmasına (enumeration) izin verirdi.
    Zaten listedeyse PK ihlali sessizce yutulur — idempotent."""
    sb = get_sb()
    me = request.api_user["id"]

    try:
        exists = sb.table("stickers").select("id").eq("id", sticker_id).execute().data
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(error="stickers_not_available"), 503
        raise

    if not exists:
        return jsonify(error="not_found"), 404

    try:
        sb.table("user_stickers").insert({
            "user_id": me,
            "sticker_id": sticker_id,
        }).execute()
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower():
            pass  # zaten listede — save_sticker()'daki AYNI tolerans
        elif "does not exist" in msg:
            return jsonify(error="stickers_not_available"), 503
        else:
            raise

    return jsonify(ok=True)


@bp.route("/stickers/<sticker_id>/remove", methods=["POST"])
@api_login_required
def api_remove_sticker(sticker_id):
    """Çıkartmayı KENDİ listenden kaldır — remove_sticker() ile AYNI: SADECE
    `user_stickers` satırı silinir, `stickers` satırına ASLA dokunulmaz (aksi
    halde bir kullanıcı BAŞKASININ çıkartmasını herkes için yok edebilirdi).
    Silme zaten (user_id=me) ile kısıtlı olduğu için ekstra sahiplik kontrolü
    gerekmez; olmayan bir satır için de ok döner (idempotent)."""
    sb = get_sb()
    me = request.api_user["id"]

    try:
        sb.table("user_stickers").delete().eq("user_id", me).eq(
            "sticker_id", sticker_id
        ).execute()
    except Exception as e:
        if "does not exist" in str(e):
            return jsonify(ok=True)  # tablo yoksa "listede zaten yok" — remove_sticker() ile AYNI
        raise

    return jsonify(ok=True)
