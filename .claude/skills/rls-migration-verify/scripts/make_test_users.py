"""RLS/realtime izole testi için tek seferlik test kullanıcıları + (opsiyonel)
ortak konuşma oluşturur. Şablon niteliğindedir — genellikle 2 katılımcı (A, B
aynı sohbette) + 1 alakasız (C) deseni kullanılır (bkz. rls-migration-verify
SKILL.md). Her çağrıda farklı bir PREFIX kullan ki eski test kalıntılarıyla
karışmasın.

Kullanım:
    python make_test_users.py --prefix msgRls --conversation
    python make_test_users.py --prefix callTest          # sohbetsiz, sadece kullanıcılar
"""
import argparse
import re
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:5000"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True, help="örn. msgRls -> msgRlsA/B/C")
    ap.add_argument("--conversation", action="store_true",
                     help="A ve B'yi aynı 1:1 sohbete katılımcı olarak ekler")
    args = ap.parse_args()
    p = args.prefix

    users = [
        (f"{p}A", f"{p}A@sosyal-test.local", f"{p}A123!x"),
        (f"{p}B", f"{p}B@sosyal-test.local", f"{p}B123!x"),
        (f"{p}C", f"{p}C@sosyal-test.local", f"{p}C123!x"),
    ]

    for username, email, password in users:
        s = requests.Session()
        r = s.get(f"{BASE}/register")
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text).group(1)
        r = s.post(f"{BASE}/register", data={
            "csrf_token": csrf, "username": username, "email": email, "password": password,
        }, allow_redirects=True)
        print(username, "register:", r.status_code)

    sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya")
    from app import create_app
    from app.supabase_client import get_sb
    app = create_app()
    with app.app_context():
        sb = get_sb()
        ids = {}
        for username, _, _ in users:
            prof = sb.table("profiles").select("id").eq("username", username).execute().data[0]
            ids[username] = prof["id"]
        print("ids:", ids)

        if args.conversation:
            conv = sb.table("conversations").insert({"is_group": False, "name": None}).execute()
            conv_id = conv.data[0]["id"]
            for uname in (users[0][0], users[1][0]):
                sb.table("conversation_participants").insert({
                    "conversation_id": conv_id, "user_id": ids[uname], "is_admin": False,
                }).execute()
            print("conv_id:", conv_id)


if __name__ == "__main__":
    main()
