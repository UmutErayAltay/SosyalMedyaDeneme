"""Realtime broadcast senkronizasyonu testi — iki kullanıcı arasında typing indicator.

Bu test PYTEST + PLAYWRIGHT arasında AYRI İKİ SÜREÇ olarak çalışır (Playwright
gerçek bir tarayıcıda giriş yapıp DOM doğrulaması yapmak zorunda, pytest'in
kendi test_client'ı bunu yapamaz). Bu yüzden setup/cleanup normal fixture
yield-teardown deseniyle YAPILAMAZ: `test_user_factory` fixture'ı (bkz.
conftest.py) oluşturduğu kullanıcıları KENDİ teardown'ında, yani BU pytest
komutu biter bitmez siler — Playwright ayrı bir `npx playwright test` çağrısı
olarak SONRADAN çalıştığında kullanıcılar/conversation zaten silinmiş olurdu
(ilk sürümde tam olarak bu oldu: her iki gerçek kullanıcı da kendi
conversation'ında 403 Forbidden aldı, çünkü conversation_participants satırları
JSON yazıldıktan hemen sonra silinmişti).

Doğru akış (bkz. package.json test:e2e:realtime script'i):
    1. python -m pytest tests/test_realtime_broadcast.py::test_realtime_setup
    2. npx playwright test e2e/realtime-broadcast.spec.js
    3. python -m pytest tests/test_realtime_broadcast.py::test_realtime_cleanup

setup ve cleanup BİLEREK ayrı test fonksiyonları (fixture teardown'ı DEĞİL) —
aralarında başka bir process (Playwright) çalışacağı için veri iki adım
arasında canlı kalmalı.
"""
import json
import os

from app import create_app
from app.supabase_client import get_sb

DATA_FILE = "e2e/test-data/realtime-users.json"


def _make_app():
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_realtime_setup():
    """İki gerçek test kullanıcısı + paylaşılan grup conversation'ı oluşturur,
    Playwright'ın okuyacağı JSON'a export eder. BİLEREK teardown YOK —
    Playwright bitene kadar veri canlı kalmalı (bkz. modül docstring'i)."""
    app = _make_app()
    with app.app_context():
        sb = get_sb()

        suffix = os.urandom(4).hex()
        users = []
        for i in (1, 2):
            email = f"realtime-user{i}-{os.urandom(8).hex()}@test.local"
            password = "Test_Pass_123!"
            username = f"rtuser{i}_{suffix}"
            user = sb.auth.admin.create_user({
                "email": email, "password": password, "email_confirm": True
            })
            user_id = user.user.id
            sb.table("profiles").upsert({
                "id": user_id, "username": username, "email": email
            }, on_conflict="id").execute()
            users.append({
                "id": user_id, "email": email, "password": password, "username": username
            })

        conv_res = sb.table("conversations").insert({
            "is_group": True, "name": f"Realtime Test Conv {suffix}",
            "created_by": users[0]["id"]
        }).execute()
        conversation_id = conv_res.data[0]["id"]

        for u in users:
            sb.table("conversation_participants").insert({
                "conversation_id": conversation_id, "user_id": u["id"]
            }).execute()

    test_data = {"user1": users[0], "user2": users[1], "conversation_id": conversation_id}
    os.makedirs("e2e/test-data", exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(test_data, f, indent=2)

    assert os.path.exists(DATA_FILE)


def test_realtime_cleanup():
    """test_realtime_setup'ın oluşturduğu conversation + iki kullanıcıyı siler,
    JSON'u kaldırır. Playwright testi BİTTİKTEN SONRA, ayrı bir pytest
    çağrısıyla çalıştırılmalı (bkz. modül docstring'i)."""
    if not os.path.exists(DATA_FILE):
        return  # setup hiç çalışmamış / zaten temizlenmiş

    with open(DATA_FILE) as f:
        test_data = json.load(f)

    app = _make_app()
    with app.app_context():
        sb = get_sb()
        conversation_id = test_data["conversation_id"]
        try:
            sb.table("conversation_participants").delete().eq(
                "conversation_id", conversation_id).execute()
            sb.table("conversations").delete().eq("id", conversation_id).execute()
        except Exception:
            pass
        for key in ("user1", "user2"):
            user_id = test_data[key]["id"]
            try:
                sb.auth.admin.delete_user(user_id)
            except Exception:
                pass
            try:
                sb.table("profiles").delete().eq("id", user_id).execute()
            except Exception:
                pass

    os.remove(DATA_FILE)
