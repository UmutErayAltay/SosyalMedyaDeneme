"""Site çapında kullanıcı aktivitesi (last-seen / online status).

Sohbet-içi aktivitenin (is_active_in) yanında, kullanıcı profilinde veya
mesaj listesinde göstermek üzere genel bir "online status" sağlar. Process-içi
in-memory dict kullanır (mevcut _active deseniyle tutarlı); şu an tek waitress
process'te çalışan mimaride yeterli, fakat multiple worker (uWSGI vb) olursa
inconsistent olur — o durumda Supabase table-based çözüm gerekir.
"""
import threading
import time

# user_id -> son görülme zamanı (time.monotonic())
_last_seen: dict[str, float] = {}
_last_seen_lock = threading.Lock()
_DEFAULT_ONLINE_TTL = 120  # 2 dakika — heartbeat'siz 2dk inaktif = offline


def mark_seen(user_id: str) -> None:
    """Kullanıcının (şimdi) aktif olduğunu kaydeder. Her HTTP request'te çağrılır."""
    with _last_seen_lock:
        _last_seen[user_id] = time.monotonic()


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
