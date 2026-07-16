"""Redis bağlantısı — REDIS_URL ortam değişkeni varsa kullanılır, yoksa
(veya bağlantı başarısız olursa) None döner ve çağıran taraf (rate_limit.py,
cache.py) mevcut process-içi/bellek-içi fallback'ine düşer — davranış
Redis olmayan ortamlarda BİREBİR korunur (tek-process küçük ölçek, mevcut
proje varsayımı, bkz. cache.py'nin eski docstring'i).

Bağlantı/health-check bir kez yapılıp süreç boyunca cache'lenir — get_sb()/
get_auth() ile aynı singleton deseni. Redis çalışırken sonradan erişilemez
hale gelirse (bağlantı kopması) bunu YENİDEN denemez — cache/rate-limit
sadece optimizasyon/best-effort olduğu için tek seferlik "yok" kararı
yeterli kabul edildi (aksi halde her çağrıda tekrar ping atmak gecikme
ekler)."""
import os

_client = None
_checked = False


def get_redis():
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        _client = client
    except Exception:
        _client = None
    return _client


def _reset_for_testing():
    """SADECE testler için — singleton durumunu sıfırlar (fakeredis ile
    test ederken her testin kendi bağlantısını enjekte edebilmesi için)."""
    global _client, _checked
    _client = None
    _checked = False
