"""Yorum/yanıt ekleme testleri — özellikle bildirim yazmalarının arkaplana
alınmasından sonra yanıtın hâlâ doğru döndüğünü ve bildirimin (asenkron da
olsa) hâlâ oluştuğunu doğrular.

Kullanıcı raporu: yavaş/kesintili mobil bağlantıda add_comment()/reply_comment()
yanıt dönmeden önce yaptığı 4-5 ardışık Supabase turu (insert + post/parent
sorgusu + notify + notify_mentions + profil) yüzünden istemci fetch'i
"başarısız" sanıyordu ama yorum aslında ekleniyordu (F5'te görünüyordu).
Bildirimler (notify/notify_mentions) arkaplan thread pool'a taşındı
(bkz. app/social.py _notify_pool) — bu test hem yanıtın hem gecikmeli
bildirimin doğru çalıştığını kanıtlar.
"""
import time

from app.supabase_client import get_sb


class TestComments:
    def test_add_comment_returns_fast_and_notifies_owner_async(self, app, client, logged_in_session):
        """Post sahibine yorum atılınca: yanıt doğru döner, yorum DB'ye yazılır,
        bildirim (arkaplanda) post sahibine gider."""
        # logged_in_session her çağrıda AYNI client'ın session'ını üzerine
        # yazar — bu yüzden en SON çağrılan kullanıcı (commenter) isteği
        # gerçekten yapan kullanıcı olur, owner sadece post/bildirim hedefi.
        owner, _ = logged_in_session(
            email="cmt_owner@example.com", password="TestPass123!"
        )
        commenter, session_client = logged_in_session(
            email="cmt_commenter@example.com", password="TestPass123!"
        )

        with app.app_context():
            sb = get_sb()
            post_res = sb.table("posts").insert({
                "user_id": owner["id"], "content": "test post", "visibility": "public"
            }).execute()
            post_id = post_res.data[0]["id"]

        try:
            resp = session_client.post(
                f"/social/comment/{post_id}",
                data={"content": "harika bir yorum", "csrf_token": "test-csrf-token"},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": "test-csrf-token"},
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["content"] == "harika bir yorum"
            assert body["username"] == commenter["username"]
            comment_id = body["id"]

            with app.app_context():
                sb = get_sb()
                saved = sb.table("comments").select("*").eq("id", comment_id).execute().data
                assert len(saved) == 1
                assert saved[0]["content"] == "harika bir yorum"

                # Bildirim arkaplan thread pool'da yazılıyor — kısa bir
                # bekleme ile tamamlandığını doğrula.
                deadline = time.time() + 5
                notif = []
                while time.time() < deadline and not notif:
                    notif = sb.table("notifications").select("*").eq(
                        "recipient_id", owner["id"]
                    ).eq("comment_id", comment_id).execute().data
                    if not notif:
                        time.sleep(0.2)
                assert notif, "Post sahibine yorum bildirimi (arkaplanda) oluşmadı"
                assert notif[0]["type"] == "comment"
        finally:
            with app.app_context():
                sb = get_sb()
                sb.table("comments").delete().eq("post_id", post_id).execute()
                sb.table("notifications").delete().eq("post_id", post_id).execute()
                sb.table("posts").delete().eq("id", post_id).execute()

    def test_reply_comment_returns_fast_and_notifies_parent_author_async(self, app, client, logged_in_session):
        """Bir yoruma yanıt atılınca: yanıt doğru döner, yanıt DB'ye yazılır,
        bildirim (arkaplanda) yorumun sahibine gider."""
        with app.app_context():
            sb = get_sb()
            owner, _ = logged_in_session(
                email="reply_owner@example.com", password="TestPass123!"
            )
        parent_author, _ = logged_in_session(
            email="reply_parent_author@example.com", password="TestPass123!"
        )
        replier, session_client = logged_in_session(
            email="reply_replier@example.com", password="TestPass123!"
        )

        with app.app_context():
            sb = get_sb()
            post_res = sb.table("posts").insert({
                "user_id": owner["id"], "content": "test post", "visibility": "public"
            }).execute()
            post_id = post_res.data[0]["id"]
            parent_res = sb.table("comments").insert({
                "post_id": post_id, "user_id": parent_author["id"], "content": "ilk yorum"
            }).execute()
            parent_id = parent_res.data[0]["id"]

        try:
            resp = session_client.post(
                f"/social/comment/{post_id}/reply/{parent_id}",
                data={"content": "buna yanıt", "csrf_token": "test-csrf-token"},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": "test-csrf-token"},
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["content"] == "buna yanıt"
            assert body["parent_id"] == parent_id
            reply_id = body["id"]

            with app.app_context():
                sb = get_sb()
                saved = sb.table("comments").select("*").eq("id", reply_id).execute().data
                assert len(saved) == 1

                deadline = time.time() + 5
                notif = []
                while time.time() < deadline and not notif:
                    notif = sb.table("notifications").select("*").eq(
                        "recipient_id", parent_author["id"]
                    ).eq("comment_id", reply_id).execute().data
                    if not notif:
                        time.sleep(0.2)
                assert notif, "Yorum sahibine yanıt bildirimi (arkaplanda) oluşmadı"
                assert notif[0]["type"] == "reply"
        finally:
            with app.app_context():
                sb = get_sb()
                sb.table("comments").delete().eq("post_id", post_id).execute()
                sb.table("notifications").delete().eq("post_id", post_id).execute()
                sb.table("posts").delete().eq("id", post_id).execute()


class TestCommentIdempotency:
    """Kök neden: add_comment()/reply_comment() içindeki insert bir bağlantı
    hatası sonrası retry_on_connection_error tarafından (veya insert'in kendi
    şema-fallback except'i tarafından) İKİ KEZ çalıştırılabiliyordu — sunucu
    ilk denemede insert'i commit edip yanıtı dönerken bağlantı koparsa,
    istemci hata sanıyordu ama olası bir retry aynı yorumu tekrar eklerdi.
    _find_recent_duplicate_comment() bunu önler: aynı kullanıcının aynı
    post'a (yanıtsa aynı parent'a) çok yakın zamanda attığı birebir aynı
    içerikli bir yorum/yanıt varsa, yeni satır eklemek yerine mevcut olanı
    döndürür (aynı comment id, DB'de tek satır)."""

    def test_duplicate_comment_submission_returns_same_id_no_extra_row(self, app, client, logged_in_session):
        owner, _ = logged_in_session(email="dup_cmt_owner@example.com", password="TestPass123!")
        commenter, session_client = logged_in_session(email="dup_cmt_user@example.com", password="TestPass123!")

        with app.app_context():
            sb = get_sb()
            post_res = sb.table("posts").insert({
                "user_id": owner["id"], "content": "test post", "visibility": "public"
            }).execute()
            post_id = post_res.data[0]["id"]

        try:
            payload = {"content": "aynı yorum tekrar denemesi", "csrf_token": "test-csrf-token"}
            headers = {"X-Requested-With": "fetch", "X-CSRF-Token": "test-csrf-token"}

            r1 = session_client.post(f"/social/comment/{post_id}", data=payload, headers=headers)
            r2 = session_client.post(f"/social/comment/{post_id}", data=payload, headers=headers)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.get_json()["id"] == r2.get_json()["id"]

            with app.app_context():
                sb = get_sb()
                rows = sb.table("comments").select("*").eq("post_id", post_id).eq(
                    "content", "aynı yorum tekrar denemesi"
                ).execute().data
                assert len(rows) == 1, "Aynı içerik iki kez eklenmiş (idempotency guard çalışmadı)"
        finally:
            with app.app_context():
                sb = get_sb()
                sb.table("comments").delete().eq("post_id", post_id).execute()
                sb.table("notifications").delete().eq("post_id", post_id).execute()
                sb.table("posts").delete().eq("id", post_id).execute()

    def test_different_content_same_user_same_post_both_saved(self, app, client, logged_in_session):
        """Guard yanlışlıkla FARKLI içerikli ardışık yorumları da engellemiyor."""
        owner, _ = logged_in_session(email="dup_cmt_owner2@example.com", password="TestPass123!")
        commenter, session_client = logged_in_session(email="dup_cmt_user2@example.com", password="TestPass123!")

        with app.app_context():
            sb = get_sb()
            post_res = sb.table("posts").insert({
                "user_id": owner["id"], "content": "test post", "visibility": "public"
            }).execute()
            post_id = post_res.data[0]["id"]

        try:
            headers = {"X-Requested-With": "fetch", "X-CSRF-Token": "test-csrf-token"}
            r1 = session_client.post(f"/social/comment/{post_id}",
                                      data={"content": "birinci yorum", "csrf_token": "test-csrf-token"},
                                      headers=headers)
            r2 = session_client.post(f"/social/comment/{post_id}",
                                      data={"content": "ikinci yorum", "csrf_token": "test-csrf-token"},
                                      headers=headers)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.get_json()["id"] != r2.get_json()["id"]

            with app.app_context():
                sb = get_sb()
                rows = sb.table("comments").select("*").eq("post_id", post_id).execute().data
                assert len(rows) == 2
        finally:
            with app.app_context():
                sb = get_sb()
                sb.table("comments").delete().eq("post_id", post_id).execute()
                sb.table("notifications").delete().eq("post_id", post_id).execute()
                sb.table("posts").delete().eq("id", post_id).execute()
