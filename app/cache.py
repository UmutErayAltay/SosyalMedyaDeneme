"""Süreç-içi TTL cache — Redis'e gerek yok (tek process, küçük veri).

Supabase round-trip'i (~0.3-0.5sn) bu projenin ana darboğazı; nadiren
değişen ve kullanıcıya-özel-olmayan veriler (kullanıcı adı listesi, gündem)
her istekte yeniden sorgulanmak yerine kısa süreli cache'lenir. debug
reloader'ı süreçle birlikte cache'i de sıfırlar — sorun değil, cache
sadece optimizasyon, doğruluk kaynağı değil.
"""
import time
import threading

_store: dict = {}
_lock = threading.Lock()


def get_cached(key: str, ttl_seconds: float, fetch_fn):
    """Cache'te taze bir değer varsa onu, yoksa fetch_fn() sonucunu döner.

    fetch_fn exception fırlatırsa cache'e yazılmaz, exception yukarı çıkar
    (çağıran taraf kendi try/except'ini korur).
    """
    now = time.time()
    with _lock:
        hit = _store.get(key)
        if hit and now - hit[0] < ttl_seconds:
            return hit[1]
    value = fetch_fn()
    with _lock:
        _store[key] = (now, value)
    return value


def invalidate(key_prefix: str) -> None:
    """key_prefix ile başlayan tüm girdileri düşürür (örn. kullanıcı kaydında
    'valid_usernames', bildirim okununca 'unread:<user_id>')."""
    with _lock:
        for k in list(_store):
            if k.startswith(key_prefix):
                del _store[k]
