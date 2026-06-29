"""Görsel yükleme yardımcısı — Supabase Storage.

Güvenli dosya yükleme akışı:
1) Dosya uzantısı + MIME tipi kontrolü (sadece görsel)
2) Boyut limiti (5MB)
3) Dosya adı çakışmasını önlemek için UUID prefix
4) Supabase Storage'a yükle, public URL döndür
"""
import uuid
from flask import current_app

# İzin verilen görsel uzantıları + MIME tipleri
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
BUCKET_NAME = "media"


def _get_extension(filename: str) -> str:
    """Dosya adından uzantıyı döndürür (örn 'photo.PNG' -> '.png')."""
    import os
    return os.path.splitext(filename)[1].lower()


def upload_image(file_storage, folder: str = "avatars") -> str | None:
    """Flask FileStorage nesnesini Supabase Storage'a yükler.

    Args:
        file_storage: request.files['avatar'] gibi Flask FileStorage nesnesi
        folder: Storage içindeki klasör ('avatars' veya 'posts')

    Returns:
        Başarılıysa public URL, başarısızsa None.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = file_storage.filename

    # 1) Uzantı kontrolü
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return None

    # 2) MIME kontrolü
    mime = file_storage.mimetype or ""
    if mime not in ALLOWED_MIMES:
        return None

    # 3) Boyut kontrolü — stream pozisyonunu koru
    file_storage.stream.seek(0, 2)  # sona git
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)     # başa dön
    if size > MAX_FILE_SIZE:
        return None

    # 4) Benzersiz dosya adı: UUID + orijinal uzantı
    unique_name = f"{uuid.uuid4().hex}{ext}"
    path = f"{folder}/{unique_name}"

    # 5) Dosya içeriğini oku
    file_bytes = file_storage.read()

    # 6) Supabase Storage'a yükle
    from .supabase_client import get_sb
    try:
        get_sb().storage.from_(BUCKET_NAME).upload(
            path, file_bytes, {"content-type": mime}
        )
    except Exception as e:
        current_app.logger.error(f"Storage upload hatası: {e}")
        return None

    # 7) Public URL döndür
    return get_sb().storage.from_(BUCKET_NAME).get_public_url(path)
