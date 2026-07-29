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


class TestApiV1Discover:
    def test_discover_without_token_returns_401(self, client):
        resp = client.get("/api/v1/discover")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_discover_with_valid_token_returns_posts_shape(self, app, client, test_user_factory):
        # Viewer + ayrı bir yazar hesabı: discover() kendi postlarını/takip
        # ettiklerini HARİÇ TUTAR (exclude_ids = followed_and_self_ids), bu
        # yüzden görünürlüğü doğrulamak için içerik BAŞKA bir hesaptan gelmeli.
        viewer = test_user_factory(email="apiv1_discover_viewer@example.com", password="TestPass123!")
        author = test_user_factory(email="apiv1_discover_author@example.com", password="TestPass123!")

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": viewer["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 discover testi için herkese açık post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/discover?page=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "has_more" in body
        assert body.get("page") == 1
        assert isinstance(body["posts"], list)
        assert any(p.get("content") == "api_v1 discover testi için herkese açık post" for p in body["posts"])
        post = next(p for p in body["posts"] if p.get("content") == "api_v1 discover testi için herkese açık post")
        # _attach_post_metrics/enrich_post_json sözleşmesindeki alanlar (RPC veya fallback fark etmez)
        assert "like_count" in post and "comment_count" in post and "liked_by_me" in post

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("user_id", author["id"]).execute()


class TestApiV1Search:
    def test_search_short_query_returns_empty_results_but_history_fields_present(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_short@example.com", password="TestPass123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        # q 2 karakterden kısa (tek harf) — asıl arama YAPILMAZ, sadece
        # recent/saved searches alanları döner (discovery.py search() ile aynı davranış).
        resp = client.get(
            "/api/v1/search?q=a",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["users"] == [] and body["posts"] == [] and body["hashtags"] == []
        assert "recent_searches" in body and "saved_searches" in body

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_search_without_token_returns_401(self, client):
        resp = client.get("/api/v1/search?q=deneme")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_search_type_users_filters_out_posts_and_hashtags(self, app, client, test_user_factory):
        searcher = test_user_factory(email="apiv1_search_searcher@example.com", password="TestPass123!")
        target = test_user_factory(
            email="apiv1_search_target@example.com", password="TestPass123!",
            username="apiv1uniqsearchtarget",
        )

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": searcher["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        resp = client.get(
            "/api/v1/search?q=apiv1uniqsearchtarget&type=users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # type=users filtresi çalışıyor: sadece users doldurulmuş, posts/hashtags boş
        assert any(u.get("username") == "apiv1uniqsearchtarget" for u in body["users"])
        assert body["posts"] == [] and body["hashtags"] == []

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", searcher["id"]).execute()


class TestApiV1SearchSaveAndHistory:
    def test_save_search_then_delete_saved_search_roundtrip(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_save@example.com", password="TestPass123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        save_resp = client.post(
            "/api/v1/search/save",
            json={"q": "apiv1 kayıtlı arama testi", "label": "test etiketi"},
            headers=headers,
        )
        assert save_resp.status_code == 200
        assert save_resp.get_json().get("ok") is True

        with app.app_context():
            rows = get_sb().table("saved_searches").select("id").eq(
                "user_id", user["id"]
            ).eq("query", "apiv1 kayıtlı arama testi").execute().data
        assert rows, "saved_searches satırı bulunamadı"
        saved_id = rows[0]["id"]

        # Round-trip: kaydedilen aramayı sahiplik kontrolüyle sil
        delete_resp = client.post(
            f"/api/v1/search/saved/{saved_id}/delete",
            headers=headers,
        )
        assert delete_resp.status_code == 200
        assert delete_resp.get_json().get("ok") is True

        with app.app_context():
            remaining = get_sb().table("saved_searches").select("id").eq("id", saved_id).execute().data
        assert remaining == []

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_save_search_missing_query_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_save_missing@example.com", password="TestPass123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

        resp = client.post(
            "/api/v1/search/save",
            json={"q": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "missing_query"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_clear_search_history_and_delete_history_item(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_history@example.com", password="TestPass123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # search_history'ye kayıt oluşturmak için gerçek bir arama at (search() endpoint'i
        # her başarılı q>=2 aramasında search_history'ye yazar).
        client.get("/api/v1/search?q=apiv1geçmiştest", headers=headers)

        with app.app_context():
            rows = get_sb().table("search_history").select("id").eq(
                "user_id", user["id"]
            ).eq("query", "apiv1geçmiştest").execute().data
        assert rows, "search_history satırı bulunamadı"
        history_id = rows[0]["id"]

        delete_resp = client.post(
            f"/api/v1/search/history/{history_id}/delete",
            headers=headers,
        )
        assert delete_resp.status_code == 200
        assert delete_resp.get_json().get("ok") is True

        with app.app_context():
            remaining = get_sb().table("search_history").select("id").eq("id", history_id).execute().data
        assert remaining == []

        clear_resp = client.post("/api/v1/search/history/clear", headers=headers)
        assert clear_resp.status_code == 200
        assert clear_resp.get_json().get("ok") is True

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()
            get_sb().table("search_history").delete().eq("user_id", user["id"]).execute()
