"""MFA (2FA) disable testleri — integration + unit tests."""
import pytest


class TestMFADisable:
    """MFA disable flow — şifre doğrulaması ile korunan devre dışı bırakma."""

    def test_disable_wrong_password_shows_error(self, client, logged_in_session):
        """POST /2fa/disable yanlış şifre → "Şifre yanlış" flash.

        Not: Aktif 2FA olmadığı için "Etkin 2FA bulunamadı" uyarısına
        ulaşmaz, ancak şifre kontrolünden GEÇMEYE çalışır ve başarısız olur.
        """
        user, _ = logged_in_session(
            email="mfa_disable_wrongpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/2fa/disable",
            data={"csrf_token": "test-csrf-token", "password": "WrongPassword123!"},
            follow_redirects=True
        )
        assert resp.status_code == 200

        body = resp.data.decode("utf-8", errors="ignore")
        # "Şifre yanlış" flash mesajı
        assert "ifre yanl" in body or "hatası" in body

    def test_disable_correct_password_passes_auth_check(self, client, logged_in_session):
        """POST /2fa/disable doğru şifre → şifre kontrolünü GEÇ.

        (Aktif 2FA olmadığı için "Etkin 2FA bulunamadı" uyarısı döner.
         Önemli olan: şifre kontrolü başarılı olup "Etkin 2FA bulunamadı"
         mesajına ulaşması — yani bu akışın güvenlik aşaması geçildi.)
        """
        user, _ = logged_in_session(
            email="mfa_disable_correctpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/2fa/disable",
            data={"csrf_token": "test-csrf-token", "password": "TestPass123!"},
            follow_redirects=True
        )
        assert resp.status_code == 200

        body = resp.data.decode("utf-8", errors="ignore")
        # Aktif 2FA yok mesajı gösterilmeli (şifre kontrolünü GEÇTI demek)
        # Orjinal kod: "Etkin 2FA bulunamadı."
        assert "Etkin 2FA bulunamad" in body or "warning" in body.lower()

    def test_disable_requires_authentication(self, client):
        """POST /2fa/disable oturumsuz → CSRF middleware'i login kontrolünden
        ÖNCE devreye girer (before_request), session'da _csrf_token yoksa
        gönderilen token'la asla eşleşmez → 400. Yetkisiz bir isteğin
        unenroll'a ulaşmadığını (dolaylı da olsa) doğrular."""
        resp = client.post(
            "/2fa/disable",
            data={"csrf_token": "her-hangi-bir-token", "password": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 400


class TestUserHasPasswordIdentity:
    """Unit test — `_user_has_password_identity()` fonksiyonu."""

    def test_user_with_email_identity_returns_true(self, app, monkeypatch):
        """Email identity'si olan kullanıcı → True."""
        from app.auth import _user_has_password_identity
        from unittest.mock import Mock

        # Mock Auth client
        mock_user = Mock()
        mock_identity = Mock()
        mock_identity.provider = "email"
        mock_user.identities = [mock_identity]

        mock_auth_response = Mock()
        mock_auth_response.user = mock_user

        def mock_get_auth():
            mock_client = Mock()
            mock_client.auth.get_user.return_value = mock_auth_response
            return mock_client

        # get_auth() ve call_with_ssl_retry() mock'la
        monkeypatch.setattr("app.auth.get_auth", mock_get_auth)
        monkeypatch.setattr(
            "app.auth.call_with_ssl_retry",
            lambda f: f()  # call_with_ssl_retry sadece fonksiyonu çağırır
        )

        result = _user_has_password_identity("dummy_token")
        assert result is True

    def test_user_without_email_identity_returns_false(self, app, monkeypatch):
        """Sadece Google identity'si (email yok) → False."""
        from app.auth import _user_has_password_identity
        from unittest.mock import Mock

        # Mock Auth client
        mock_user = Mock()
        mock_identity = Mock()
        mock_identity.provider = "google"  # Email DEĞİL
        mock_user.identities = [mock_identity]

        mock_auth_response = Mock()
        mock_auth_response.user = mock_user

        def mock_get_auth():
            mock_client = Mock()
            mock_client.auth.get_user.return_value = mock_auth_response
            return mock_client

        monkeypatch.setattr("app.auth.get_auth", mock_get_auth)
        monkeypatch.setattr(
            "app.auth.call_with_ssl_retry",
            lambda f: f()
        )

        result = _user_has_password_identity("dummy_token")
        assert result is False

    def test_user_with_multiple_identities_including_email_returns_true(self, app, monkeypatch):
        """Birden fazla identity (email + google) → True (email var)."""
        from app.auth import _user_has_password_identity
        from unittest.mock import Mock

        mock_user = Mock()
        mock_email_identity = Mock()
        mock_email_identity.provider = "email"
        mock_google_identity = Mock()
        mock_google_identity.provider = "google"

        mock_user.identities = [mock_email_identity, mock_google_identity]

        mock_auth_response = Mock()
        mock_auth_response.user = mock_user

        def mock_get_auth():
            mock_client = Mock()
            mock_client.auth.get_user.return_value = mock_auth_response
            return mock_client

        monkeypatch.setattr("app.auth.get_auth", mock_get_auth)
        monkeypatch.setattr(
            "app.auth.call_with_ssl_retry",
            lambda f: f()
        )

        result = _user_has_password_identity("dummy_token")
        assert result is True

    def test_exception_during_get_user_returns_true_fail_closed(self, app, monkeypatch):
        """get_auth() exception → fail-closed davranış (True döner).

        Belirsiz durumlarda güvenlik tarafından (şifre ister) davranılır.
        """
        from app.auth import _user_has_password_identity
        from unittest.mock import Mock

        def mock_get_auth():
            mock_client = Mock()
            mock_client.auth.get_user.side_effect = Exception("Network error")
            return mock_client

        monkeypatch.setattr("app.auth.get_auth", mock_get_auth)
        monkeypatch.setattr(
            "app.auth.call_with_ssl_retry",
            lambda f: f()
        )

        result = _user_has_password_identity("dummy_token")
        assert result is True  # Fail-closed: şifre istenir
