"""cache.py testleri — hem bellek-içi (REDIS_URL yok) hem Redis modu
(fakeredis) için get_cached/invalidate'in AYNI davrandığını doğrular."""
import fakeredis
import pytest

from app import redis_client
from app.cache import get_cached, invalidate, _store


@pytest.fixture(autouse=True)
def _reset_memory_state():
    _store.clear()
    yield
    _store.clear()


class TestCacheMemoryMode:
    def test_caches_fetch_result(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return {"v": calls["n"]}

        first = get_cached("test:mem:key1", ttl_seconds=60, fetch_fn=fetch)
        second = get_cached("test:mem:key1", ttl_seconds=60, fetch_fn=fetch)
        assert first == second == {"v": 1}
        assert calls["n"] == 1  # ikinci çağrıda fetch_fn TEKRAR çalışmadı

    def test_invalidate_by_prefix(self):
        get_cached("user:1:posts", ttl_seconds=60, fetch_fn=lambda: "a")
        get_cached("user:1:profile", ttl_seconds=60, fetch_fn=lambda: "b")
        get_cached("user:2:posts", ttl_seconds=60, fetch_fn=lambda: "c")

        invalidate("user:1:")

        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return "yeni-deger"

        # user:1: ile başlayanlar silindi, fetch_fn TEKRAR çağrılmalı
        get_cached("user:1:posts", ttl_seconds=60, fetch_fn=fetch)
        assert calls["n"] == 1
        # user:2: dokunulmadı, hâlâ cache'te olmalı
        assert get_cached("user:2:posts", ttl_seconds=60, fetch_fn=lambda: "DEĞİŞMEMELİ") == "c"

    def test_fetch_exception_not_cached(self):
        def fetch_fail():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            get_cached("test:mem:fail", ttl_seconds=60, fetch_fn=fetch_fail)
        # hata cache'lenmedi, sonraki çağrı fetch_fn'i tekrar dener
        assert get_cached("test:mem:fail", ttl_seconds=60, fetch_fn=lambda: "kurtuldu") == "kurtuldu"


class TestCacheRedisMode:
    @pytest.fixture(autouse=True)
    def _inject_fake_redis(self, monkeypatch):
        fake = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(redis_client, "_client", fake)
        monkeypatch.setattr(redis_client, "_checked", True)
        yield fake
        fake.flushall()

    def test_caches_fetch_result(self, _inject_fake_redis):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return {"v": calls["n"]}

        first = get_cached("test:redis:key1", ttl_seconds=60, fetch_fn=fetch)
        second = get_cached("test:redis:key1", ttl_seconds=60, fetch_fn=fetch)
        assert first == second == {"v": 1}
        assert calls["n"] == 1

    def test_invalidate_by_prefix(self, _inject_fake_redis):
        get_cached("ruser:1:posts", ttl_seconds=60, fetch_fn=lambda: "a")
        get_cached("ruser:1:profile", ttl_seconds=60, fetch_fn=lambda: "b")
        get_cached("ruser:2:posts", ttl_seconds=60, fetch_fn=lambda: "c")

        invalidate("ruser:1:")

        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return "yeni-deger"

        get_cached("ruser:1:posts", ttl_seconds=60, fetch_fn=fetch)
        assert calls["n"] == 1
        assert get_cached("ruser:2:posts", ttl_seconds=60, fetch_fn=lambda: "DEĞİŞMEMELİ") == "c"

    def test_falls_back_to_memory_if_redis_errors(self, monkeypatch):
        """Redis GET/SETEX hata fırlatırsa fetch_fn yine de çalışıp doğru
        değeri dönmeli — cache doğruluk kaynağı değil, best-effort."""
        class BrokenRedis:
            def get(self, *a, **k):
                raise ConnectionError("simulated redis outage")

        monkeypatch.setattr(redis_client, "_client", BrokenRedis())
        monkeypatch.setattr(redis_client, "_checked", True)

        result = get_cached("test:redis:broken", ttl_seconds=60, fetch_fn=lambda: "calisti")
        assert result == "calisti"
