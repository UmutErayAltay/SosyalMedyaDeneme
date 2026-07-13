r"""Flask test_client() ile GERÇEK DB'ye karşı hızlı doğrulama için ortak
yardımcılar. Bu proje test suite kullanmıyor (CLAUDE.md) — her doğrulama
tek seferlik bir script; bu modül CSRF/login boilerplate'ini tekrar tekrar
yazmamak için var.

Kullanım (kendi tek seferlik script'inde import et):

    import sys
    sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya")
    sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya\.claude\skills\flask-smoke-test\scripts")
    from flask_client_helper import make_client, login

    client, app = make_client()
    with app.app_context():
        login(client, "kullanici@ornek.local", "sifre")
        r = client.get("/feed")
        assert r.status_code == 200, r.status_code
        print("feed OK")
"""
import re
import sys

PROJECT_ROOT = r"C:\Users\Artemis\Desktop\sosyal-medya"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def make_client():
    from app import create_app
    app = create_app()
    app.testing = True
    return app.test_client(), app


def extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not m:
        raise ValueError("csrf_token bulunamadı — sayfa yapısı değişmiş olabilir")
    return m.group(1)


def login(client, email: str, password: str):
    """GET /login -> csrf çek -> POST /login. Session client'ta kalıcı kalır
    (aynı client ile sonraki isteklerde oturum açık)."""
    r = client.get("/login")
    csrf = extract_csrf(r.get_data(as_text=True))
    r = client.post("/login", data={
        "csrf_token": csrf, "email": email, "password": password,
    }, follow_redirects=True)
    return r


def post_form(client, path: str, data: dict, get_path: str = None):
    """CSRF gerektiren bir POST için: önce get_path'ten (yoksa path'in
    kendisinden) csrf çeker, sonra data ile birleştirip POST eder."""
    r = client.get(get_path or path)
    csrf = extract_csrf(r.get_data(as_text=True))
    payload = {"csrf_token": csrf, **data}
    return client.post(path, data=payload, follow_redirects=True)
