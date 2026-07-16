"""Test suite shared fixtures — gerçek Supabase'e karşı çalışır.

Test kullanıcıları sb.auth.admin.create_user ile oluşturulup
test sonunda sb.auth.admin.delete_user ile silinir (cleanup).
"""
import pytest
from app import create_app
from app.supabase_client import get_sb
from supabase import create_client


@pytest.fixture(scope="session")
def app():
    """Flask app fixture — session scope (tüm testler için bir kez)."""
    app_instance = create_app()
    app_instance.config["TESTING"] = True
    return app_instance


@pytest.fixture
def client(app):
    """Flask test_client fixture — function scope (her test için taze)."""
    return app.test_client()


def _cleanup_test_user(sb, email):
    """Belirtilen email ile test kullanıcısı varsa sil (ve profile'i)."""
    try:
        existing = sb.auth.admin.list_users()
        for u in existing:
            if u.email == email:
                sb.auth.admin.delete_user(u.id)
                # Profile'i de sil (foreign key cascade'i yerine manual)
                try:
                    sb.table("profiles").delete().eq("id", u.id).execute()
                except Exception:
                    pass
                return
    except Exception:
        pass


@pytest.fixture
def test_user_factory(app):
    """Test kullanıcı factory fixture — parametre olarak email/password/username alıp
    Supabase'de gerçek kullanıcı + profile oluşturur, yield eder, sonra siler.

    Kullanım:
        user = test_user_factory(email="test@example.com", password="TestPass123!")
    """
    created_emails = []

    def _create_user(email: str, password: str, username: str = None):
        """Create a test user and return user_id + login info."""
        if username is None:
            # Email'den otomatik username üret (@ öncesi)
            username = email.split("@")[0].replace(".", "_")

        with app.app_context():
            sb = get_sb()

            # Cleanup: eski kalıntı temizle
            _cleanup_test_user(sb, email)

            # User oluştur
            user = sb.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True  # Email doğrulama bypass
            })
            user_id = user.user.id

            # Profile oluştur
            sb.table("profiles").upsert({
                "id": user_id,
                "username": username,
                "email": email
            }, on_conflict="id").execute()

            # Login yapıp token'ları al (publishable key ile)
            tmp_client = create_client(
                app.config["SUPABASE_URL"],
                app.config["SUPABASE_PUBLISHABLE_KEY"]
            )
            login_res = tmp_client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            created_emails.append(email)

            return {
                "id": user_id,
                "email": email,
                "username": username,
                "access_token": login_res.session.access_token,
                "refresh_token": login_res.session.refresh_token
            }

    yield _create_user

    # Cleanup: tüm oluşturulan kullanıcıları sil
    with app.app_context():
        sb = get_sb()
        for email in created_emails:
            _cleanup_test_user(sb, email)


@pytest.fixture
def logged_in_session(client, test_user_factory):
    """Helper: test_user'ı oluşturur ve client session'ında login eder.

    Kullanım:
        user, session_client = logged_in_session(email="test@example.com", password="TestPass123!")
    """
    def _setup(email: str, password: str, username: str = None):
        """Create user, login, return (user_dict, test_client)."""
        user = test_user_factory(email=email, password=password, username=username)

        # Session'ı setup et
        with client.session_transaction() as sess:
            sess["user"] = {
                "id": user["id"],
                "email": user["email"],
                "username": user["username"]
            }
            sess["access_token"] = user["access_token"]
            sess["refresh_token"] = user["refresh_token"]
            sess["_csrf_token"] = "test-csrf-token"

        return user, client

    return _setup
