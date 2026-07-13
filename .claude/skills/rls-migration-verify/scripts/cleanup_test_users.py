"""make_test_users.py ile oluşturulan test kullanıcılarını (ve varsa ortak
sohbeti) siler. Aynı --prefix ile çağır.

Kullanım:
    python cleanup_test_users.py --prefix msgRls --conversation-id 861af750-8c4e-4225-aea4-8d109b7933c5
    python cleanup_test_users.py --prefix callTest
"""
import argparse
import sys

sys.path.insert(0, r"C:\Users\Artemis\Desktop\sosyal-medya")
from app import create_app
from app.supabase_client import get_sb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--conversation-id", default=None)
    args = ap.parse_args()
    p = args.prefix

    app = create_app()
    with app.app_context():
        sb = get_sb()
        if args.conversation_id:
            conv_id = args.conversation_id
            sb.table("messages").delete().eq("conversation_id", conv_id).execute()
            sb.table("conversation_participants").delete().eq("conversation_id", conv_id).execute()
            sb.table("conversations").delete().eq("id", conv_id).execute()

        for suffix in ("A", "B", "C"):
            username = f"{p}{suffix}"
            prof = sb.table("profiles").select("id").eq("username", username).execute().data
            if not prof:
                continue
            uid = prof[0]["id"]
            sb.table("profiles").delete().eq("id", uid).execute()
            try:
                sb.auth.admin.delete_user(uid)
            except Exception as e:
                print("auth delete warn:", username, e)
        print("cleanup done")


if __name__ == "__main__":
    main()
