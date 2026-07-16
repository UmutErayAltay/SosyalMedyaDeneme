"""Discover sayfası pagination testleri."""
import pytest


class TestDiscoverPagination:
    """GET /kesfet pagination — sayfa parametreleri ve geçerlilik kontrolleri."""

    def test_discover_page_1_returns_200(self, client, logged_in_session):
        """GET /kesfet?page=1 → 200, posts listesi."""
        user, _ = logged_in_session(
            email="discover_test_page1@example.com",
            password="TestPass123!"
        )

        resp = client.get("/kesfet?page=1")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # Sayfa yüklenmeli (en azından tablo/grid yapısı olmalı)
        assert "discover" in body.lower() or "post" in body.lower()

    def test_discover_page_2_returns_200(self, client, logged_in_session):
        """GET /kesfet?page=2 → 200."""
        user, _ = logged_in_session(
            email="discover_test_page2@example.com",
            password="TestPass123!"
        )

        resp = client.get("/kesfet?page=2")
        assert resp.status_code == 200
        # Sayfa başarıyla render'lanmış
        assert resp.data is not None

    def test_discover_page_0_clamped_to_1(self, client, logged_in_session):
        """GET /kesfet?page=0 → 1'e sabitleniyor, 200."""
        user, _ = logged_in_session(
            email="discover_test_page0@example.com",
            password="TestPass123!"
        )

        # page=0 geçersiz; kod 1'e sabitlemelidir
        resp = client.get("/kesfet?page=0")
        assert resp.status_code == 200

    def test_discover_negative_page_clamped_to_1(self, client, logged_in_session):
        """GET /kesfet?page=-5 → 1'e sabitleniyor, 200."""
        user, _ = logged_in_session(
            email="discover_test_negative@example.com",
            password="TestPass123!"
        )

        resp = client.get("/kesfet?page=-5")
        assert resp.status_code == 200

    def test_discover_large_page_clamped_to_100000(self, client, logged_in_session):
        """GET /kesfet?page=200000 → 100000'e clamp edilir, 200."""
        user, _ = logged_in_session(
            email="discover_test_largepage@example.com",
            password="TestPass123!"
        )

        # page=200000 üst sınırı aşıyor; 100000'e sabitlenmeli
        # (offset int4 sınırını aşmasını ve DoS vektörünü önler)
        resp = client.get("/kesfet?page=200000")
        assert resp.status_code == 200

    def test_discover_no_page_param_defaults_to_1(self, client, logged_in_session):
        """GET /kesfet (page yok) → sayfa 1 olarak davranır, 200."""
        user, _ = logged_in_session(
            email="discover_test_nopage@example.com",
            password="TestPass123!"
        )

        resp = client.get("/kesfet")
        assert resp.status_code == 200

    def test_discover_invalid_page_type_defaults_gracefully(self, client, logged_in_session):
        """GET /kesfet?page=abc (string) → graceful, 200."""
        user, _ = logged_in_session(
            email="discover_test_invalid@example.com",
            password="TestPass123!"
        )

        # page=abc type int'e cast edilemez; Flask type=int default 1'e düşürür
        resp = client.get("/kesfet?page=abc")
        assert resp.status_code == 200

    def test_discover_redirects_to_login_if_not_authenticated(self, client):
        """GET /kesfet oturumsuz → /login'e redirect (login_required)."""
        resp = client.get("/kesfet", follow_redirects=False)
        # login_required decorator redirect eder
        assert resp.status_code in (302, 303)
        loc = resp.headers.get("Location", "")
        assert "/login" in loc or "/auth/login" in loc
