"""rate_limit.py testleri — hem bellek-içi (REDIS_URL yok) hem Redis modu
(fakeredis ile — gerçek bir Redis sunucusu gerekmez, gerçek Redis
protokolünü/komutlarını bellek-içi taklit eder) sliding-window semantiğinin
BİREBİR aynı davrandığını doğrular."""
import time

import fakeredis
import pytest

from app import redis_client
from app.rate_limit import is_rate_limited, _attempts


@pytest.fixture(autouse=True)
def _reset_memory_state():
    """Her testten önce/sonra bellek-içi _attempts dict'ini temizle —
    testler birbirinin state'ine sızmasın."""
    _attempts.clear()
    yield
    _attempts.clear()


class TestRateLimitMemoryMode:
    """REDIS_URL yokken (bu test ortamının varsayılan hali) mevcut
    bellek-içi davranış — regresyon koruması."""

    def test_allows_up_to_max_attempts(self):
        key = "test:memory:allow"
        for _ in range(3):
            assert is_rate_limited(key, max_attempts=3, window_seconds=60) is False
        # 4. deneme limiti aşar
        assert is_rate_limited(key, max_attempts=3, window_seconds=60) is True

    def test_window_expiry_resets_count(self):
        key = "test:memory:expiry"
        for _ in range(3):
            is_rate_limited(key, max_attempts=3, window_seconds=1)
        assert is_rate_limited(key, max_attempts=3, window_seconds=1) is True
        time.sleep(1.1)
        # pencere geçti, sayaç sıfırlanmış olmalı
        assert is_rate_limited(key, max_attempts=3, window_seconds=1) is False

    def test_different_keys_independent(self):
        for _ in range(3):
            is_rate_limited("test:memory:key-a", max_attempts=3, window_seconds=60)
        # key-a limitte ama key-b hiç dokunulmadı
        assert is_rate_limited("test:memory:key-a", max_attempts=3, window_seconds=60) is True
        assert is_rate_limited("test:memory:key-b", max_attempts=3, window_seconds=60) is False


class TestRateLimitRedisMode:
    """REDIS_URL ayarlıymış GİBİ davranması için app.redis_client._client'a
    fakeredis enjekte edilir — gerçek Redis protokolüne karşı test edilir,
    mock değil."""

    @pytest.fixture(autouse=True)
    def _inject_fake_redis(self, monkeypatch):
        fake = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(redis_client, "_client", fake)
        monkeypatch.setattr(redis_client, "_checked", True)
        yield fake
        fake.flushall()

    def test_allows_up_to_max_attempts(self, _inject_fake_redis):
        key = "test:redis:allow"
        for _ in range(3):
            assert is_rate_limited(key, max_attempts=3, window_seconds=60) is False
        assert is_rate_limited(key, max_attempts=3, window_seconds=60) is True

    def test_window_expiry_resets_count(self, _inject_fake_redis):
        # fakeredis çağrı başına gözle görülür gecikme ekleyebiliyor (bu
        # ortamda ölçülen: >1sn/işlem) — dar bir pencere (ör. 1sn) bu
        # gecikmeyle yarışıp yanlışlıkla erken "pencere geçti" sonucu
        # verebilir (mantık hatası değil, test zamanlama payı). Geniş bir
        # pencere (10sn) + üzerinde rahat bir bekleme (11sn) kullanılıyor.
        key = "test:redis:expiry"
        for _ in range(3):
            is_rate_limited(key, max_attempts=3, window_seconds=10)
        assert is_rate_limited(key, max_attempts=3, window_seconds=10) is True
        time.sleep(11)
        assert is_rate_limited(key, max_attempts=3, window_seconds=10) is False

    def test_different_keys_independent(self, _inject_fake_redis):
        for _ in range(3):
            is_rate_limited("test:redis:key-a", max_attempts=3, window_seconds=60)
        assert is_rate_limited("test:redis:key-a", max_attempts=3, window_seconds=60) is True
        assert is_rate_limited("test:redis:key-b", max_attempts=3, window_seconds=60) is False

    def test_falls_back_to_memory_if_redis_errors(self, monkeypatch):
        """Redis pipeline.execute() hata fırlatırsa (bağlantı koptu vb.)
        bellek-içi yola sessizce düşülmeli — çağıran taraf ASLA exception
        görmemeli (rate limit best-effort, kritik yol değil)."""
        class BrokenRedis:
            def pipeline(self):
                raise ConnectionError("simulated redis outage")

        monkeypatch.setattr(redis_client, "_client", BrokenRedis())
        monkeypatch.setattr(redis_client, "_checked", True)

        key = "test:redis:broken-fallback"
        # exception fırlamamalı, bellek-içi yoldan normal çalışmalı
        assert is_rate_limited(key, max_attempts=3, window_seconds=60) is False
