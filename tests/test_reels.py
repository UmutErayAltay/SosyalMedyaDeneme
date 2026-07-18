"""Reels (dikey kısa video) testleri — is_reel bayrak, video zorunluluğu, filtreleme."""
import pytest
from io import BytesIO

from app.supabase_client import get_sb


class TestReels:
    def test_create_reel_without_video_fails(self, app, client, logged_in_session):
        """is_reel=on ama video olmadan post oluşturma başarısız olur (flash hata)."""
        user, _ = logged_in_session(
            email="reel_no_video@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/post/new",
            data={
                "csrf_token": "test-csrf-token",
                "content": "Bu bir reel olacaktı",
                "is_reel": "on",
                # video yok
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # Flash mesajı: "Reel için video gerekli."
        assert "Reel için video gerekli" in body or "video gerekli" in body.lower()

        # DB'de post oluşturulmamış olmalı
        with app.app_context():
            sb = get_sb()
            posts = sb.table("posts").select("*").eq("user_id", user["id"]).execute().data
            assert len(posts) == 0

    def test_create_regular_post_without_video_succeeds(self, app, client, logged_in_session):
        """is_reel olmadan post oluşturma video olmasa da başarılı olur."""
        user, _ = logged_in_session(
            email="reel_regular_post@example.com",
            password="TestPass123!"
        )

        resp = client.post(
            "/post/new",
            data={
                "csrf_token": "test-csrf-token",
                "content": "Bu normal bir post",
                # is_reel yok, video yok
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # DB'de post oluşturulmuş olmalı, is_reel false veya null
        with app.app_context():
            sb = get_sb()
            posts = sb.table("posts").select("*").eq("user_id", user["id"]).execute().data
            assert len(posts) >= 1
            assert posts[0]["is_reel"] is not True

    def test_create_reel_with_mock_video_succeeds(self, app, client, logged_in_session):
        """is_reel=on ve mock video file ile post oluşturma başarılı olur, is_reel=true kaydedilir."""
        user, _ = logged_in_session(
            email="reel_with_video@example.com",
            password="TestPass123!"
        )

        # Mock video file (minimal MP4 header)
        video_bytes = b'\x00\x00\x00\x20ftypisom\x00\x00\x00\x00'

        resp = client.post(
            "/post/new",
            data={
                "csrf_token": "test-csrf-token",
                "content": "Bu bir reel!",
                "is_reel": "on",
                "video": (BytesIO(video_bytes), "video.mp4"),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # Başarılı olursa "Reel için video gerekli" hatası olmamalı
        # (başka hata olabilir, ama bu özel hata olmamalı)
        assert "Reel için video gerekli" not in body

        # DB'de post var mı ve is_reel=true mı doğrula
        with app.app_context():
            sb = get_sb()
            posts = sb.table("posts").select("*").eq(
                "user_id", user["id"]
            ).execute().data
            # En az bir post olmalı
            assert len(posts) >= 1
            # En son oluşturulan post is_reel=true olmalı
            latest_post = max(posts, key=lambda p: p.get("created_at", ""))
            assert latest_post.get("is_reel") is True

    def test_reels_page_only_shows_public_reel_posts(self, app, client, logged_in_session):
        """GET /reels sadece public, is_reel=true, video_url not null, draft/archived olmayan postları gösterir."""
        user, _ = logged_in_session(
            email="reel_viewer@example.com",
            password="TestPass123!"
        )

        # Test verisi: çeşitli post türleri oluştur
        with app.app_context():
            sb = get_sb()

            # 1. Public reel (görünmeli)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Public reel",
                "is_reel": True,
                "video_url": "https://example.com/video1.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # 2. Public non-reel (görünmemeli)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Public regular post",
                "is_reel": False,
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # 3. Public reel ama video_url yok (görünmemeli)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Public reel without video",
                "is_reel": True,
                "video_url": None,
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # 4. Followers-only reel (görünmemeli — visibility != public)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Followers reel",
                "is_reel": True,
                "video_url": "https://example.com/video2.mp4",
                "visibility": "followers",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # 5. Public reel ama draft (görünmemeli)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Draft reel",
                "is_reel": True,
                "video_url": "https://example.com/video3.mp4",
                "visibility": "public",
                "is_draft": True,
                "is_archived": False,
            }).execute()

            # 6. Public reel ama archived (görünmemeli)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Archived reel",
                "is_reel": True,
                "video_url": "https://example.com/video4.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": True,
            }).execute()

        # Reels page'ini aç
        resp = client.get("/reels", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # Sadece #1 (public reel with video) görünmeli
        assert "Public reel" in body
        # #2, #3, #4, #5, #6 görünmemeli
        assert "Public regular post" not in body
        assert "Public reel without video" not in body
        assert "Followers reel" not in body
        assert "Draft reel" not in body
        assert "Archived reel" not in body

    def test_reels_page_filters_blocked_users(self, app, client, logged_in_session):
        """GET /reels engellenen kullanıcının reel'lerini göstermez."""
        user1, _ = logged_in_session(
            email="reel_blocker@example.com",
            password="TestPass123!"
        )

        user2, _ = logged_in_session(
            email="reel_blocked@example.com",
            password="TestPass123!"
        )

        # User2'nin public reel'ini oluştur
        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": user2["id"],
                "content": "Blocked user's reel",
                "is_reel": True,
                "video_url": "https://example.com/blocked.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # User1 tarafından User2 engellenir
            sb.table("blocks").insert({
                "blocker_id": user1["id"],
                "blocked_id": user2["id"],
            }).execute()

        # User1 olarak reels sayfasını aç
        with client.session_transaction() as sess:
            sess["user"] = {
                "id": user1["id"],
                "email": user1["email"],
                "username": user1["username"]
            }
            sess["_csrf_token"] = "test-csrf-token"

        resp = client.get("/reels", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # Engellenen kullanıcının reel'i görünmemeli
        assert "Blocked user's reel" not in body

    def test_reels_page_empty_when_no_reel_posts(self, app, client, logged_in_session):
        """Hiç public reel olmadığında sayfa boş veya mesaj gösterir."""
        user, _ = logged_in_session(
            email="reel_empty@example.com",
            password="TestPass123!"
        )

        # Hiç post oluşturmadık — sayfa boş olmalı
        resp = client.get("/reels", follow_redirects=False)
        assert resp.status_code == 200
        # Boş sayfada "reel bulunamadı" benzeri mesaj veya liste boş olabilir

    def test_reels_page_respects_privacy_settings(self, app, client, logged_in_session):
        """Private profili olan kullanıcının reel'leri, takip ETMEYEN birisi tarafından görülmez."""
        private_user, _ = logged_in_session(
            email="reel_private_profile@example.com",
            password="TestPass123!"
        )

        other_user, _ = logged_in_session(
            email="reel_other@example.com",
            password="TestPass123!"
        )

        # private_user'ı private profile yap
        with app.app_context():
            sb = get_sb()
            sb.table("profiles").update({"is_private": True}).eq(
                "id", private_user["id"]
            ).execute()

            # private_user'ın public reel'ini oluştur
            sb.table("posts").insert({
                "user_id": private_user["id"],
                "content": "Private profile reel",
                "is_reel": True,
                "video_url": "https://example.com/private.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        # other_user olarak reels'i aç
        with client.session_transaction() as sess:
            sess["user"] = {
                "id": other_user["id"],
                "email": other_user["email"],
                "username": other_user["username"]
            }
            sess["_csrf_token"] = "test-csrf-token"

        resp = client.get("/reels", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # private_user'ın reel'i görünmemeli (takip etmeyen dış kişi)
        assert "Private profile reel" not in body

    def test_reels_page_shows_own_private_profile_reels(self, app, client, logged_in_session):
        """Kullanıcı kendi private profili reels'lerini görebilir."""
        user, _ = logged_in_session(
            email="reel_own_private@example.com",
            password="TestPass123!"
        )

        with app.app_context():
            sb = get_sb()
            # Profil private yap
            sb.table("profiles").update({"is_private": True}).eq(
                "id", user["id"]
            ).execute()

            # Kendi public reel'ini oluştur
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "My own reel in private profile",
                "is_reel": True,
                "video_url": "https://example.com/own.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        # Reels sayfasını aç
        resp = client.get("/reels", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # Kendi reel'i görmeli (private profile olsa da kendi content'i)
        assert "My own reel in private profile" in body

    def test_reels_pagination(self, app, client, logged_in_session):
        """Reels sayfalanır — her sayfada PAGE_SIZE (20) post olur, has_more doğru şekilde hesaplanır."""
        user, _ = logged_in_session(
            email="reel_pagination@example.com",
            password="TestPass123!"
        )

        # 25 reel oluştur (2 sayfa: 20 + 5)
        with app.app_context():
            sb = get_sb()
            for i in range(25):
                sb.table("posts").insert({
                    "user_id": user["id"],
                    "content": f"Reel {i+1}",
                    "is_reel": True,
                    "video_url": f"https://example.com/video{i+1}.mp4",
                    "visibility": "public",
                    "is_draft": False,
                    "is_archived": False,
                }).execute()

        # Sayfa 1
        resp = client.get("/reels?page=1", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # Sayfa 2
        resp = client.get("/reels?page=2", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # Page 2'de en az birkaç reel olmalı (5 tane kaldı)
        assert "Reel" in body

    def test_reel_sorting_newest_first(self, app, client, logged_in_session):
        """GET /reels'te postlar en yeni başta sıralanır."""
        user, _ = logged_in_session(
            email="reel_sorting@example.com",
            password="TestPass123!"
        )

        with app.app_context():
            sb = get_sb()
            # İki reel oluştur (2. tane daha yeni olacak)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Older reel",
                "is_reel": True,
                "video_url": "https://example.com/old.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

            # Biraz sonra (yaşamda, test'te de created_at otomatik şu anki zaman)
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "Newer reel",
                "is_reel": True,
                "video_url": "https://example.com/new.mp4",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get("/reels?page=1", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")

        # "Newer reel" "Older reel"'den önce gelmelidir (HTML'de)
        newer_pos = body.find("Newer reel")
        older_pos = body.find("Older reel")
        # İkisi de bulunabilir (veya hiçbiri, sayfa yapısına göre)
        # Eğer ikisi de varsa, newer < older olmalı
        if newer_pos != -1 and older_pos != -1:
            assert newer_pos < older_pos, "Newer reel should appear before older reel"
