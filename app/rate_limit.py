"""Rate limit (IP veya kullanıcı bazlı) — auth.py'deki forgot-password
_reset_rate_limited deseninin paylaşılan/genelleştirilmiş hali.

REDIS_URL ayarlıysa Redis'teki bir ZSET (sorted set) ile sliding-window
sayım yapılır — bu, çoklu worker/instance'a (yatay ölçekleme) geçildiğinde
her worker'ın kendi belleğinde AYRI saymasını (ve dolayısıyla limitin
etkisiz kalmasını) önler. REDIS_URL yoksa (veya Redis'e erişilemezse)
ESKİ bellek-içi davranışa (tek process varsayımı, "arkadaş grubu ölçeği")
sessizce düşülür — iki yol da AYNI sliding-window semantiğini üretir.
"""
import time
import uuid

from .redis_client import get_redis

_attempts: dict[str, list[float]] = {}


def is_rate_limited(key: str, max_attempts: int, window_seconds: int) -> bool:
    """`key` başına pencere içindeki deneme sayısını artırır; limit aşıldıysa
    True döner (bu çağrı da bir deneme olarak sayılır — çağıran taraf True
    dönünce isteği reddeder)."""
    r = get_redis()
    if r is not None:
        try:
            return _is_rate_limited_redis(r, key, max_attempts, window_seconds)
        except Exception:
            pass  # Redis'e erişilemezse bellek-içi yola düş (aşağıda devam eder)

    now = time.time()
    attempts = [t for t in _attempts.get(key, []) if now - t < window_seconds]
    attempts.append(now)
    _attempts[key] = attempts
    return len(attempts) > max_attempts


def _is_rate_limited_redis(r, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Bellek-içi sürümle BİREBİR aynı sliding-window semantiği: pencere
    dışına düşen denemeleri at, yeni denemeyi ekle, toplam say. Üye adı
    zaman damgasına rastgele bir ek eklenerek üretiliyor — aynı milisaniyede
    birden fazla deneme olursa ZSET'te üye çakışıp birbirinin üzerine
    yazmasın diye (skor yine de zaman damgası, sıralama/pruning bozulmaz)."""
    zkey = f"ratelimit:{key}"
    now = time.time()
    pipe = r.pipeline()
    pipe.zremrangebyscore(zkey, 0, now - window_seconds)
    pipe.zadd(zkey, {f"{now}:{uuid.uuid4().hex}": now})
    pipe.zcard(zkey)
    pipe.expire(zkey, window_seconds)
    results = pipe.execute()
    count = results[2]
    return count > max_attempts
