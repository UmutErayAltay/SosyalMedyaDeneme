"""Bellek-içi rate limit (IP veya kullanıcı bazlı) — auth.py'deki forgot-password
_reset_rate_limited deseninin paylaşılan/genelleştirilmiş hali. Tek process
varsayımıyla çalışır (Redis yok); bu ölçekte (arkadaş grubu) yeterli —
çoklu worker'a geçilirse Redis gerekir.
"""
import time

_attempts: dict[str, list[float]] = {}


def is_rate_limited(key: str, max_attempts: int, window_seconds: int) -> bool:
    """`key` başına pencere içindeki deneme sayısını artırır; limit aşıldıysa
    True döner (bu çağrı da bir deneme olarak sayılır — çağıran taraf True
    dönünce isteği reddeder)."""
    now = time.time()
    attempts = [t for t in _attempts.get(key, []) if now - t < window_seconds]
    attempts.append(now)
    _attempts[key] = attempts
    return len(attempts) > max_attempts
