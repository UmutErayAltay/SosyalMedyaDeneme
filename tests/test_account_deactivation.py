"""Hesap deaktivasyonu testleri — şifre doğrulaması, DB durumu, profil
görünürlüğü ve login'de otomatik reaktivasyon."""
import pytest

from app.supabase_client import get_sb


class TestAccountDeactivation:
    def test_wrong_password_shows_error_and_stays_active(self, app, client, logged_in_session):
        user, _ = logged_in_session(
            email="deact_wrongpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/profile/deactivate",
            data={"csrf_token": "test-csrf-token", "password": "WrongPassword123!"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "ifre yanl" in body or "hatası" in body

        with app.app_context():
            sb = get_sb()
            prof = sb.table("profiles").select("is_deactivated").eq("id", user["id"]).execute().data
            assert prof[0]["is_deactivated"] is False

    def test_missing_password_shows_error(self, client, logged_in_session):
        logged_in_session(
            email="deact_nopw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/profile/deactivate",
            data={"csrf_token": "test-csrf-token"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "ifreni gir" in body or "hata" in body.lower()

    def test_correct_password_deactivates_and_logs_out(self, app, client, logged_in_session):
        user, _ = logged_in_session(
            email="deact_correctpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/profile/deactivate",
            data={"csrf_token": "test-csrf-token", "password": "TestPass123!"},
            follow_redirects=False,
        )
        # Başarılı deaktivasyon -> login'e redirect
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

        # DB'de gerçekten deaktif olarak işaretlendi mi
        with app.app_context():
            sb = get_sb()
            prof = sb.table("profiles").select("is_deactivated").eq("id", user["id"]).execute().data
            assert prof[0]["is_deactivated"] is True

        # Session temizlendi mi (bir sonraki login-gerektiren istek login'e düşmeli)
        resp2 = client.get("/profile/edit", follow_redirects=False)
        assert resp2.status_code in (302, 303)
        assert "login" in resp2.headers.get("Location", "")

    def test_deactivated_profile_hidden_from_others(self, app, client, logged_in_session):
        """Deaktif bir hesabın profili BAŞKASI tarafından görüntülenirse normal
        profil yerine 'deaktif' mesajı gösterilmeli (enumeration önleme)."""
        target, _ = logged_in_session(
            email="deact_target@example.com",
            password="TestPass123!"
        )
        with app.app_context():
            sb = get_sb()
            sb.table("profiles").update({"is_deactivated": True}).eq("id", target["id"]).execute()

        # Farklı bir kullanıcı olarak görüntüle
        viewer, _ = logged_in_session(
            email="deact_viewer@example.com",
            password="TestPass123!"
        )

        resp = client.get(f"/u/{target['username']}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "deaktif" in body.lower()

    def test_login_reactivates_deactivated_account(self, app, client, logged_in_session):
        """Deaktif bir hesap normal şifreyle login olduğunda is_deactivated
        otomatik False'a döner (silme değil, tekrar giriş = reaktivasyon)."""
        user, _ = logged_in_session(
            email="deact_reactivate@example.com",
            password="TestPass123!"
        )
        with app.app_context():
            sb = get_sb()
            sb.table("profiles").update({"is_deactivated": True}).eq("id", user["id"]).execute()

        resp = client.post(
            "/login",
            data={
                "csrf_token": "test-csrf-token",
                "email": user["email"],
                "password": "TestPass123!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "tekrar aktifle" in body.lower()

        with app.app_context():
            sb = get_sb()
            prof = sb.table("profiles").select("is_deactivated").eq("id", user["id"]).execute().data
            assert prof[0]["is_deactivated"] is False
