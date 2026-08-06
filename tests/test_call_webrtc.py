"""1:1 WebRTC arama testi — iki gerçek kullanıcı arasında sesli/görüntülü arama.

AYNI iki-ayrı-pytest-süreci deseni test_realtime_broadcast.py'de (bkz. o
dosyanın modül docstring'i — Playwright ayrı bir süreç olarak araya girdiği
için normal fixture yield-teardown KULLANILAMAZ). Fark: burada GRUP değil,
1:1 bir conversation gerekiyor (call.js'in arama butonları sadece 1:1'de
render edilir, bkz. app/templates/messages/_conversation_panel.html) — bu
yüzden test_realtime_broadcast.py'nin (is_group=True) verisi yeniden
KULLANILAMIYOR, ayrı bir JSON dosyası + kurulum/temizlik çifti var.

Doğru akış (bkz. package.json test:e2e:call script'i):
    1. python -m pytest tests/test_call_webrtc.py::test_call_setup
    2. npx playwright test e2e/call-webrtc.spec.js
    3. python -m pytest tests/test_call_webrtc.py::test_call_cleanup
"""
import json
import os

from app import create_app
from app.supabase_client import get_sb

DATA_FILE = "e2e/test-data/call-users.json"


def _make_app():
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_call_setup():
    """İki gerçek test kullanıcısı + paylaşılan 1:1 conversation'ı oluşturur
    (app/messaging/_common.py::_get_or_create_1to1 ile AYNI şema — boş
    conversations insert + is_admin'siz 2 participant satırı), Playwright'ın
    okuyacağı JSON'a export eder. BİLEREK teardown YOK."""
    app = _make_app()
    with app.app_context():
        sb = get_sb()

        suffix = os.urandom(4).hex()
        users = []
        for i in (1, 2):
            email = f"call-user{i}-{os.urandom(8).hex()}@test.local"
            password = "Test_Pass_123!"
            username = f"calluser{i}_{suffix}"
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

        conv_res = sb.table("conversations").insert({}).execute()
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


def test_call_cleanup():
    """test_call_setup'ın oluşturduğu conversation + iki kullanıcıyı siler,
    JSON'u kaldırır. Playwright testi BİTTİKTEN SONRA çalıştırılmalı."""
    if not os.path.exists(DATA_FILE):
        return

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
