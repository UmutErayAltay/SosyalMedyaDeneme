"""Kayıtlı arama (saved_searches) testleri — CRUD ve sahiplik kontrolü."""
import pytest

from app.supabase_client import get_sb


class TestSavedSearch:
    def test_save_search_with_valid_query(self, app, client, logged_in_session):
        """Geçerli bir sorguyu kaydetme başarılı olur."""
        user, _ = logged_in_session(
            email="saved_search_create@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "python",
                "label": "My Python Search"
            },
            follow_redirects=False,
        )
        # Başarılı kayıt -> /search?q=python'a redirect
        assert resp.status_code == 302
        assert "q=python" in resp.headers.get("Location", "")

        # DB'de gerçekten kaydedildi mi
        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq(
                "user_id", user["id"]
            ).eq("query", "python").execute().data
            assert len(saved) == 1
            assert saved[0]["query"] == "python"
            assert saved[0]["label"] == "My Python Search"

    def test_save_search_empty_query_redirects(self, app, client, logged_in_session):
        """Boş sorguyla kaydetme attemptı redirect olur."""
        user, _ = logged_in_session(
            email="saved_search_empty@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "",
                "label": "Empty"
            },
            follow_redirects=False,
        )
        # Boş query -> /search (q yok) redirect
        assert resp.status_code == 302
        assert "/search" in resp.headers.get("Location", "")

        # DB'de hiç kayıt olmadığını doğrula
        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq(
                "user_id", user["id"]
            ).eq("query", "").execute().data
            assert len(saved) == 0

    def test_save_search_without_label(self, app, client, logged_in_session):
        """Label olmadan arama kaydedilebilir (label NULL olur)."""
        user, _ = logged_in_session(
            email="saved_search_no_label@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "javascript",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq(
                "user_id", user["id"]
            ).eq("query", "javascript").execute().data
            assert len(saved) == 1
            assert saved[0]["label"] is None

    def test_delete_own_saved_search_succeeds(self, app, client, logged_in_session):
        """Kullanıcı kendi kayıtlı aramasını silebilir."""
        user, _ = logged_in_session(
            email="saved_search_delete_own@example.com",
            password="TestPass123!"
        )

        # Önce bir arama kaydet
        with app.app_context():
            sb = get_sb()
            result = sb.table("saved_searches").insert({
                "user_id": user["id"],
                "query": "rust",
                "label": "Rust Learning"
            }).execute()
            item_id = result.data[0]["id"]

        # Kendi kaydını sil
        resp = client.post(
            f"/search/saved/{item_id}/delete",
            data={
                "csrf_token": "test-csrf-token",
                "q": "rust"
            },
            follow_redirects=False,
        )
        # Silme başarılı -> redirect
        assert resp.status_code == 302

        # DB'de kayıt silinmiş mi doğrula
        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq("id", item_id).execute().data
            assert len(saved) == 0

    def test_delete_other_users_saved_search_fails(self, app, client, logged_in_session):
        """Başka kullanıcının kayıtlı aramasını silmeye çalışmak başarısız olur."""
        user1, _ = logged_in_session(
            email="saved_search_attacker@example.com",
            password="TestPass123!"
        )

        user2, _ = logged_in_session(
            email="saved_search_victim@example.com",
            password="TestPass123!"
        )

        # User2'nin bir kaydını oluştur
        with app.app_context():
            sb = get_sb()
            result = sb.table("saved_searches").insert({
                "user_id": user2["id"],
                "query": "golang",
                "label": "Go Language"
            }).execute()
            item_id = result.data[0]["id"]

        # Session'ı user1'e taşı (attacker)
        with client.session_transaction() as sess:
            sess["user"] = {
                "id": user1["id"],
                "email": user1["email"],
                "username": user1["username"]
            }
            sess["_csrf_token"] = "test-csrf-token"

        # User1 User2'nin kaydını silmeye çalış
        resp = client.post(
            f"/search/saved/{item_id}/delete",
            data={
                "csrf_token": "test-csrf-token",
                "q": "golang"
            },
            follow_redirects=False,
        )
        # Silme redirect döner ama DB'de kayıt hâlâ olmalı (RLS tarafından korunmuş)
        assert resp.status_code == 302

        # DB'de User2'nin kaydı hâlâ var mı doğrula (RLS tarafından korunmuş)
        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq(
                "id", item_id
            ).eq("user_id", user2["id"]).execute().data
            assert len(saved) == 1, "Sahip olmadığınız kaydı silemezsiniz"

    def test_multiple_saved_searches_per_user(self, app, client, logged_in_session):
        """Bir kullanıcı aynı sorguyu birden fazla kez kaydedebilir (farklı label'larla)."""
        user, _ = logged_in_session(
            email="saved_search_multiple@example.com",
            password="TestPass123!"
        )

        # Aynı sorguyu iki farklı label'la kaydet
        resp1 = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "django",
                "label": "Django Tutorial"
            },
            follow_redirects=False,
        )
        assert resp1.status_code == 302

        resp2 = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "django",
                "label": "Django Best Practices"
            },
            follow_redirects=False,
        )
        assert resp2.status_code == 302

        # Her ikisi de kayıtlı olmalı
        with app.app_context():
            sb = get_sb()
            saved = sb.table("saved_searches").select("*").eq(
                "user_id", user["id"]
            ).eq("query", "django").execute().data
            assert len(saved) == 2
            labels = {s["label"] for s in saved}
            assert "Django Tutorial" in labels
            assert "Django Best Practices" in labels

    def test_csrf_token_required_for_save(self, app, client, logged_in_session):
        """CSRF token olmadan POST başarısız olmalı."""
        logged_in_session(
            email="saved_search_no_csrf@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/search/save",
            data={
                "q": "nodejs",
                "label": "Node.js"
                # csrf_token yok
            },
            follow_redirects=False,
        )
        # app/__init__.py:150-157 csrf_protect(): token eksikse/yanlışsa
        # her zaman tam olarak 400 döner (abort(400, ...)).
        assert resp.status_code == 400

    def test_saved_search_isolation_between_users(self, app, client, logged_in_session):
        """Her kullanıcı sadece kendi kayıtlarını görür."""
        user1, _ = logged_in_session(
            email="saved_search_user1@example.com",
            password="TestPass123!"
        )

        # User1 arama kaydet
        resp = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "kubernetes",
                "label": "K8s"
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        user2, _ = logged_in_session(
            email="saved_search_user2@example.com",
            password="TestPass123!"
        )

        # User2 arama kaydet
        resp = client.post(
            "/search/save",
            data={
                "csrf_token": "test-csrf-token",
                "q": "docker",
                "label": "Docker"
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Her kullanıcı sadece kendi aramasını görmeli
        with app.app_context():
            sb = get_sb()
            user1_saved = sb.table("saved_searches").select("*").eq(
                "user_id", user1["id"]
            ).execute().data
            user2_saved = sb.table("saved_searches").select("*").eq(
                "user_id", user2["id"]
            ).execute().data

            assert len(user1_saved) == 1
            assert user1_saved[0]["query"] == "kubernetes"

            assert len(user2_saved) == 1
            assert user2_saved[0]["query"] == "docker"
