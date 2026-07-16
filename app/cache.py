"""TTL cache — REDIS_URL ayarlıysa Redis kullanılır, yoksa (veya erişilemezse)
process-içi bellek-içi fallback'e sessizce düşülür.

Supabase round-trip'i (~0.3-0.5sn) bu projenin ana darboğazı; nadiren
değişen ve kullanıcıya-özel-olmayan veriler (kullanıcı adı listesi, gündem)
her istekte yeniden sorgulanmak yerine kısa süreli cache'lenir. Bellek-içi
modda debug reloader'ı süreçle birlikte cache'i de sıfırlar — sorun değil,
cache sadece optimizasyon, doğruluk kaynağı değil. Redis modunda birden
fazla worker/instance AYNI cache'i paylaşır (yatay ölçeklemede gerekli).
"""
import json
import time
import threading

from .redis_client import get_redis

_store: dict = {}
_lock = threading.Lock()

_REDIS_PREFIX = "cache:"


def get_cached(key: str, ttl_seconds: float, fetch_fn):
    """Cache'te taze bir değer varsa onu, yoksa fetch_fn() sonucunu döner.

    fetch_fn exception fırlatırsa cache'e yazılmaz, exception yukarı çıkar
    (çağıran taraf kendi try/except'ini korur).
    """
    r = get_redis()
    if r is not None:
        try:
            cached = r.get(_REDIS_PREFIX + key)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            pass  # Redis okunamazsa fetch_fn'e düş (aşağıda), yazmayı da dener

        value = fetch_fn()
        try:
            ttl = max(1, int(ttl_seconds))
            r.set(_REDIS_PREFIX + key, json.dumps(value), ex=ttl)
        except Exception:
            pass  # Redis'e yazılamazsa sorun değil — cache optimizasyon, doğruluk değil
        return value

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
    r = get_redis()
    if r is not None:
        try:
            keys = list(r.scan_iter(match=f"{_REDIS_PREFIX}{key_prefix}*"))
            if keys:
                r.delete(*keys)
        except Exception:
            pass
        return

    with _lock:
        for k in list(_store):
            if k.startswith(key_prefix):
                del _store[k]
