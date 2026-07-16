"""MFA (2FA) enroll flow testleri — test_client + gerçek Supabase."""
import time
import pytest


class TestMFAEnroll:
    """MFA enrollment akışı — şifre doğrulama → QR gösterme → kod doğrulama."""

    def test_enroll_get_without_flag_shows_password_form(self, client, logged_in_session):
        """GET /2fa/enroll — flagı yok → şifre formu göster."""
        user, _ = logged_in_session(
            email="mfa_test_nopass@example.com",
            password="TestPass123!"
        )

        resp = client.get("/2fa/enroll")
        assert resp.status_code == 200

        body = resp.data.decode("utf-8", errors="ignore")
        # Şifre input'u var mı
        assert 'name="password"' in body
        # QR/Gizli anahtar gösterilmemeli
        assert "Gizli anahtar" not in body

    def test_enroll_wrong_password_shows_error_and_does_not_set_flag(
        self, client, logged_in_session
    ):
        """POST /2fa/enroll yanlış şifre → "Şifre yanlış" flash + flag set edilmez."""
        user, _ = logged_in_session(
            email="mfa_test_wrongpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/2fa/enroll",
            data={"csrf_token": "test-csrf-token", "password": "WrongPassword123!"},
            follow_redirects=True
        )
        assert resp.status_code == 200

        body = resp.data.decode("utf-8", errors="ignore")
        # Error flash'ı beklenir
        assert "ifre yanl" in body  # "Şifre yanlış" (Türkçe, büyüklük/özel char'ları göz ardı et)
        # Hâlâ şifre formunda olmalı
        assert 'name="password"' in body

        # Session'da flag set edilmemeli
        with client.session_transaction() as sess:
            assert sess.get("mfa_verified_for_enroll") is None

    def test_enroll_correct_password_sets_flag_and_redirects(
        self, client, logged_in_session
    ):
        """POST /2fa/enroll doğru şifre → flag set + 302 redirect /2fa/enroll'e."""
        user, _ = logged_in_session(
            email="mfa_test_correctpw@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/2fa/enroll",
            data={"csrf_token": "test-csrf-token", "password": "TestPass123!"},
            follow_redirects=False
        )
        # 302 redirect beklenir
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        assert "/2fa/enroll" in loc

        # Session'da flag set edilmeli (timestamp)
        with client.session_transaction() as sess:
            flag = sess.get("mfa_verified_for_enroll")
            assert flag is not None
            assert isinstance(flag, float)

    def test_enroll_get_with_flag_shows_qr_and_consumes_flag(
        self, client, logged_in_session, app
    ):
        """GET /2fa/enroll flagı set olmuşsa → QR göster + flag tüket."""
        user, _ = logged_in_session(
            email="mfa_test_withflag@example.com",
            password="TestPass123!"
        )

        # Flagı manuel set et (şifre POST'u atla)
        with client.session_transaction() as sess:
            sess["mfa_verified_for_enroll"] = time.time()

        resp = client.get("/2fa/enroll")
        assert resp.status_code == 200

        body = resp.data.decode("utf-8", errors="ignore")
        # QR/Gizli anahtar gösterilmeli
        assert "Gizli anahtar" in body or "secret" in body.lower()
        # Şifre input'u OLMAMALI (QR sayfasında yok)
        assert 'name="password"' not in body

        # Session'da flag tüketilmeli (tek kullanımlık)
        with client.session_transaction() as sess:
            assert sess.get("mfa_verified_for_enroll") is None

    def test_enroll_second_get_falls_back_to_password_form(
        self, client, logged_in_session
    ):
        """GET /2fa/enroll flagı tüketildikten sonra → şifre formuna geri dön."""
        user, _ = logged_in_session(
            email="mfa_test_secondget@example.com",
            password="TestPass123!"
        )

        # İlk GET — flag yok
        resp1 = client.get("/2fa/enroll")
        assert resp1.status_code == 200
        body1 = resp1.data.decode("utf-8", errors="ignore")
        assert 'name="password"' in body1

        # Flagı set et
        with client.session_transaction() as sess:
            sess["mfa_verified_for_enroll"] = time.time()

        # İkinci GET — QR sayfası (flag tüketilir)
        resp2 = client.get("/2fa/enroll")
        assert resp2.status_code == 200
        body2 = resp2.data.decode("utf-8", errors="ignore")
        assert "Gizli anahtar" in body2 or "secret" in body2.lower()

        # Flag'in tüketildiğini doğrula
        with client.session_transaction() as sess:
            assert sess.get("mfa_verified_for_enroll") is None

        # Üçüncü GET — flagı atlandığı için yine şifre formu
        resp3 = client.get("/2fa/enroll")
        assert resp3.status_code == 200
        body3 = resp3.data.decode("utf-8", errors="ignore")
        assert 'name="password"' in body3


def test_enroll_redirects_to_login_if_not_authenticated(client):
    """GET /2fa/enroll oturumsuz → /login'e redirect."""
    resp = client.get("/2fa/enroll", follow_redirects=False)
    assert resp.status_code in (302, 303)
    loc = resp.headers.get("Location", "")
    assert "/login" in loc or "/auth/login" in loc
