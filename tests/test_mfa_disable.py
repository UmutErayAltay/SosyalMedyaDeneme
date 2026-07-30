"""MFA (2FA) disable testleri — integration + unit tests."""
import re
import pytest
import pyotp


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


class TestMFADisableAAL2RoundTrip:
    """GERÇEK enroll → disable round-trip'i — 2026-07-30'da native (/api/v1)
    tarafında keşfedilen AAL2 bug'ının regresyon testi.

    Önceki testler (TestMFADisable) hiçbir zaman GERÇEKTEN enrolled bir 2FA'yı
    disable etmiyordu — sadece "şifre kontrolünü geçti, aktif 2FA yok" yoluna
    ulaşıyordu. Bu yüzden şu bug hiç yakalanmamıştı: kurulu supabase-auth
    kütüphanesi, VERIFIED bir TOTP factor'ü sadece AAL1 (şifre-only) session'la
    unenroll etmeye izin vermiyor ("AAL2 required to unenroll verified
    factor"). Bu test pyotp ile ÜRETİLMİŞ gerçek geçerli TOTP kodlarıyla
    enroll → verify → disable (code'suz → yanlış kod → GERÇEK kod) zincirini
    uçtan uca kanıtlar.
    """

    def _enroll_totp(self, client, email, password):
        """Web /2fa/enroll akışıyla GERÇEKTEN 2FA enroll et, secret'i döner."""
        # Adım 1: şifre POST'u → flag set + redirect (QR sayfasına)
        resp = client.post(
            "/2fa/enroll",
            data={"csrf_token": "test-csrf-token", "password": password},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Adım 2: GET → QR + secret göster
        resp = client.get("/2fa/enroll")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # Secret base32 (A-Z2-7) — Türkçe metne bağımlı olmadan ":</strong><br>"
        # sınırından sonraki base32 bloğunu yakala.
        m = re.search(r":</strong><br>([A-Z2-7]+)", body)
        assert m, f"Secret bulunamadı, body: {body[:500]}"
        secret = m.group(1)

        # Adım 3: pyotp ile GERÇEK geçerli TOTP kodu üret → POST ile doğrula
        code = pyotp.TOTP(secret).now()
        resp = client.post(
            "/2fa/enroll",
            data={"csrf_token": "test-csrf-token", "code": code},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "etkinle" in body.lower()  # "2FA başarıyla etkinleştirildi!"

        return secret

    def test_disable_without_code_then_wrong_code_then_valid_code_disables(
        self, app, client, logged_in_session
    ):
        """Enroll → disable code'suz (uyarı, unenroll'a ulaşılmaz) → yanlış kod
        (Geçersiz doğrulama kodu) → GERÇEK kod (AAL2'ye yükselt + unenroll
        BAŞARILI). Fix olmadan son adım "AAL2 required" hatasıyla patlardı
        (bkz. sınıf docstring'i + bu dosyanın altındaki manuel doğrulama notu).
        """
        email = "mfa_disable_aal2@example.com"
        password = "TestPass123!"
        user, _ = logged_in_session(email=email, password=password)

        secret = self._enroll_totp(client, email, password)

        # code'suz disable → uyarı flash, unenroll'a ulaşılmaz
        resp = client.post(
            "/2fa/disable",
            data={"csrf_token": "test-csrf-token", "password": password},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "6 haneli kodu da gir" in body

        # yanlış kod → "Geçersiz doğrulama kodu" (AAL2'ye yükseltme başarısız)
        real_code = pyotp.TOTP(secret).now()
        wrong_code = "000000" if real_code != "000000" else "111111"
        resp = client.post(
            "/2fa/disable",
            data={
                "csrf_token": "test-csrf-token",
                "password": password,
                "code": wrong_code,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "Geçersiz do" in body

        # GERÇEK kod → AAL2'ye yükselt + GERÇEKTEN unenroll et
        valid_code = pyotp.TOTP(secret).now()
        resp = client.post(
            "/2fa/disable",
            data={
                "csrf_token": "test-csrf-token",
                "password": password,
                "code": valid_code,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert "devre d" in body.lower()  # "2FA devre dışı bırakıldı."

        # Bağımsız doğrulama: taze bir sign_in_with_password + mfa.list_factors
        # ile GERÇEKTEN hiçbir verified TOTP factor kalmadığını kanıtla
        # (flash mesajına güvenmek yerine gerçek Supabase durumunu sorgula).
        from supabase import create_client

        verify_client = create_client(
            app.config["SUPABASE_URL"],
            app.config["SUPABASE_PUBLISHABLE_KEY"],
        )
        login_res = verify_client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
        assert login_res.session is not None  # code istemeden login başarılı
        factors = verify_client.auth.mfa.list_factors()
        verified = [f for f in (factors.totp or []) if f.status == "verified"]
        assert verified == [], "2FA hâlâ enrolled — unenroll GERÇEKTEN başarısız olmuş"
