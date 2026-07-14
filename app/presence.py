"""Site çapında kullanıcı aktivitesi (last-seen / online status).

Sohbet-içi aktivitenin (is_active_in) yanında, kullanıcı profilinde veya
mesaj listesinde göstermek üzere genel bir "online status" sağlar. Process-içi
in-memory dict kullanır (mevcut _active deseniyle tutarlı); şu an tek waitress
process'te çalışan mimaride yeterli, fakat multiple worker (uWSGI vb) olursa
inconsistent olur — o durumda Supabase table-based çözüm gerekir.

Son görülme zamanı ayrıca Supabase profiles.last_seen_at'a THROTTLED şekilde
yazılır — her request'te yazmak aşırı yük olur, 60 sn'den fazla geçmemişse
yazma atlanır (DB çağrıları kesintiye uğrayan kullanıcı deneyimine yol açmaz).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# user_id -> son görülme zamanı (time.monotonic())
_last_seen: dict[str, float] = {}
_last_seen_lock = threading.Lock()
# user_id -> son DB yazımı zamanı (time.monotonic())
_last_db_write: dict[str, float] = {}
_DEFAULT_ONLINE_TTL = 120  # 2 dakika — heartbeat'siz 2dk inaktif = offline
_DB_WRITE_THROTTLE = 60  # Bir kullanıcı için sonraki DB yazımı 60sn sonra

# DB yazması (Supabase UPDATE) request'i beklemesin diye arka plan havuzu —
# messaging/views.py'deki _write_pool deseniyle aynı, `with` bloğuna alınmaz.
_write_pool = ThreadPoolExecutor(max_workers=2)


def mark_seen(user_id: str) -> None:
    """Kullanıcının (şimdi) aktif olduğunu kaydeder. Her HTTP request'te çağrılır.

    Bellek içi son-görülme zamanını hemen günceller. Supabase'e yazma 60 sn'den
    daha kısa aralıkla olmaz (DB taşkınını önlemek); 60sn geçmişse arka planda
    yazma dener (başarısızlık bellek içi durumu etkilemez, kritik olmayan).
    """
    should_write = False
    with _last_seen_lock:
        _last_seen[user_id] = time.monotonic()
        now = time.monotonic()
        last_write = _last_db_write.get(user_id, 0)

        # Throttle: son yazımdan 60sn+ geçmişse DB'ye yazmayı dene
        if now - last_write > _DB_WRITE_THROTTLE:
            _last_db_write[user_id] = now
            should_write = True

    if should_write:
        _write_pool.submit(_write_last_seen_to_db, user_id)


def _write_last_seen_to_db(user_id: str) -> None:
    """Son görülme zamanını Supabase profiles.last_seen_at'a yaz.

    _write_pool üzerinden arka planda çalışır (fire-and-forget); başarısızlık
    (Supabase inişi vs) sessizce yutulur, request'i etkilemez.
    """
    try:
        from .supabase_client import get_sb
        sb = get_sb()
        # PostgREST's now() işlevi sunucu saatini (UTC) kullanır
        sb.table("profiles").update({"last_seen_at": "now()"}).eq("id", user_id).execute()
    except Exception:
        # Supabase erişimi başarısız olsa da mark_seen'in dönerken işin devam etmesine
        # izin ver — presence sistem kritik değil (notification delivery vs değil).
        pass


def is_online(user_id: str, ttl: int = _DEFAULT_ONLINE_TTL) -> bool:
    """Kullanıcı son `ttl` saniye içinde aktif miydi (browser heartbeat / sayfa yüklemesi)?

    Profil / mesaj listesi gibi yerlerde "aktif" göstergesi için.
    Sohbet-içi aktivite (is_active_in, 45sn) daha kesin; bu daha geniş (2dk).
    """
    with _last_seen_lock:
        ts = _last_seen.get(user_id)
    if ts is None:
        return False
    return (time.monotonic() - ts) < ttl
