"""JSON REST API (Faz 1, native Android yol haritası) testleri.

Gerçek Supabase test kullanıcısıyla çalışır (mock yok, test_user_factory
fixture'ı bkz. tests/conftest.py) — auth/token akışı güvenlik-kritik.
"""
import pytest

from app.supabase_client import get_sb


class TestApiV1Login:
    def test_login_correct_credentials_returns_token_and_me_works(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_login_ok@example.com", password="TestPass123!")

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("token")
        assert body["user"]["id"] == user["id"]
        assert body["user"]["username"] == user["username"]

        token = body["token"]
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        me_body = me_resp.get_json()
        assert me_body["user"]["id"] == user["id"]

        # Temizlik: bu testin ürettiği api_tokens satırını sil
        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_login_wrong_password_returns_401(self, client, test_user_factory):
        user = test_user_factory(email="apiv1_login_wrongpw@example.com", password="TestPass123!")

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "WrongPassword123!"},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_credentials"


class TestApiV1Feed:
    def test_feed_without_token_returns_401(self, client):
        resp = client.get("/api/v1/feed")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_feed_with_valid_token_returns_posts_shape(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_feed_ok@example.com", password="TestPass123!")

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "api_v1 feed testi için post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/feed?limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "has_next" in body
        assert any(p.get("content") == "api_v1 feed testi için post" for p in body["posts"])
        # _attach_post_metrics/enrich_post_json sözleşmesindeki alanlar (RPC veya fallback fark etmez)
        post = next(p for p in body["posts"] if p.get("content") == "api_v1 feed testi için post")
        assert "like_count" in post and "comment_count" in post and "liked_by_me" in post

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("user_id", user["id"]).execute()


class TestApiV1Logout:
    def test_logout_revokes_token_and_csrf_exempt(self, app, client, test_user_factory):
        """Ayrıca CSRF muafiyetinin GERÇEKTEN işlediğini kanıtlar: bu POST
        isteği hiçbir csrf_token/X-CSRF-Token TAŞIMAZ (sadece Bearer header) —
        eğer muafiyet çalışmasaydı web CSRF middleware'i 400 dönerdi."""
        user = test_user_factory(email="apiv1_logout@example.com", password="TestPass123!")

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        # CSRF token'ı OLMADAN POST — muafiyet çalışıyorsa 400 (CSRF) değil,
        # normal iş mantığı (ok=True) dönmeli.
        logout_resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.get_json().get("ok") is True

        # Aynı token artık geçersiz olmalı
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 401
        assert me_resp.get_json().get("error") == "unauthorized"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()
