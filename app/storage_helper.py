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

# İzin verilen video uzantıları + MIME tipleri — görsellerden ayrı, daha büyük
# limit (25MB): arkadaş grubu ölçeğinde kısa klipler için yeterli, Supabase
# Storage bant genişliğini/depolamasını gereksiz şişirmesin diye sınırlı.
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}
ALLOWED_VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
MAX_VIDEO_SIZE = 25 * 1024 * 1024  # 25 MB

# Sesli mesaj — tarayıcının MediaRecorder API'si genelde .webm (Opus codec)
# üretir; küçük limit yeterli (kısa DM sesli mesajları, dakikalarca kayıt değil).
ALLOWED_AUDIO_EXTENSIONS = {".webm", ".ogg", ".mp3", ".m4a", ".wav"}
# "video/webm" da dahil: WebM konteyneri sadece ses içerse bile bazı
# tarayıcılar/işletim sistemleri bu MIME'ı üretebiliyor (dosya hâlâ .webm
# uzantılı ve gerçekte ses verisi).
ALLOWED_AUDIO_MIMES = {
    "audio/webm", "video/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav",
}
MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_extension(filename: str) -> str:
    """Dosya adından uzantıyı döndürür (örn 'photo.PNG' -> '.png')."""
    import os
    return os.path.splitext(filename)[1].lower()


# --- Magic-number (gerçek dosya baytı) doğrulaması ---------------------------
# Uzantı + istemcinin beyan ettiği Content-Type kolayca sahtelenebilir (ör.
# kötü niyetli bir .php/.svg dosyasını "photo.jpg" diye yeniden adlandırıp
# Content-Type: image/jpeg başlığıyla göndermek yeterli olurdu). Gerçek dosya
# baytlarının ilk birkaç düzinesi formatı ele verir — küçük bir proje olduğu
# için yeni bir bağımlılık (Pillow/python-magic) EKLEMEK yerine elle yazılmış
# imza kontrolü kullanılır (video/ses ile aynı desen).
#
# ÖNCEDEN burada stdlib `imghdr` kullanılıyordu — Python 3.13'te KALDIRILDI
# (PEP 594), sadece deprecated değil. Render'ın build image'ı Python 3.14'e
# geçince (render.yaml'daki PYTHON_VERSION=3.11.9 pin'i her nedense honour
# edilmedi/edilmiyor) `import imghdr` modül yükleme anında ModuleNotFoundError
# fırlatıp TÜM uygulamanın başlamasını engelledi (build başarılı, "Deploying"
# sonrası "cause could not be determined" — 2026-07-29). Elle yazılmış magic-byte
# kontrolü hiçbir stdlib/3.parti bağımlılığa dayanmadığı için Python sürümünden
# tamamen bağımsız, bir daha bu sınıf hataya açık değil.
def _detect_image_kind(header: bytes) -> str | None:
    """png/jpeg/gif/webp için bilinen magic byte imzaları."""
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


_IMAGE_EXT_TO_KIND = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
}


def _looks_like_image(header: bytes, ext: str) -> bool:
    """Gerçek dosya baytlarından formatı tespit eder; beyan edilen uzantıyla
    (ext) eşleşmiyorsa reddedilir."""
    return _detect_image_kind(header) == _IMAGE_EXT_TO_KIND.get(ext)


def _detect_video_kind(header: bytes) -> str | None:
    """mp4/mov (ISO base media, 'ftyp' kutusu 4-8. baytlarda) ve webm (EBML
    imzası \\x1a\\x45\\xdf\\xa3 baştan) için basit bir prefix kontrolü —
    ffprobe/python-magic gibi ağır bir bağımlılık gerekmeyecek kadar."""
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "iso-base-media"  # mp4 VE mov aynı kutu ailesini kullanır
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


_VIDEO_EXT_TO_KIND = {
    ".mp4": "iso-base-media",
    ".mov": "iso-base-media",
    ".webm": "webm",
}


def _looks_like_video(header: bytes, ext: str) -> bool:
    return _detect_video_kind(header) == _VIDEO_EXT_TO_KIND.get(ext)


def _detect_audio_kind(header: bytes) -> str | None:
    """wav/ogg/mp3/m4a/webm ses dosyaları için bilinen magic byte imzaları."""
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"OggS":
        return "ogg"
    if header[:3] == b"ID3" or header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "m4a"
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


_AUDIO_EXT_TO_KIND = {
    ".wav": "wav",
    ".ogg": "ogg",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".webm": "webm",
}


def _looks_like_audio(header: bytes, ext: str) -> bool:
    return _detect_audio_kind(header) == _AUDIO_EXT_TO_KIND.get(ext)


def _read_header(file_storage, n: int = 64) -> bytes:
    """Stream pozisyonunu KORUYARAK ilk n baytı okur (upload akışı devam
    edebilsin diye seek(0) ile geri sarılır)."""
    file_storage.stream.seek(0)
    header = file_storage.stream.read(n)
    file_storage.stream.seek(0)
    return header


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

    # 2.5) Magic-number kontrolü — uzantı/Content-Type sadece istemci beyanı,
    # gerçek baytlar bunlarla eşleşmezse (ör. yeniden adlandırılmış .php) reddet.
    if not _looks_like_image(_read_header(file_storage), ext):
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


def upload_video(file_storage, folder: str = "posts") -> str | None:
    """Flask FileStorage nesnesini (video) Supabase Storage'a yükler.

    upload_image ile aynı akış (uzantı+MIME+boyut kontrolü, UUID adlandırma)
    ama video'ya özel izin listesi/limit ile — bkz. ALLOWED_VIDEO_*.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = file_storage.filename
    ext = _get_extension(filename)
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return None

    mime = file_storage.mimetype or ""
    if mime not in ALLOWED_VIDEO_MIMES:
        return None

    if not _looks_like_video(_read_header(file_storage), ext):
        return None

    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_VIDEO_SIZE:
        return None

    unique_name = f"{uuid.uuid4().hex}{ext}"
    path = f"{folder}/{unique_name}"
    file_bytes = file_storage.read()

    from .supabase_client import get_sb
    try:
        get_sb().storage.from_(BUCKET_NAME).upload(
            path, file_bytes, {"content-type": mime}
        )
    except Exception as e:
        current_app.logger.error(f"Video storage upload hatası: {e}")
        return None

    return get_sb().storage.from_(BUCKET_NAME).get_public_url(path)


def upload_audio(file_storage, folder: str = "messages") -> str | None:
    """Flask FileStorage nesnesini (sesli mesaj) Supabase Storage'a yükler.

    upload_image/upload_video ile aynı akış, kendi izin listesi/limitiyle
    (bkz. ALLOWED_AUDIO_*) — tarayıcı MediaRecorder API'sinden gelen kayıtlar
    genelde .webm olur.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = file_storage.filename
    ext = _get_extension(filename)
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return None

    mime = file_storage.mimetype or ""
    if mime not in ALLOWED_AUDIO_MIMES:
        return None

    if not _looks_like_audio(_read_header(file_storage), ext):
        return None

    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_AUDIO_SIZE:
        return None

    unique_name = f"{uuid.uuid4().hex}{ext}"
    path = f"{folder}/{unique_name}"
    file_bytes = file_storage.read()

    from .supabase_client import get_sb
    try:
        get_sb().storage.from_(BUCKET_NAME).upload(
            path, file_bytes, {"content-type": mime}
        )
    except Exception as e:
        current_app.logger.error(f"Ses storage upload hatası: {e}")
        return None

    return get_sb().storage.from_(BUCKET_NAME).get_public_url(path)


def upload_images(files, folder: str = "posts", max_count: int = 4) -> list[str]:
    """Birden fazla FileStorage nesnesini yükler.

    Args:
        files: request.files.getlist('images') gibi FileStorage listesi
        folder: Storage klasörü
        max_count: maksimum görsel sayısı

    Returns:
        Başarıyla yüklenenlerin public URL listesi (boş olabilir).
    """
    urls = []
    count = 0
    for f in files:
        if count >= max_count:
            break
        url = upload_image(f, folder=folder)
        if url:
            urls.append(url)
            count += 1
    return urls
