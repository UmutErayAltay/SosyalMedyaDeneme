"""JSON REST API (Faz 1, native Android yol haritası) testleri.

Gerçek Supabase test kullanıcısıyla çalışır (mock yok, test_user_factory
fixture'ı bkz. tests/conftest.py) — auth/token akışı güvenlik-kritik.
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pyotp
import pytest

from app.supabase_client import get_sb


# app/notifications.py NOTIFICATION_TYPES ile AYNI 13 kolon — testte bağımsız
# bir kopya tutulur ki bu dosya notifications.py'nin private sabitine değil,
# api_v1.py'nin JSON sözleşmesine (preferences alan adları) bağımlı kalsın.
_NOTIF_PREF_COLUMNS = [
    "notify_like", "notify_comment", "notify_reply", "notify_comment_like",
    "notify_comment_reaction", "notify_follow", "notify_follow_request",
    "notify_follow_accept", "notify_message", "notify_mention",
    "notify_hashtag_post", "notify_story_reaction", "notify_repost",
]


def _api_login(client, email, password):
    """Ortak login yardımcısı — TestApiV1Login/Feed/Discover/Search gibi
    login akışının KENDİSİNİ doğrulayan testlerde kullanılır."""
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return resp.get_json()["token"]


def _api_token_for(app, user_id):
    """Login rate limitini (10 istek/300sn, IP başına — bkz. api_v1.py
    login() docstring'i: web login()'le AYNI anahtar paylaşılıyor) atlayarak
    doğrudan bir api_tokens satırı oluşturur.

    Profil testleri (TestApiV1Profile) çok sayıda kullanıcı için token
    gerektiriyor; hepsi gerçek /auth/login'i çağırsaydı aynı test IP'sinden
    (127.0.0.1) art arda 10'dan fazla login isteği rate limit'e takılıp
    KeyError('token') ile testleri sahte-başarısız gösterirdi (login akışının
    kendisi zaten TestApiV1Login'de ayrıca test ediliyor, burada tekrar
    gerekmiyor)."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with app.app_context():
        get_sb().table("api_tokens").insert({
            "user_id": user_id,
            "token_hash": token_hash,
        }).execute()
    return raw_token


class TestApiV1Login:
    def test_login_correct_credentials_returns_token_and_me_works(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_login_ok@example.com", password="TestPass123!")

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("token")
        assert body["user"]["id"] == user["id"]
        assert body["user"]["username"] == user["username"]

        token = body["token"]
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        me_body = me_resp.get_json()
        assert me_body["user"]["id"] == user["id"]

        # Realtime oturumu — login() bu GERÇEK çağrı sırasında _store_realtime_
        # session()'ı ZATEN tetikledi (REALTIME_TOKEN_ENCRYPTION_KEY .env'de
        # mevcut varsayılıyor). YENİ bir gerçek login çağrısı EKLEMEDEN, bu
        # testin ürettiği TEK login üzerinden hem DB hem endpoint doğrulanır
        # (login:{ip} bütçesine ek yük YOK).
        from app.realtime_session import _decrypt_token
        with app.app_context():
            row = get_sb().table("api_tokens").select(
                "sb_access_token_enc, sb_refresh_token_enc, sb_token_expires_at"
            ).eq("user_id", user["id"]).execute().data[0]
        assert row["sb_access_token_enc"]
        assert row["sb_refresh_token_enc"]
        assert row["sb_token_expires_at"]
        decrypted_access = _decrypt_token(row["sb_access_token_enc"])
        decrypted_refresh = _decrypt_token(row["sb_refresh_token_enc"])
        assert decrypted_access and decrypted_access.count(".") == 2  # JWT şekli
        assert decrypted_refresh

        rt_resp = client.get(
            "/api/v1/realtime-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rt_resp.status_code == 200
        rt_body = rt_resp.get_json()
        assert rt_body.get("access_token")
        assert rt_body.get("supabase_url")
        assert rt_body.get("supabase_publishable_key")

        # Temizlik: bu testin ürettiği api_tokens satırını sil
        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_login_wrong_password_returns_401(self, client, test_user_factory):
        user = test_user_factory(email="apiv1_login_wrongpw@example.com", password="TestPass123!")

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "WrongPassword123!"},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_credentials"


class TestApiV1Feed:
    def test_feed_without_token_returns_401(self, client):
        resp = client.get("/api/v1/feed")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_feed_with_valid_token_returns_posts_shape(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_feed_ok@example.com", password="TestPass123!")
        # Gerçek /auth/login DEĞİL: bu test login akışını değil feed()'i test
        # ediyor — dosyadaki TÜM sınıfların gerçek login çağırması IP başına
        # 10/300sn rate limit'i CI'de aşıp KeyError('token') ile sahte-başarısız
        # veriyordu (bkz. TestApiV1Profile'daki AYNI çözüm).
        token = _api_token_for(app, user["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": user["id"],
                "content": "api_v1 feed testi için post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/feed?limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "has_next" in body
        assert any(p.get("content") == "api_v1 feed testi için post" for p in body["posts"])
        # _attach_post_metrics/enrich_post_json sözleşmesindeki alanlar (RPC veya fallback fark etmez)
        post = next(p for p in body["posts"] if p.get("content") == "api_v1 feed testi için post")
        assert "like_count" in post and "comment_count" in post and "liked_by_me" in post

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("user_id", user["id"]).execute()


class TestApiV1Logout:
    def test_logout_revokes_token_and_csrf_exempt(self, app, client, test_user_factory):
        """Ayrıca CSRF muafiyetinin GERÇEKTEN işlediğini kanıtlar: bu POST
        isteği hiçbir csrf_token/X-CSRF-Token TAŞIMAZ (sadece Bearer header) —
        eğer muafiyet çalışmasaydı web CSRF middleware'i 400 dönerdi."""
        user = test_user_factory(email="apiv1_logout@example.com", password="TestPass123!")
        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape
        # yorumu (rate-limit birikimi önlemi); logout() token'ın NASIL üretildiğine
        # bakmaz, sadece hash'in api_tokens'ta var/aktif olmasına bakar.
        token = _api_token_for(app, user["id"])

        # CSRF token'ı OLMADAN POST — muafiyet çalışıyorsa 400 (CSRF) değil,
        # normal iş mantığı (ok=True) dönmeli.
        logout_resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.get_json().get("ok") is True

        # Aynı token artık geçersiz olmalı
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 401
        assert me_resp.get_json().get("error") == "unauthorized"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1Discover:
    def test_discover_without_token_returns_401(self, client):
        resp = client.get("/api/v1/discover")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_discover_with_valid_token_returns_posts_shape(self, app, client, test_user_factory):
        # Viewer + ayrı bir yazar hesabı: discover() kendi postlarını/takip
        # ettiklerini HARİÇ TUTAR (exclude_ids = followed_and_self_ids), bu
        # yüzden görünürlüğü doğrulamak için içerik BAŞKA bir hesaptan gelmeli.
        viewer = test_user_factory(email="apiv1_discover_viewer@example.com", password="TestPass123!")
        author = test_user_factory(email="apiv1_discover_author@example.com", password="TestPass123!")

        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        token = _api_token_for(app, viewer["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 discover testi için herkese açık post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/discover?page=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "has_more" in body
        assert body.get("page") == 1
        assert isinstance(body["posts"], list)
        assert any(p.get("content") == "api_v1 discover testi için herkese açık post" for p in body["posts"])
        post = next(p for p in body["posts"] if p.get("content") == "api_v1 discover testi için herkese açık post")
        # _attach_post_metrics/enrich_post_json sözleşmesindeki alanlar (RPC veya fallback fark etmez)
        assert "like_count" in post and "comment_count" in post and "liked_by_me" in post

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("user_id", author["id"]).execute()


class TestApiV1Search:
    def test_search_short_query_returns_empty_results_but_history_fields_present(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_short@example.com", password="TestPass123!")
        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        token = _api_token_for(app, user["id"])

        # q 2 karakterden kısa (tek harf) — asıl arama YAPILMAZ, sadece
        # recent/saved searches alanları döner (discovery.py search() ile aynı davranış).
        resp = client.get(
            "/api/v1/search?q=a",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["users"] == [] and body["posts"] == [] and body["hashtags"] == []
        assert "recent_searches" in body and "saved_searches" in body

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_search_without_token_returns_401(self, client):
        resp = client.get("/api/v1/search?q=deneme")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_search_type_users_filters_out_posts_and_hashtags(self, app, client, test_user_factory):
        searcher = test_user_factory(email="apiv1_search_searcher@example.com", password="TestPass123!")
        target = test_user_factory(
            email="apiv1_search_target@example.com", password="TestPass123!",
            username="apiv1uniqsearchtarget",
        )

        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        token = _api_token_for(app, searcher["id"])

        resp = client.get(
            "/api/v1/search?q=apiv1uniqsearchtarget&type=users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # type=users filtresi çalışıyor: sadece users doldurulmuş, posts/hashtags boş
        assert any(u.get("username") == "apiv1uniqsearchtarget" for u in body["users"])
        assert body["posts"] == [] and body["hashtags"] == []

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", searcher["id"]).execute()


class TestApiV1SearchSaveAndHistory:
    def test_save_search_then_delete_saved_search_roundtrip(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_save@example.com", password="TestPass123!")
        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        save_resp = client.post(
            "/api/v1/search/save",
            json={"q": "apiv1 kayıtlı arama testi", "label": "test etiketi"},
            headers=headers,
        )
        assert save_resp.status_code == 200
        assert save_resp.get_json().get("ok") is True

        with app.app_context():
            rows = get_sb().table("saved_searches").select("id").eq(
                "user_id", user["id"]
            ).eq("query", "apiv1 kayıtlı arama testi").execute().data
        assert rows, "saved_searches satırı bulunamadı"
        saved_id = rows[0]["id"]

        # Round-trip: kaydedilen aramayı sahiplik kontrolüyle sil
        delete_resp = client.post(
            f"/api/v1/search/saved/{saved_id}/delete",
            headers=headers,
        )
        assert delete_resp.status_code == 200
        assert delete_resp.get_json().get("ok") is True

        with app.app_context():
            remaining = get_sb().table("saved_searches").select("id").eq("id", saved_id).execute().data
        assert remaining == []

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_save_search_missing_query_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_save_missing@example.com", password="TestPass123!")
        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/search/save",
            json={"q": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "missing_query"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_clear_search_history_and_delete_history_item(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_search_history@example.com", password="TestPass123!")
        # Gerçek login DEĞİL — bkz. test_feed_with_valid_token_returns_posts_shape yorumu
        # (bu testin kendisi CI'de KeyError('token') ile başarısız olan test'ti —
        # dosyadaki gerçek login çağrılarının BİRİKİMİ rate limit'i aşmıştı).
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        # search_history'ye kayıt oluşturmak için gerçek bir arama at (search() endpoint'i
        # her başarılı q>=2 aramasında search_history'ye yazar).
        client.get("/api/v1/search?q=apiv1geçmiştest", headers=headers)

        with app.app_context():
            rows = get_sb().table("search_history").select("id").eq(
                "user_id", user["id"]
            ).eq("query", "apiv1geçmiştest").execute().data
        assert rows, "search_history satırı bulunamadı"
        history_id = rows[0]["id"]

        delete_resp = client.post(
            f"/api/v1/search/history/{history_id}/delete",
            headers=headers,
        )
        assert delete_resp.status_code == 200
        assert delete_resp.get_json().get("ok") is True

        with app.app_context():
            remaining = get_sb().table("search_history").select("id").eq("id", history_id).execute().data
        assert remaining == []

        clear_resp = client.post("/api/v1/search/history/clear", headers=headers)
        assert clear_resp.status_code == 200
        assert clear_resp.get_json().get("ok") is True

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()
            get_sb().table("search_history").delete().eq("user_id", user["id"]).execute()


class TestApiV1Profile:
    """Faz 3 profil ekranı — profile.py profile()/insights() ve social.py
    toggle_follow()/follow-requests mantığının JSON API karşılığı.
    """

    def test_own_profile_is_self_true_and_stats_correct(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_profile_self@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": user["id"], "content": "apiv1 kendi profilim testi",
                "visibility": "public", "is_draft": False, "is_archived": False,
            }).execute()

        resp = client.get(
            f"/api/v1/profile/{user['username']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["is_self"] is True
        assert body["deactivated"] is False
        assert body["profile"]["username"] == user["username"]
        # is_self olduğu için email JSON'da mevcut olmalı (kendi hesabı)
        assert body["profile"].get("email") == user["email"]
        assert body["stats"]["posts"] == 1
        assert any(p.get("content") == "apiv1 kendi profilim testi" for p in body["posts"])

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("user_id", user["id"]).execute()

    def test_other_users_public_profile_hides_email(self, app, client, test_user_factory):
        viewer = test_user_factory(email="apiv1_profile_viewer@example.com", password="TestPass123!")
        author = test_user_factory(email="apiv1_profile_author@example.com", password="TestPass123!")
        token = _api_token_for(app, viewer["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": author["id"], "content": "apiv1 başkasının herkese açık profili testi",
                "visibility": "public", "is_draft": False, "is_archived": False,
            }).execute()

        resp = client.get(
            f"/api/v1/profile/{author['username']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["is_self"] is False
        assert body["profile"]["username"] == author["username"]
        # Güvenlik: başka bir kullanıcının email'i JSON'a asla sızmamalı
        assert "email" not in body["profile"]
        assert any(p.get("content") == "apiv1 başkasının herkese açık profili testi" for p in body["posts"])

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("user_id", author["id"]).execute()

    def test_private_profile_non_follower_sees_empty_posts_but_profile_visible(self, app, client, test_user_factory):
        viewer = test_user_factory(email="apiv1_profile_priv_viewer@example.com", password="TestPass123!")
        owner = test_user_factory(email="apiv1_profile_priv_owner@example.com", password="TestPass123!")
        token = _api_token_for(app, viewer["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("profiles").update({"is_private": True}).eq("id", owner["id"]).execute()
            sb.table("posts").insert({
                "user_id": owner["id"], "content": "apiv1 gizli profil postu",
                "visibility": "public", "is_draft": False, "is_archived": False,
            }).execute()

        resp = client.get(
            f"/api/v1/profile/{owner['username']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["is_private"] is True
        assert body["is_following"] is False
        assert body["posts"] == []
        # Profil bilgisi (username vs) yine de görünür — sadece postlar gizli
        assert body["profile"]["username"] == owner["username"]

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("user_id", owner["id"]).execute()

    def test_toggle_follow_then_unfollow_public_profile(self, app, client, test_user_factory):
        follower = test_user_factory(email="apiv1_follow_follower@example.com", password="TestPass123!")
        target = test_user_factory(email="apiv1_follow_target@example.com", password="TestPass123!")
        token = _api_token_for(app, follower["id"])
        headers = {"Authorization": f"Bearer {token}"}

        follow_resp = client.post(f"/api/v1/profile/{target['username']}/follow", headers=headers)
        assert follow_resp.status_code == 200
        follow_body = follow_resp.get_json()
        assert follow_body["following"] is True
        assert follow_body["is_pending"] is False
        assert follow_body["followers_count"] == 1

        # Profilde is_following=true görünmeli
        prof_resp = client.get(f"/api/v1/profile/{target['username']}", headers=headers)
        assert prof_resp.get_json()["is_following"] is True

        # Tekrar çağırınca unfollow olur
        unfollow_resp = client.post(f"/api/v1/profile/{target['username']}/follow", headers=headers)
        assert unfollow_resp.status_code == 200
        unfollow_body = unfollow_resp.get_json()
        assert unfollow_body["following"] is False
        assert unfollow_body["followers_count"] == 0

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", follower["id"]).execute()

    def test_follow_request_to_private_profile_then_accept(self, app, client, test_user_factory):
        follower = test_user_factory(email="apiv1_freq_follower@example.com", password="TestPass123!")
        owner = test_user_factory(email="apiv1_freq_owner@example.com", password="TestPass123!")

        with app.app_context():
            get_sb().table("profiles").update({"is_private": True}).eq("id", owner["id"]).execute()

        follower_token = _api_token_for(app, follower["id"])
        follower_headers = {"Authorization": f"Bearer {follower_token}"}

        follow_resp = client.post(f"/api/v1/profile/{owner['username']}/follow", headers=follower_headers)
        assert follow_resp.status_code == 200
        follow_body = follow_resp.get_json()
        assert follow_body["following"] is False
        assert follow_body["is_pending"] is True

        # Hedef kullanıcı /follow-requests'te bu isteği görüyor
        owner_token = _api_token_for(app, owner["id"])
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        list_resp = client.get("/api/v1/follow-requests", headers=owner_headers)
        assert list_resp.status_code == 200
        assert any(u["username"] == follower["username"] for u in list_resp.get_json()["users"])

        # Kabul et
        accept_resp = client.post(
            f"/api/v1/follow-requests/{follower['id']}/accept", headers=owner_headers
        )
        assert accept_resp.status_code == 200
        assert accept_resp.get_json()["ok"] is True

        # Artık takip ediyor görünmeli
        prof_resp = client.get(f"/api/v1/profile/{owner['username']}", headers=follower_headers)
        assert prof_resp.get_json()["is_following"] is True

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", follower["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", owner["id"]).execute()

    def test_accepting_someone_elses_follow_request_returns_404(self, app, client, test_user_factory):
        follower = test_user_factory(email="apiv1_freq_enum_follower@example.com", password="TestPass123!")
        real_owner = test_user_factory(email="apiv1_freq_enum_owner@example.com", password="TestPass123!")
        unrelated = test_user_factory(email="apiv1_freq_enum_unrelated@example.com", password="TestPass123!")

        with app.app_context():
            get_sb().table("profiles").update({"is_private": True}).eq("id", real_owner["id"]).execute()

        follower_token = _api_token_for(app, follower["id"])
        client.post(
            f"/api/v1/profile/{real_owner['username']}/follow",
            headers={"Authorization": f"Bearer {follower_token}"},
        )

        # unrelated, follower'ın gerçek alıcısı DEĞİL — accept/reject 404 vermeli
        unrelated_token = _api_token_for(app, unrelated["id"])
        unrelated_headers = {"Authorization": f"Bearer {unrelated_token}"}

        accept_resp = client.post(
            f"/api/v1/follow-requests/{follower['id']}/accept", headers=unrelated_headers
        )
        assert accept_resp.status_code == 404

        reject_resp = client.post(
            f"/api/v1/follow-requests/{follower['id']}/reject", headers=unrelated_headers
        )
        assert reject_resp.status_code == 404

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", follower["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", unrelated["id"]).execute()

    def test_followers_and_following_list_endpoints(self, app, client, test_user_factory):
        follower = test_user_factory(email="apiv1_flist_follower@example.com", password="TestPass123!")
        target = test_user_factory(email="apiv1_flist_target@example.com", password="TestPass123!")
        token = _api_token_for(app, follower["id"])
        headers = {"Authorization": f"Bearer {token}"}

        client.post(f"/api/v1/profile/{target['username']}/follow", headers=headers)

        followers_resp = client.get(f"/api/v1/profile/{target['username']}/followers", headers=headers)
        assert followers_resp.status_code == 200
        followers_body = followers_resp.get_json()
        assert "users" in followers_body and "title" in followers_body
        assert any(u["username"] == follower["username"] for u in followers_body["users"])

        following_resp = client.get(f"/api/v1/profile/{follower['username']}/following", headers=headers)
        assert following_resp.status_code == 200
        following_body = following_resp.get_json()
        assert any(u["username"] == target["username"] for u in following_body["users"])

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", follower["id"]).execute()

    def test_insights_matches_own_data(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_insights@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": user["id"], "content": "apiv1 insights testi",
                "visibility": "public", "is_draft": False, "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/profile/insights?days=7",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["days"] == 7
        assert body["total_posts"] == 1
        assert len(body["likes_by_day"]) == 7
        assert len(body["day_of_week_stats"]) == 7
        assert any(p.get("content") == "apiv1 insights testi" for p in body["top_posts"])

        # Geçersiz days sessizce 14'e düşer
        resp_invalid = client.get(
            "/api/v1/profile/insights?days=999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_invalid.get_json()["days"] == 14

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("user_id", user["id"]).execute()

    def test_profile_not_found_and_deactivated_profile(self, app, client, test_user_factory):
        viewer = test_user_factory(email="apiv1_profile_404@example.com", password="TestPass123!")
        deactivated_user = test_user_factory(
            email="apiv1_profile_deact@example.com", password="TestPass123!"
        )
        token = _api_token_for(app, viewer["id"])
        headers = {"Authorization": f"Bearer {token}"}

        not_found_resp = client.get("/api/v1/profile/apiv1-bu-kullanici-yok", headers=headers)
        assert not_found_resp.status_code == 404

        with app.app_context():
            get_sb().table("profiles").update({"is_deactivated": True}).eq(
                "id", deactivated_user["id"]
            ).execute()

        deact_resp = client.get(f"/api/v1/profile/{deactivated_user['username']}", headers=headers)
        assert deact_resp.status_code == 200
        deact_body = deact_resp.get_json()
        assert deact_body["deactivated"] is True
        assert deact_body["posts"] == []

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", viewer["id"]).execute()


class TestApiV1Reels:
    def test_reels_without_token_returns_401(self, client):
        resp = client.get("/api/v1/reels")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_reels_only_returns_video_reel_posts_not_normal_posts(self, app, client, test_user_factory):
        # reels() sadece is_reel=true + video_url dolu, herkese açık postları
        # döner — normal (video'suz/is_reel=false) bir post ASLA görünmemeli.
        viewer = test_user_factory(email="apiv1_reels_viewer@example.com", password="TestPass123!")
        author = test_user_factory(email="apiv1_reels_author@example.com", password="TestPass123!")
        token = _api_token_for(app, viewer["id"])

        with app.app_context():
            sb = get_sb()
            sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 reels testi — gerçek reel",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
                "is_reel": True,
                "video_url": "https://example.com/test-reel.mp4",
            }).execute()
            sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 reels testi — normal post (reels'te görünmemeli)",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute()

        resp = client.get(
            "/api/v1/reels?page=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "has_more" in body and body.get("page") == 1
        contents = [p.get("content") for p in body["posts"]]
        assert "api_v1 reels testi — gerçek reel" in contents
        assert "api_v1 reels testi — normal post (reels'te görünmemeli)" not in contents
        reel_post = next(p for p in body["posts"] if p.get("content") == "api_v1 reels testi — gerçek reel")
        assert "like_count" in reel_post and "comment_count" in reel_post

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("user_id", author["id"]).execute()


def _cleanup_conversation(app, cid, *user_ids):
    """Konuşma testlerinin ürettiği messages/conversation_participants/
    conversations satırlarını + api_tokens'ı temizler. (auth kullanıcısı
    test_user_factory teardown'ında zaten silinir; profiles(id) FK'leri
    ON DELETE CASCADE olduğundan bu satırlar aslında o silme ile de
    temizlenirdi — burada AYRICA/erken temizlenir ki aynı test dosyasındaki
    başka testler kalıntıyla karışmasın.)"""
    with app.app_context():
        sb = get_sb()
        sb.table("messages").delete().eq("conversation_id", cid).execute()
        sb.table("conversation_participants").delete().eq("conversation_id", cid).execute()
        sb.table("conversations").delete().eq("id", cid).execute()
        for uid in user_ids:
            sb.table("api_tokens").delete().eq("user_id", uid).execute()


class TestApiV1Messaging:
    """messaging/*.py'nin metin-mesaj + 1:1 inbox alt kümesinin JSON API'si —
    grup/görsel/ses/sticker/tepki/arama BU İTERASYONUN kapsamı dışında."""

    def test_conversations_empty_for_new_user(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_msg_empty@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.get(
            "/api/v1/messages/conversations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"conversations": []}

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_start_send_and_view_conversation_roundtrip(self, app, client, test_user_factory):
        user_a = test_user_factory(email="apiv1_msg_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        token_b = _api_token_for(app, user_b["id"])
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        start_resp = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        assert start_resp.status_code == 200
        cid = start_resp.get_json()["conversation_id"]
        assert cid

        # get-or-create: aynı çift için tekrar başlatınca AYNI konuşma dönmeli
        start_resp2 = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        assert start_resp2.get_json()["conversation_id"] == cid

        send_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"content": "api_v1 mesajlaşma testi - merhaba"},
            headers=headers_a,
        )
        assert send_resp.status_code == 200
        sent = send_resp.get_json()["message"]
        assert sent["content"] == "api_v1 mesajlaşma testi - merhaba"
        assert sent["sender_id"] == user_a["id"]

        # reply_to_id ile B'den cevap
        reply_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"content": "api_v1 mesajlaşma testi - cevap", "reply_to_id": sent["id"]},
            headers=headers_b,
        )
        assert reply_resp.status_code == 200
        reply_msg = reply_resp.get_json()["message"]
        assert reply_msg.get("reply_to_id") == sent["id"]

        # Karşı taraf (B) konuşmayı ve mesaj geçmişini görebiliyor mu
        detail_resp = client.get(f"/api/v1/messages/conversations/{cid}", headers=headers_b)
        assert detail_resp.status_code == 200
        detail_body = detail_resp.get_json()
        assert detail_body["conversation"]["id"] == cid
        assert detail_body["conversation"]["is_group"] is False
        assert detail_body["conversation"]["name"] == user_a["username"]
        contents = [m["content"] for m in detail_body["messages"]]
        assert "api_v1 mesajlaşma testi - merhaba" in contents
        assert "api_v1 mesajlaşma testi - cevap" in contents

        # Inbox'ta da görünüyor mu (her iki taraf için, karşı tarafın adıyla)
        inbox_a = client.get("/api/v1/messages/conversations", headers=headers_a).get_json()
        conv_a = next(c for c in inbox_a["conversations"] if c["id"] == cid)
        assert conv_a["is_group"] is False
        assert conv_a["name"] == user_b["username"]
        assert conv_a["last_message_preview"] == "api_v1 mesajlaşma testi - cevap"[:40]

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])

    def test_non_participant_cannot_access_conversation(self, app, client, test_user_factory):
        """Enumeration/yetkisiz erişim koruması: konuşmaya katılımcı OLMAYAN
        3. bir kullanıcı ne mesaj geçmişini görebilir, ne mesaj gönderebilir,
        ne de okundu işaretleyebilir."""
        user_a = test_user_factory(email="apiv1_msg_c_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_c_b@example.com", password="TestPass123!")
        outsider = test_user_factory(email="apiv1_msg_c_out@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        token_out = _api_token_for(app, outsider["id"])
        headers_out = {"Authorization": f"Bearer {token_out}"}

        start_resp = client.post(
            f"/api/v1/messages/start/{user_b['username']}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        cid = start_resp.get_json()["conversation_id"]

        detail_resp = client.get(f"/api/v1/messages/conversations/{cid}", headers=headers_out)
        assert detail_resp.status_code == 403

        send_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"content": "izinsiz mesaj"},
            headers=headers_out,
        )
        assert send_resp.status_code == 403

        mark_read_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/mark-read", headers=headers_out
        )
        assert mark_read_resp.status_code == 404

        # Dışarıdan gönderilen mesaj gerçekten insert EDİLMEDİ mi?
        with app.app_context():
            rows = get_sb().table("messages").select("id").eq(
                "conversation_id", cid
            ).eq("content", "izinsiz mesaj").execute().data
        assert rows == []

        _cleanup_conversation(app, cid, user_a["id"], outsider["id"])

    def test_mark_read_updates_has_unread_status(self, app, client, test_user_factory):
        user_a = test_user_factory(email="apiv1_msg_read_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_read_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        token_b = _api_token_for(app, user_b["id"])
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        start_resp = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        cid = start_resp.get_json()["conversation_id"]

        client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"content": "api_v1 okundu testi"},
            headers=headers_a,
        )

        inbox_b_before = client.get("/api/v1/messages/conversations", headers=headers_b).get_json()
        conv_before = next(c for c in inbox_b_before["conversations"] if c["id"] == cid)
        assert conv_before["has_unread"] is True

        mark_resp = client.post(f"/api/v1/messages/conversations/{cid}/mark-read", headers=headers_b)
        assert mark_resp.status_code == 200
        assert mark_resp.get_json()["ok"] is True

        inbox_b_after = client.get("/api/v1/messages/conversations", headers=headers_b).get_json()
        conv_after = next(c for c in inbox_b_after["conversations"] if c["id"] == cid)
        assert conv_after["has_unread"] is False

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])

    def test_send_message_rate_limit_enforced(self, app, client, test_user_factory):
        """send_message()'daki AYNI limit: kullanıcı bazlı, dakikada 30 mesaj
        (bkz. rate_limit.is_rate_limited: len(attempts) > max_attempts).
        Taze bir kullanıcı (yeni UUID) kullanılır ki `send_message:{me}`
        anahtarı başka testlerle KARIŞMASIN (bkz. _api_token_for docstring'i
        — aynı tuzak burada da geçerli)."""
        user_a = test_user_factory(email="apiv1_msg_rl_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_rl_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        headers_a = {"Authorization": f"Bearer {token_a}"}

        start_resp = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        cid = start_resp.get_json()["conversation_id"]

        statuses = []
        for i in range(31):
            resp = client.post(
                f"/api/v1/messages/conversations/{cid}/send",
                data={"content": f"rl-mesaj-{i}"},
                headers=headers_a,
            )
            statuses.append(resp.status_code)

        assert statuses[:30] == [200] * 30
        assert statuses[30] == 429

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])

    def test_send_image_message_uploads_and_returns_url(self, app, client, test_user_factory):
        """Faz 4 medya mesajları — multipart/form-data'ya geçişin (JSON'dan)
        regresyon testi: content YOKKEN sadece görselle gönderim başarılı
        olmalı, dönen mesajın image_url'i GERÇEKTEN Supabase Storage'a
        yüklenmiş bir URL olmalı (mock yok — minimal ama geçerli bir PNG
        magic-byte header'ı, storage_helper._detect_image_kind()'ı geçer)."""
        user_a = test_user_factory(email="apiv1_msg_img_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_img_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        headers_a = {"Authorization": f"Bearer {token_a}"}

        start_resp = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        cid = start_resp.get_json()["conversation_id"]

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"image": (BytesIO(png_bytes), "photo.png")},
            headers=headers_a,
        )
        assert resp.status_code == 200
        sent = resp.get_json()["message"]
        assert sent["content"] == ""
        assert sent["image_url"]
        assert sent["image_url"].startswith("http")

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])

    def test_send_message_without_content_or_image_returns_400(self, app, client, test_user_factory):
        user_a = test_user_factory(email="apiv1_msg_empty_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_msg_empty_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        headers_a = {"Authorization": f"Bearer {token_a}"}

        start_resp = client.post(f"/api/v1/messages/start/{user_b['username']}", headers=headers_a)
        cid = start_resp.get_json()["conversation_id"]

        resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "empty"

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])


class TestApiV1GroupChat:
    """messaging/creation.py create_group() + messaging/group_admin.py'nin
    TAMAMININ (rename/üye ekle-çıkar/admin toggle/ayrılma) JSON API'si."""

    def _create_group(self, client, headers, name, member_ids):
        return client.post(
            "/api/v1/messages/group/new",
            json={"name": name, "user_ids": member_ids},
            headers=headers,
        )

    def test_create_group_appears_in_inbox_with_correct_metadata_and_admin_flags(
        self, app, client, test_user_factory
    ):
        creator = test_user_factory(email="apiv1_grp_create_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_create_b@example.com", password="TestPass123!")
        member_c = test_user_factory(email="apiv1_grp_create_c@example.com", password="TestPass123!")
        token_creator = _api_token_for(app, creator["id"])
        headers_creator = {"Authorization": f"Bearer {token_creator}"}

        resp = self._create_group(
            client, headers_creator, "api_v1 test grubu", [member_b["id"], member_c["id"]]
        )
        assert resp.status_code == 200
        cid = resp.get_json()["conversation_id"]
        assert cid

        inbox = client.get("/api/v1/messages/conversations", headers=headers_creator).get_json()
        conv = next(c for c in inbox["conversations"] if c["id"] == cid)
        assert conv["is_group"] is True
        assert conv["name"] == "api_v1 test grubu"
        assert conv["member_count"] == 3

        members_resp = client.get(f"/api/v1/messages/group/{cid}/members", headers=headers_creator)
        assert members_resp.status_code == 200
        members = members_resp.get_json()["members"]
        assert len(members) == 3
        by_id = {m["id"]: m for m in members}
        assert by_id[creator["id"]]["is_admin"] is True
        assert by_id[member_b["id"]]["is_admin"] is False
        assert by_id[member_c["id"]]["is_admin"] is False

        _cleanup_conversation(app, cid, creator["id"], member_b["id"], member_c["id"])

    def test_create_group_too_few_members_returns_400(self, app, client, test_user_factory):
        creator = test_user_factory(email="apiv1_grp_few_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_few_b@example.com", password="TestPass123!")
        token = _api_token_for(app, creator["id"])

        resp = self._create_group(client, {"Authorization": f"Bearer {token}"}, "eksik grup", [member_b["id"]])
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "too_few_members"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", creator["id"]).execute()
            get_sb().table("api_tokens").delete().eq("user_id", member_b["id"]).execute()

    def test_non_admin_and_non_participant_get_403_on_admin_actions(self, app, client, test_user_factory):
        """Admin OLMAYAN bir üye VE gruba HİÇ katılımcı olmayan biri, rename/
        add/remove/toggle-admin denerse (ikisi de _api_is_group_admin() için
        DB'de satır bulamadığından) AYNI 403 not_admin'i alır; members
        endpoint'i ise katılımcı olmayanı ayrıca 403 forbidden ile reddeder."""
        creator = test_user_factory(email="apiv1_grp_perm_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_perm_b@example.com", password="TestPass123!")
        member_d = test_user_factory(email="apiv1_grp_perm_d@example.com", password="TestPass123!")
        outsider = test_user_factory(email="apiv1_grp_perm_out@example.com", password="TestPass123!")
        headers_creator = {"Authorization": f"Bearer {_api_token_for(app, creator['id'])}"}
        headers_b = {"Authorization": f"Bearer {_api_token_for(app, member_b['id'])}"}
        headers_out = {"Authorization": f"Bearer {_api_token_for(app, outsider['id'])}"}

        resp = self._create_group(client, headers_creator, "izin testi grubu", [member_b["id"], member_d["id"]])
        cid = resp.get_json()["conversation_id"]

        for headers in (headers_b, headers_out):
            rename_resp = client.post(
                f"/api/v1/messages/group/{cid}/rename", json={"name": "yeni ad"}, headers=headers
            )
            assert rename_resp.status_code == 403
            assert rename_resp.get_json()["error"] == "not_admin"

            add_resp = client.post(
                f"/api/v1/messages/group/{cid}/members/add",
                json={"user_ids": [outsider["id"]]},
                headers=headers,
            )
            assert add_resp.status_code == 403
            assert add_resp.get_json()["error"] == "not_admin"

            remove_resp = client.post(
                f"/api/v1/messages/group/{cid}/members/{member_d['id']}/remove", headers=headers
            )
            assert remove_resp.status_code == 403
            assert remove_resp.get_json()["error"] == "not_admin"

            toggle_resp = client.post(
                f"/api/v1/messages/group/{cid}/members/{member_d['id']}/toggle-admin", headers=headers
            )
            assert toggle_resp.status_code == 403
            assert toggle_resp.get_json()["error"] == "not_admin"

        # outsider ayrıca üye listesini de göremez (forbidden — detail/send ile tutarlı)
        members_resp = client.get(f"/api/v1/messages/group/{cid}/members", headers=headers_out)
        assert members_resp.status_code == 403
        assert members_resp.get_json()["error"] == "forbidden"

        _cleanup_conversation(app, cid, creator["id"], member_b["id"], member_d["id"], outsider["id"])

    def test_admin_add_remove_member_and_self_remove_guard(self, app, client, test_user_factory):
        creator = test_user_factory(email="apiv1_grp_addrm_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_addrm_b@example.com", password="TestPass123!")
        member_d = test_user_factory(email="apiv1_grp_addrm_d@example.com", password="TestPass123!")
        new_member = test_user_factory(email="apiv1_grp_addrm_new@example.com", password="TestPass123!")
        headers_creator = {"Authorization": f"Bearer {_api_token_for(app, creator['id'])}"}

        resp = self._create_group(client, headers_creator, "ekle-çıkar grubu", [member_b["id"], member_d["id"]])
        cid = resp.get_json()["conversation_id"]

        add_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/add",
            json={"user_ids": [new_member["id"]]},
            headers=headers_creator,
        )
        assert add_resp.status_code == 200
        added = add_resp.get_json()["added"]
        assert len(added) == 1
        assert added[0]["id"] == new_member["id"]
        assert added[0]["is_admin"] is False

        members_after_add = client.get(
            f"/api/v1/messages/group/{cid}/members", headers=headers_creator
        ).get_json()["members"]
        assert {m["id"] for m in members_after_add} == {
            creator["id"], member_b["id"], member_d["id"], new_member["id"]
        }

        remove_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{member_b['id']}/remove", headers=headers_creator
        )
        assert remove_resp.status_code == 200
        assert remove_resp.get_json()["ok"] is True

        members_after_remove = client.get(
            f"/api/v1/messages/group/{cid}/members", headers=headers_creator
        ).get_json()["members"]
        assert member_b["id"] not in {m["id"] for m in members_after_remove}

        # Kendini çıkarmaya çalışmak
        self_remove_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{creator['id']}/remove", headers=headers_creator
        )
        assert self_remove_resp.status_code == 400
        assert self_remove_resp.get_json()["error"] == "cannot_remove_self"

        # Zaten çıkarılmış (artık üye olmayan) birini tekrar çıkarmaya çalışmak
        already_removed_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{member_b['id']}/remove", headers=headers_creator
        )
        assert already_removed_resp.status_code == 404
        assert already_removed_resp.get_json()["error"] == "not_a_member"

        _cleanup_conversation(
            app, cid, creator["id"], member_b["id"], member_d["id"], new_member["id"]
        )

    def test_toggle_admin_and_last_admin_guard(self, app, client, test_user_factory):
        creator = test_user_factory(email="apiv1_grp_toggle_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_toggle_b@example.com", password="TestPass123!")
        member_d = test_user_factory(email="apiv1_grp_toggle_d@example.com", password="TestPass123!")
        outsider = test_user_factory(email="apiv1_grp_toggle_out@example.com", password="TestPass123!")
        headers_creator = {"Authorization": f"Bearer {_api_token_for(app, creator['id'])}"}

        resp = self._create_group(client, headers_creator, "toggle grubu", [member_b["id"], member_d["id"]])
        cid = resp.get_json()["conversation_id"]

        # Tek admin (creator) kendini düşürmeye çalışırsa reddedilir
        guard_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{creator['id']}/toggle-admin", headers=headers_creator
        )
        assert guard_resp.status_code == 400
        assert guard_resp.get_json()["error"] == "last_admin"

        # Admin başka birini (member_b) admin yapar
        toggle_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{member_b['id']}/toggle-admin", headers=headers_creator
        )
        assert toggle_resp.status_code == 200
        assert toggle_resp.get_json()["is_admin"] is True

        # Artık başka bir admin (member_b) olduğundan creator kendini düşürebilir
        demote_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{creator['id']}/toggle-admin", headers=headers_creator
        )
        assert demote_resp.status_code == 200
        assert demote_resp.get_json()["is_admin"] is False

        # Üye olmayan (gruba hiç katılmamış) biri için toggle -> 404 not_a_member
        not_member_resp = client.post(
            f"/api/v1/messages/group/{cid}/members/{outsider['id']}/toggle-admin",
            headers={"Authorization": f"Bearer {_api_token_for(app, member_b['id'])}"},
        )
        assert not_member_resp.status_code == 404
        assert not_member_resp.get_json()["error"] == "not_a_member"

        _cleanup_conversation(app, cid, creator["id"], member_b["id"], member_d["id"], outsider["id"])

    def test_leave_group_removes_participant_and_reassigns_sole_admin(self, app, client, test_user_factory):
        """RPC'nin gerçek davranışını (varsaymadan) doğrular: tek admin
        ayrılınca kalan üyelerden biri otomatik admin olur; admin OLMAYAN
        biri ayrılınca mevcut admin değişmez."""
        creator = test_user_factory(email="apiv1_grp_leave_a@example.com", password="TestPass123!")
        member_b = test_user_factory(email="apiv1_grp_leave_b@example.com", password="TestPass123!")
        member_d = test_user_factory(email="apiv1_grp_leave_d@example.com", password="TestPass123!")
        headers_creator = {"Authorization": f"Bearer {_api_token_for(app, creator['id'])}"}
        headers_b = {"Authorization": f"Bearer {_api_token_for(app, member_b['id'])}"}
        headers_d = {"Authorization": f"Bearer {_api_token_for(app, member_d['id'])}"}

        resp = self._create_group(client, headers_creator, "ayrılma grubu", [member_b["id"], member_d["id"]])
        cid = resp.get_json()["conversation_id"]

        # Admin olmayan (member_d) ayrılır -> sadece üye sayısı düşer, admin değişmez
        leave_d_resp = client.post(f"/api/v1/messages/group/{cid}/leave", headers=headers_d)
        assert leave_d_resp.status_code == 200
        assert leave_d_resp.get_json()["ok"] is True

        with app.app_context():
            remaining_after_d = get_sb().table("conversation_participants").select(
                "user_id, is_admin"
            ).eq("conversation_id", cid).execute().data
        remaining_ids = {r["user_id"] for r in remaining_after_d}
        assert member_d["id"] not in remaining_ids
        assert {r["user_id"]: r["is_admin"] for r in remaining_after_d}[creator["id"]] is True

        # Aynı kişi zaten ayrılmışken tekrar ayrılmaya çalışırsa 404 not_a_member
        leave_d_again_resp = client.post(f"/api/v1/messages/group/{cid}/leave", headers=headers_d)
        assert leave_d_again_resp.status_code == 404
        assert leave_d_again_resp.get_json()["error"] == "not_a_member"

        # Tek admin (creator) ayrılır -> kalan tek üye (member_b) otomatik admin olmalı
        leave_creator_resp = client.post(f"/api/v1/messages/group/{cid}/leave", headers=headers_creator)
        assert leave_creator_resp.status_code == 200

        with app.app_context():
            remaining_after_creator = get_sb().table("conversation_participants").select(
                "user_id, is_admin"
            ).eq("conversation_id", cid).execute().data
        assert {r["user_id"] for r in remaining_after_creator} == {member_b["id"]}
        assert remaining_after_creator[0]["is_admin"] is True

        _cleanup_conversation(app, cid, creator["id"], member_b["id"], member_d["id"])


class TestApiV1Interactions:
    def test_like_without_token_returns_401(self, client):
        resp = client.post("/api/v1/posts/nonexistent/like")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_toggle_like_then_unlike(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_like_author@example.com", password="TestPass123!")
        liker = test_user_factory(email="apiv1_like_liker@example.com", password="TestPass123!")
        token = _api_token_for(app, liker["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 beğeni testi için post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        like_resp = client.post(f"/api/v1/posts/{post_id}/like", headers=headers)
        assert like_resp.status_code == 200
        like_body = like_resp.get_json()
        assert like_body["liked"] is True
        assert like_body["reaction"] == "like"
        assert like_body["count"] == 1

        # Aynı reaksiyona tekrar basınca kaldırılır (toggle)
        unlike_resp = client.post(f"/api/v1/posts/{post_id}/like", headers=headers)
        assert unlike_resp.status_code == 200
        unlike_body = unlike_resp.get_json()
        assert unlike_body["liked"] is False
        assert unlike_body["reaction"] is None
        assert unlike_body["count"] == 0

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", liker["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_post_detail_returns_post_and_comment_hierarchy(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_detail_author@example.com", password="TestPass123!")
        commenter = test_user_factory(email="apiv1_detail_commenter@example.com", password="TestPass123!")
        token = _api_token_for(app, commenter["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 post detay testi",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
            post_id = post_row["id"]
            top_comment = sb.table("comments").insert({
                "post_id": post_id, "user_id": commenter["id"], "content": "ana yorum",
            }).execute().data[0]
            sb.table("comments").insert({
                "post_id": post_id, "user_id": author["id"], "content": "yanıt yorumu",
                "parent_comment_id": top_comment["id"],
            }).execute()

        resp = client.get(f"/api/v1/posts/{post_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["post"]["id"] == post_id
        assert "like_count" in body["post"] and "comment_count" in body["post"]
        assert len(body["comments"]) == 1
        top = body["comments"][0]
        assert top["content"] == "ana yorum"
        assert len(top["replies"]) == 1
        assert top["replies"][0]["content"] == "yanıt yorumu"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", commenter["id"]).execute()
            sb.table("comments").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_add_comment_and_reply_roundtrip(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_addcomment_author@example.com", password="TestPass123!")
        commenter = test_user_factory(email="apiv1_addcomment_commenter@example.com", password="TestPass123!")
        token = _api_token_for(app, commenter["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 yorum ekleme testi",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        add_resp = client.post(
            f"/api/v1/posts/{post_id}/comments",
            json={"content": "api_v1 ilk yorum"},
            headers=headers,
        )
        assert add_resp.status_code == 200
        added = add_resp.get_json()["comment"]
        assert added["content"] == "api_v1 ilk yorum"
        assert added["parent_comment_id"] is None
        comment_id = added["id"]

        reply_resp = client.post(
            f"/api/v1/posts/{post_id}/comments",
            json={"content": "api_v1 yanıt", "parent_comment_id": comment_id},
            headers=headers,
        )
        assert reply_resp.status_code == 200
        reply = reply_resp.get_json()["comment"]
        assert reply["parent_comment_id"] == comment_id

        # Boş içerik → 400
        empty_resp = client.post(
            f"/api/v1/posts/{post_id}/comments", json={"content": ""}, headers=headers,
        )
        assert empty_resp.status_code == 400

        detail = client.get(f"/api/v1/posts/{post_id}", headers=headers).get_json()
        assert len(detail["comments"]) == 1
        assert len(detail["comments"][0]["replies"]) == 1

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", commenter["id"]).execute()
            sb.table("comments").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_post_detail_hides_others_private_draft_post(self, app, client, test_user_factory):
        """_can_view_post() korumasi: baskasinin taslak (is_draft) postuna
        erisim 404 ile engellenir - enumeration onleme, kaynak fonksiyonla
        BIREBIR ayni davranis."""
        author = test_user_factory(email="apiv1_detail_priv_author@example.com", password="TestPass123!")
        viewer = test_user_factory(email="apiv1_detail_priv_viewer@example.com", password="TestPass123!")
        token = _api_token_for(app, viewer["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 taslak gizlilik testi",
                "visibility": "public",
                "is_draft": True,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        resp = client.get(f"/api/v1/posts/{post_id}", headers=headers)
        assert resp.status_code == 404

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", viewer["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()


class TestApiV1CreatePost:
    """Not: image dosya yükleme (upload_image) yolu BİLEREK burada test
    EDİLMİYOR — bu test suite'inde hiçbir yerde gerçek Supabase Storage'a
    dosya yükleyen bir test yok (temizlik/kirlilik riski), bu endpoint de
    aynı sınırı korur; sadece metin-yolu (endpoint'in kendi mantığı:
    doğrulama/görünürlük/hashtag senkronu/response şekli) test edilir."""

    def test_create_post_without_token_returns_401(self, client):
        resp = client.post("/api/v1/posts", data={"content": "deneme"})
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_create_post_empty_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_createpost_empty@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post("/api/v1/posts", data={"content": ""}, headers=headers)
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "empty"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_create_post_text_only_returns_post_shape(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_createpost_text@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/posts",
            data={"content": "api_v1 post oluşturma testi #apiv1createposttest"},
            headers=headers,
        )
        assert resp.status_code == 200
        post = resp.get_json()["post"]
        assert post["content"] == "api_v1 post oluşturma testi #apiv1createposttest"
        assert post["user_id"] == user["id"]
        assert post["visibility"] == "public"
        assert post["like_count"] == 0
        assert post["comment_count"] == 0
        assert post["liked_by_me"] is False
        post_id = post["id"]

        # Hashtag senkronu gerçekten çalıştı mı (create_post()'daki AYNI yan etki)
        with app.app_context():
            sb = get_sb()
            linked = sb.table("post_hashtags").select("hashtag_id").eq("post_id", post_id).execute().data
        assert len(linked) >= 1

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("post_hashtags").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_create_post_invalid_visibility_falls_back_to_public(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_createpost_vis@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/posts",
            data={"content": "api_v1 görünürlük testi", "visibility": "gecersiz_deger"},
            headers=headers,
        )
        assert resp.status_code == 200
        post = resp.get_json()["post"]
        assert post["visibility"] == "public"
        post_id = post["id"]

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_create_post_followers_visibility_accepted(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_createpost_followers@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/posts",
            data={"content": "api_v1 takipçilere özel testi", "visibility": "followers"},
            headers=headers,
        )
        assert resp.status_code == 200
        post = resp.get_json()["post"]
        assert post["visibility"] == "followers"
        post_id = post["id"]

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_create_post_with_poll_creates_poll_and_returns_options(self, app, client, test_user_factory):
        """Faz 5 Dalga 4C — poll_option_1..4 (en az 2 dolu) anket oluşturur,
        response'taki post.poll dolu döner (attach_polls() çağrısının AYNI
        turda eklendiği regresyon testi)."""
        user = test_user_factory(email="apiv1_createpost_poll@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/posts",
            data={
                "content": "api_v1 anket testi",
                "poll_option_1": "Seçenek A",
                "poll_option_2": "Seçenek B",
                "poll_option_3": "",
                "poll_option_4": "",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        post = resp.get_json()["post"]
        post_id = post["id"]
        assert post["poll"] is not None
        option_texts = {o["text"] for o in post["poll"]["options"]}
        assert option_texts == {"Seçenek A", "Seçenek B"}

        with app.app_context():
            sb = get_sb()
            poll_id = post["poll"]["id"]
            sb.table("poll_options").delete().eq("poll_id", poll_id).execute()
            sb.table("polls").delete().eq("id", poll_id).execute()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_create_post_poll_without_content_returns_poll_question_required(
        self, app, client, test_user_factory,
    ):
        user = test_user_factory(email="apiv1_createpost_poll_noq@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/posts",
            data={"content": "", "poll_option_1": "A", "poll_option_2": "B"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "poll_question_required"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1ProfileEdit:
    """profile.py profile_edit()'in POST dalının JSON API karşılığı.

    Not: avatar dosya yükleme (upload_image) yolu BİLEREK test EDİLMİYOR —
    TestApiV1CreatePost'daki AYNI gerekçe (bu suite'te gerçek Supabase
    Storage'a dosya yükleyen tek bir test yok, temizlik/kirlilik riski)."""

    def test_edit_profile_updates_username_bio_full_name(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_pedit_ok@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}
        new_username = "apiv1peditnewuser"

        resp = client.post(
            "/api/v1/profile/edit",
            data={
                "full_name": "Yeni Ad Soyad",
                "bio": "apiv1 profil düzenleme testi bio",
                "username": new_username,
                "is_private": "true",
                "hide_last_seen": "false",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["profile"]["username"] == new_username
        assert body["profile"]["full_name"] == "Yeni Ad Soyad"
        assert body["profile"]["bio"] == "apiv1 profil düzenleme testi bio"
        assert body["profile"]["is_private"] is True
        assert body["profile"]["hide_last_seen"] is False

        # DB'de gerçekten değişti mi (JSON yanıtı değil, kaynağın kendisi)
        with app.app_context():
            row = get_sb().table("profiles").select("*").eq("id", user["id"]).execute().data[0]
        assert row["username"] == new_username
        assert row["full_name"] == "Yeni Ad Soyad"
        assert row["bio"] == "apiv1 profil düzenleme testi bio"
        assert row["is_private"] is True

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_edit_profile_short_username_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_pedit_short@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/profile/edit",
            data={"username": "ab"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "short_username"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_edit_profile_taken_username_returns_400(self, app, client, test_user_factory):
        owner = test_user_factory(
            email="apiv1_pedit_taken_owner@example.com", password="TestPass123!",
            username="apiv1pedittakenowner",
        )
        other = test_user_factory(email="apiv1_pedit_taken_other@example.com", password="TestPass123!")
        token = _api_token_for(app, other["id"])

        resp = client.post(
            "/api/v1/profile/edit",
            data={"username": owner["username"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "username_taken"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", other["id"]).execute()


class TestApiV1NotificationPreferences:
    """notifications.py preferences()'ın JSON API karşılığı."""

    def test_get_preferences_defaults_all_true_when_no_row(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_notifpref_default@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        # Satırın gerçekten yok olduğundan emin ol (fail-open davranışı test ediliyor)
        with app.app_context():
            get_sb().table("notification_preferences").delete().eq("user_id", user["id"]).execute()

        resp = client.get("/api/v1/notifications/preferences", headers=headers)
        assert resp.status_code == 200
        prefs = resp.get_json()["preferences"]
        assert len(prefs) == 13
        assert all(v is True for v in prefs.values())

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_post_preferences_updates_one_field_others_stay_true(self, app, client, test_user_factory):
        """POST body eksik/yok alanı False sayar (web checkbox'ın "yoksa kapalı"
        davranışının JSON karşılığı) — bu yüzden native client, web formu gibi,
        TÜM 13 alanı her seferinde açıkça gönderir (bir ayarlar ekranının tüm
        toggle durumunu tek seferde göndermesi doğal kullanımdır). Burada
        notify_like DIŞINDAKİ 12 alan True gönderilip DB'de True kaldığı,
        notify_like'ın False yazıldığı doğrulanır."""
        user = test_user_factory(email="apiv1_notifpref_post@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        all_true_except_like = {col: True for col in _NOTIF_PREF_COLUMNS}
        all_true_except_like["notify_like"] = False

        resp = client.post(
            "/api/v1/notifications/preferences",
            json=all_true_except_like,
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        with app.app_context():
            row = get_sb().table("notification_preferences").select("*").eq(
                "user_id", user["id"]
            ).execute().data[0]
        assert row["notify_like"] is False
        for col in _NOTIF_PREF_COLUMNS:
            if col != "notify_like":
                assert row[col] is True, f"{col} True kalmalıydı"

        get_resp = client.get("/api/v1/notifications/preferences", headers=headers)
        get_prefs = get_resp.get_json()["preferences"]
        assert get_prefs["notify_like"] is False
        assert get_prefs["notify_comment"] is True

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("notification_preferences").delete().eq("user_id", user["id"]).execute()


class TestApiV1Notifications:
    """notifications.py list_notifications()/unread_count()'un JSON API karşılığı.

    KRİTİK: web'in target_url'ü (url_for ile üretilen bir route string'i)
    yerine native'in kendi navigasyon kararını verebileceği ham id alanları
    (post_id/username/conversation_id/hashtag) döner — bu testler o ham
    alanların DOĞRU tür için DOĞRU değeri taşıdığını doğrular.

    like/follow bildirimleri api_v1.py'de SENKRON (notify() doğrudan çağrılır,
    arkaplan pool'a submit edilmez) — bu yüzden polling gerekmez. message
    bildirimi ise _write_pool'a submit edilir (bkz. api_send_message), test
    test_comments.py'deki AYNI polling desenini kullanır.
    """

    def test_notifications_without_token_returns_401(self, client):
        resp = client.get("/api/v1/notifications")
        assert resp.status_code == 401
        resp2 = client.get("/api/v1/notifications/unread-count")
        assert resp2.status_code == 401

    def test_like_notification_has_post_id_and_second_view_marks_read(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_notif_like_author@example.com", password="TestPass123!")
        liker = test_user_factory(email="apiv1_notif_like_liker@example.com", password="TestPass123!")
        author_token = _api_token_for(app, author["id"])
        liker_token = _api_token_for(app, liker["id"])
        author_headers = {"Authorization": f"Bearer {author_token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 bildirim testi - beğeni",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        like_resp = client.post(
            f"/api/v1/posts/{post_id}/like",
            headers={"Authorization": f"Bearer {liker_token}"},
        )
        assert like_resp.status_code == 200

        # like() senkron notify() çağırıyor — bildirim bu noktada zaten DB'de.
        count_resp = client.get("/api/v1/notifications/unread-count", headers=author_headers)
        assert count_resp.get_json()["count"] == 1

        list_resp = client.get("/api/v1/notifications", headers=author_headers)
        assert list_resp.status_code == 200
        body = list_resp.get_json()
        entry = next(n for n in body["notifications"] if n["type"] == "like" and n["post_id"] == post_id)
        assert entry["is_read"] is False
        assert entry["actor_summary"] == liker["username"]
        assert entry["username"] is None  # like'ta profile değil post'a gidilir
        assert entry["conversation_id"] is None
        assert entry["hashtag"] is None
        assert entry["text"] == "gönderini beğendi"

        # Görüntüleme okundu işaretlemeliydi — cache invalidate edildi, taze sayım 0 olmalı.
        count_resp2 = client.get("/api/v1/notifications/unread-count", headers=author_headers)
        assert count_resp2.get_json()["count"] == 0

        list_resp2 = client.get("/api/v1/notifications", headers=author_headers)
        entry2 = next(n for n in list_resp2.get_json()["notifications"] if n["type"] == "like" and n["post_id"] == post_id)
        assert entry2["is_read"] is True

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", author["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", liker["id"]).execute()
            sb.table("notifications").delete().eq("post_id", post_id).execute()
            sb.table("likes").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_two_likes_on_same_post_group_into_one_notification(self, app, client, test_user_factory):
        """Gerçek gruplama testi: aynı posta 2 farklı kullanıcı like atınca
        notifications tablosunda 2 AYRI satır olsa bile /notifications tek
        gruplanmış satır döner, actor_summary 'A ve B' formatında gelir."""
        author = test_user_factory(email="apiv1_notif_group_author@example.com", password="TestPass123!")
        liker1 = test_user_factory(email="apiv1_notif_group_liker1@example.com", password="TestPass123!")
        liker2 = test_user_factory(email="apiv1_notif_group_liker2@example.com", password="TestPass123!")
        author_token = _api_token_for(app, author["id"])
        liker1_token = _api_token_for(app, liker1["id"])
        liker2_token = _api_token_for(app, liker2["id"])
        author_headers = {"Authorization": f"Bearer {author_token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 bildirim testi - grup beğeni",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        client.post(f"/api/v1/posts/{post_id}/like", headers={"Authorization": f"Bearer {liker1_token}"})
        client.post(f"/api/v1/posts/{post_id}/like", headers={"Authorization": f"Bearer {liker2_token}"})

        # DB seviyesinde gerçekten 2 ayrı satır oluştuğunu doğrula (gruplama
        # SADECE görüntüleme katmanında, DB'de tekilleşme YOK).
        with app.app_context():
            raw_rows = get_sb().table("notifications").select("id").eq(
                "recipient_id", author["id"]
            ).eq("post_id", post_id).eq("type", "like").execute().data
        assert len(raw_rows) == 2

        list_resp = client.get("/api/v1/notifications", headers=author_headers)
        assert list_resp.status_code == 200
        like_entries = [n for n in list_resp.get_json()["notifications"]
                         if n["type"] == "like" and n["post_id"] == post_id]
        assert len(like_entries) == 1, "İki like tek gruplanmış bildirime dönüşmeliydi"
        summary = like_entries[0]["actor_summary"]
        assert liker1["username"] in summary
        assert liker2["username"] in summary
        assert " ve " in summary
        assert like_entries[0]["is_read"] is False

        with app.app_context():
            sb = get_sb()
            for uid in (author["id"], liker1["id"], liker2["id"]):
                sb.table("api_tokens").delete().eq("user_id", uid).execute()
            sb.table("notifications").delete().eq("post_id", post_id).execute()
            sb.table("likes").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_follow_notification_includes_username(self, app, client, test_user_factory):
        owner = test_user_factory(email="apiv1_notif_follow_owner@example.com", password="TestPass123!")
        follower = test_user_factory(email="apiv1_notif_follow_follower@example.com", password="TestPass123!")
        owner_token = _api_token_for(app, owner["id"])
        follower_token = _api_token_for(app, follower["id"])

        follow_resp = client.post(
            f"/api/v1/profile/{owner['username']}/follow",
            headers={"Authorization": f"Bearer {follower_token}"},
        )
        assert follow_resp.status_code == 200
        assert follow_resp.get_json()["following"] is True  # public profil, direkt kabul

        list_resp = client.get(
            "/api/v1/notifications", headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert list_resp.status_code == 200
        entry = next(n for n in list_resp.get_json()["notifications"] if n["type"] == "follow")
        assert entry["username"] == follower["username"]
        assert entry["actor_summary"] == follower["username"]
        assert entry["post_id"] is None
        assert entry["conversation_id"] is None
        assert entry["text"] == "seni takip etmeye başladı"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", owner["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", follower["id"]).execute()
            sb.table("notifications").delete().eq("recipient_id", owner["id"]).execute()
            sb.table("follows").delete().eq("follower_id", follower["id"]).eq(
                "following_id", owner["id"]
            ).execute()

    def test_message_notification_includes_conversation_id(self, app, client, test_user_factory):
        user_a = test_user_factory(email="apiv1_notif_msg_a@example.com", password="TestPass123!")
        user_b = test_user_factory(email="apiv1_notif_msg_b@example.com", password="TestPass123!")
        token_a = _api_token_for(app, user_a["id"])
        token_b = _api_token_for(app, user_b["id"])

        start_resp = client.post(
            f"/api/v1/messages/start/{user_b['username']}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        cid = start_resp.get_json()["conversation_id"]

        send_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            data={"content": "api_v1 bildirim testi - mesaj"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert send_resp.status_code == 200

        # Mesaj bildirimi _write_pool'a (arkaplan) submit ediliyor — test_comments.py
        # ile AYNI polling deseni, DB'de oluşana kadar bekle.
        with app.app_context():
            sb = get_sb()
            deadline = time.time() + 5
            notif_rows = []
            while time.time() < deadline and not notif_rows:
                notif_rows = sb.table("notifications").select("*").eq(
                    "recipient_id", user_b["id"]
                ).eq("conversation_id", cid).eq("type", "message").execute().data
                if not notif_rows:
                    time.sleep(0.2)
            assert notif_rows, "Mesaj bildirimi (arkaplanda) oluşmadı"

        list_resp = client.get(
            "/api/v1/notifications", headers={"Authorization": f"Bearer {token_b}"},
        )
        assert list_resp.status_code == 200
        entry = next(n for n in list_resp.get_json()["notifications"] if n["type"] == "message")
        assert entry["conversation_id"] == cid
        assert entry["post_id"] is None
        assert entry["actor_summary"] == user_a["username"]

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])
        with app.app_context():
            get_sb().table("notifications").delete().eq("conversation_id", cid).execute()


class TestApiV1CloseFriends:
    """close_friends.py'nin JSON API karşılığı."""

    def test_add_list_remove_round_trip(self, app, client, test_user_factory):
        owner = test_user_factory(email="apiv1_cf_owner@example.com", password="TestPass123!")
        friend = test_user_factory(email="apiv1_cf_friend@example.com", password="TestPass123!")
        token = _api_token_for(app, owner["id"])
        headers = {"Authorization": f"Bearer {token}"}

        add_resp = client.post(
            "/api/v1/close-friends/add",
            json={"user_id": friend["id"]},
            headers=headers,
        )
        assert add_resp.status_code == 200
        assert add_resp.get_json()["ok"] is True

        list_resp = client.get("/api/v1/close-friends", headers=headers)
        assert list_resp.status_code == 200
        users = list_resp.get_json()["users"]
        assert any(u["id"] == friend["id"] for u in users)

        remove_resp = client.post(f"/api/v1/close-friends/{friend['id']}/remove", headers=headers)
        assert remove_resp.status_code == 200
        assert remove_resp.get_json()["ok"] is True

        list_resp2 = client.get("/api/v1/close-friends", headers=headers)
        users2 = list_resp2.get_json()["users"]
        assert not any(u["id"] == friend["id"] for u in users2)

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", owner["id"]).execute()
            sb.table("close_friends").delete().eq("owner_id", owner["id"]).execute()

    def test_add_self_as_close_friend_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_cf_self@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/close-friends/add",
            json={"user_id": user["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "cannot_add_self"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1ProfileDeactivate:
    """profile.py deactivate_account()'ın JSON API karşılığı."""

    def test_wrong_password_fails_and_does_not_deactivate(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_deact_wrongpw@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/profile/deactivate",
            json={"password": "WrongPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_password"

        with app.app_context():
            row = get_sb().table("profiles").select("is_deactivated").eq("id", user["id"]).execute().data[0]
        assert row["is_deactivated"] is False

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_correct_password_deactivates_and_revokes_token(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_deact_ok@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/profile/deactivate",
            json={"password": "TestPass123!"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        with app.app_context():
            row = get_sb().table("profiles").select("is_deactivated").eq("id", user["id"]).execute().data[0]
        assert row["is_deactivated"] is True

        # Deaktivasyonda kullanılan token artık iptal edilmiş olmalı
        me_resp = client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 401
        assert me_resp.get_json().get("error") == "unauthorized"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1TwoFactor:
    """2FA (TOTP) native endpoint'leri — GERÇEK Supabase MFA API'sine karşı,
    pyotp ile ÜRETİLMİŞ gerçek geçerli kodlarla (RFC 6238) enroll→verify→
    login round-trip'i kanıtlar (mock yok).

    login:{ip} birikimli rate-limit flakiness'i (bkz. _api_token_for
    docstring'i + dosya başı) göz önünde bulundurularak, gerçek
    /api/v1/auth/login çağrısı SADECE login()'in 2FA dalını doğrulayan 3
    testte yapılır (toplam 4 çağrı + TestApiV1Login'deki 2 = 6/10, güvenli
    marj). /2fa/enroll, /2fa/enroll/verify, /2fa/disable endpoint'leri
    KENDİ taze sign_in_with_password'lerini kullanır — bunlar login:{ip}
    sayacını hiç ETKİLEMEZ (ayrı bir Supabase çağrısı, /api/v1/auth/login
    route'undan geçmiyor)."""

    def _enroll_and_verify(self, client, token, password):
        """Enroll → secret'tan GERÇEK geçerli TOTP kodu üret → verify.
        (factor_id, secret) döner — factor_id sonraki bir enroll denemesinde
        (already_enabled testi) veya secret sonraki bir login kodu üretiminde
        kullanılabilir."""
        enroll_resp = client.post(
            "/api/v1/2fa/enroll",
            json={"password": password},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert enroll_resp.status_code == 200, enroll_resp.get_json()
        body = enroll_resp.get_json()
        factor_id = body.get("factor_id")
        secret = body.get("secret")
        assert factor_id and secret and body.get("qr_code")

        code = pyotp.TOTP(secret).now()
        verify_resp = client.post(
            "/api/v1/2fa/enroll/verify",
            json={"password": password, "factor_id": factor_id, "code": code},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert verify_resp.status_code == 200, verify_resp.get_json()
        assert verify_resp.get_json().get("ok") is True
        return factor_id, secret

    def test_status_false_before_enroll(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_2fa_status_off@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.get("/api/v1/2fa/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": False}

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_enroll_wrong_password_returns_invalid_password(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_2fa_enroll_wrongpw@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/2fa/enroll",
            json={"password": "WrongPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_password"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_enroll_missing_password_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_2fa_enroll_nopw@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/2fa/enroll",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "password_required"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_enroll_verify_roundtrip_then_status_true_then_already_enabled(
        self, app, client, test_user_factory
    ):
        """Tam round-trip: enroll → GERÇEK TOTP kodu üret → verify → status
        (enabled) → tekrar enroll (already_enabled, 409)."""
        email = "apiv1_2fa_roundtrip@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        self._enroll_and_verify(client, token, password)

        status_resp = client.get("/api/v1/2fa/status", headers={"Authorization": f"Bearer {token}"})
        assert status_resp.status_code == 200
        assert status_resp.get_json() == {"enabled": True}

        again_resp = client.post(
            "/api/v1/2fa/enroll",
            json={"password": password},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert again_resp.status_code == 409
        assert again_resp.get_json().get("error") == "already_enabled"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_enroll_verify_invalid_code_format(self, app, client, test_user_factory):
        email = "apiv1_2fa_badcodefmt@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        enroll_resp = client.post(
            "/api/v1/2fa/enroll",
            json={"password": password},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert enroll_resp.status_code == 200
        factor_id = enroll_resp.get_json()["factor_id"]

        resp = client.post(
            "/api/v1/2fa/enroll/verify",
            json={"password": password, "factor_id": factor_id, "code": "abc12"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "invalid_code_format"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_login_without_code_returns_mfa_required_then_valid_code_returns_token(
        self, app, client, test_user_factory
    ):
        """Gerçek uçtan uca: 2FA aktifken code'suz login → mfa_required;
        AYNI kullanıcı GEÇERLİ bir TOTP koduyla login → token döner."""
        email = "apiv1_2fa_login_flow@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        _, secret = self._enroll_and_verify(client, token, password)

        # Gerçek /api/v1/auth/login çağrısı #1 (bu sınıfta, code'suz)
        no_code_resp = client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert no_code_resp.status_code == 403
        assert no_code_resp.get_json().get("error") == "mfa_required"

        # Gerçek çağrı #2 — GEÇERLİ kod
        valid_code = pyotp.TOTP(secret).now()
        ok_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "code": valid_code},
        )
        assert ok_resp.status_code == 200
        body = ok_resp.get_json()
        assert body.get("token")
        assert body["user"]["id"] == user["id"]

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_login_with_wrong_code_returns_invalid_code(self, app, client, test_user_factory):
        email = "apiv1_2fa_login_wrongcode@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        _, secret = self._enroll_and_verify(client, token, password)

        real_code = pyotp.TOTP(secret).now()
        # Gerçek koddan KESİNLİKLE farklı, ama yine de 6 haneli/rakam bir kod
        wrong_code = "000000" if real_code != "000000" else "111111"

        # Gerçek çağrı #3 (bu sınıfta) — yanlış kod
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "code": wrong_code},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_code"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_disable_requires_code_then_wrong_code_fails_then_valid_code_succeeds_then_login_without_code(
        self, app, client, test_user_factory
    ):
        """Disable AAL2 akışının TAMAMI — gerçek Supabase'e karşı test sırasında
        bulunan bir bugu doğrular/kanıtlar (bkz. api_2fa_disable() DİKKAT notu):
        kurulu supabase-auth kütüphanesi, VERIFIED bir TOTP factor'ü sadece
        AAL1 (şifre) session'la unenroll etmeye izin VERMİYOR
        ("AAL2 required to unenroll verified factor", gerçek hesaba karşı
        doğrulandı) — bu yüzden password-only disable ASLA başarılı olamazdı;
        native endpoint'i buna göre bir `code` adımı ekleyecek şekilde
        DÜZELTİLDİ (spesifikasyondan sapma, kod yorumunda gerekçelendirildi).

        Sonda: disable BAŞARILI olduktan sonra login code istemeden
        (mfa_required olmadan) başarılı olmalı.
        """
        email = "apiv1_2fa_disable_then_login@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        _, secret = self._enroll_and_verify(client, token, password)

        # code'suz disable — code_required (unenroll'un AAL2 gerektirdiği
        # anlaşılınca client'a AÇIKÇA bildirilir, login()'deki mfa_required
        # deseniyle tutarlı)
        no_code_resp = client.post(
            "/api/v1/2fa/disable",
            json={"password": password},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert no_code_resp.status_code == 400
        assert no_code_resp.get_json().get("error") == "code_required"

        # Yanlış kodla disable — invalid_code (AAL2'ye yükseltme başarısız)
        real_code = pyotp.TOTP(secret).now()
        wrong_code = "000000" if real_code != "000000" else "111111"
        wrong_code_resp = client.post(
            "/api/v1/2fa/disable",
            json={"password": password, "code": wrong_code},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert wrong_code_resp.status_code == 401
        assert wrong_code_resp.get_json().get("error") == "invalid_code"

        # GEÇERLİ kod — AAL2'ye yükselt + gerçekten unenroll et
        valid_code = pyotp.TOTP(secret).now()
        disable_resp = client.post(
            "/api/v1/2fa/disable",
            json={"password": password, "code": valid_code},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert disable_resp.status_code == 200, disable_resp.get_json()
        assert disable_resp.get_json().get("ok") is True

        status_resp = client.get("/api/v1/2fa/status", headers={"Authorization": f"Bearer {token}"})
        assert status_resp.get_json() == {"enabled": False}

        # Gerçek çağrı #4 (bu sınıfta) — disable sonrası code'suz login
        login_resp = client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert login_resp.status_code == 200
        assert login_resp.get_json().get("token")

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_disable_wrong_password_returns_invalid_password(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_2fa_disable_wrongpw@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/2fa/disable",
            json={"password": "WrongPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_password"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_disable_no_active_2fa_returns_404(self, app, client, test_user_factory):
        email = "apiv1_2fa_disable_noactive@example.com"
        password = "TestPass123!"
        user = test_user_factory(email=email, password=password)
        token = _api_token_for(app, user["id"])

        resp = client.post(
            "/api/v1/2fa/disable",
            json={"password": password},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.get_json().get("error") == "no_active_2fa"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_2fa_enroll_rate_limit(self, app, client, test_user_factory):
        """5 deneme/300sn — 6. istek 429 döner. Anahtar kullanıcı bazlı
        (2fa_enroll:{user_id}) olduğu için login:{ip} bütçesini ETKİLEMEZ."""
        user = test_user_factory(email="apiv1_2fa_enroll_ratelimit@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        for _ in range(5):
            resp = client.post(
                "/api/v1/2fa/enroll",
                json={},  # password eksik — 400 döner ama yine de rate limit sayacını artırır
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400

        resp = client.post(
            "/api/v1/2fa/enroll",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 429
        assert resp.get_json().get("error") == "rate_limited"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


def _cleanup_registered_user(app, user_id):
    """api_register() ile GERÇEKTEN oluşturulmuş bir kullanıcıyı temizler —
    test_user_factory'nin cleanup'ından FARKLI olarak, kullanıcı fixture ile
    değil doğrudan endpoint'in kendisiyle yaratıldığı için burada elle
    yapılıyor (email değil user_id ile, register() sonucunda id doğrudan
    elde ediliyor)."""
    with app.app_context():
        sb = get_sb()
        try:
            sb.table("api_tokens").delete().eq("user_id", user_id).execute()
        except Exception:
            pass
        try:
            sb.table("profiles").delete().eq("id", user_id).execute()
        except Exception:
            pass
        try:
            sb.auth.admin.delete_user(user_id)
        except Exception:
            pass


class TestApiV1Register:
    """POST /api/v1/auth/register — register:{ip} paylaşılan bütçesi 5/600,
    bu sınıfta TAM OLARAK 5 gerçek çağrı var (sınır aşılmıyor, bkz. her
    testin docstring'i — dosyanın login:{ip} birikimli rate-limit
    flakiness geçmişiyle AYNI dikkat, bkz. _api_token_for docstring'i)."""

    def test_register_missing_fields_returns_400(self, client):
        resp = client.post("/api/v1/auth/register", json={"email": "x@example.com"})
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "missing_fields"

    def test_register_short_username_returns_400(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "apiv1_reg_shortu@example.com", "password": "TestPass123!", "username": "ab"},
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "short_username"

    def test_register_username_taken_returns_400(self, app, client, test_user_factory):
        existing = test_user_factory(
            email="apiv1_reg_existingu@example.com", password="TestPass123!", username="apiv1_reg_taken_name"
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "apiv1_reg_newemail@example.com",
                "password": "TestPass123!",
                "username": existing["username"],
            },
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "username_taken"

    def test_register_email_taken_returns_400(self, app, client, test_user_factory):
        existing = test_user_factory(email="apiv1_reg_existinge@example.com", password="TestPass123!")
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": existing["email"],
                "password": "TestPass123!",
                "username": "apiv1_reg_newname_xyz",
            },
        )
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "email_taken"

    def test_register_success_creates_user_and_returns_working_token(self, app, client):
        email = "apiv1_reg_success@example.com"
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!", "username": "apiv1_reg_success_user"},
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body.get("token")
        assert body["user"]["email"] == email
        assert body["user"]["username"] == "apiv1_reg_success_user"
        user_id = body["user"]["id"]

        try:
            me_resp = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {body['token']}"},
            )
            assert me_resp.status_code == 200
            assert me_resp.get_json()["user"]["id"] == user_id

            with app.app_context():
                prof = get_sb().table("profiles").select("is_private, username").eq(
                    "id", user_id
                ).execute().data[0]
            assert prof["is_private"] is True
            assert prof["username"] == "apiv1_reg_success_user"

            # Realtime oturumu — api_register()'a EKLENEN "otomatik giriş"
            # adımının (bkz. api_v1.py docstring güncellemesi) GERÇEKTEN bir
            # Supabase Auth session ürettiğini ve DB'ye şifreli yazıldığını
            # kanıtlar. YENİ bir gerçek /register çağrısı YOK — register:{ip}
            # bütçesi (5/600, bu sınıfta ZATEN tam 5 çağrı var) bu genişletmeyle
            # artmıyor, aynı tek çağrının sonucu ek olarak doğrulanıyor.
            from app.realtime_session import _decrypt_token
            with app.app_context():
                row = get_sb().table("api_tokens").select(
                    "sb_access_token_enc, sb_refresh_token_enc, sb_token_expires_at"
                ).eq("user_id", user_id).execute().data[0]
            assert row["sb_access_token_enc"]
            assert row["sb_refresh_token_enc"]
            assert row["sb_token_expires_at"]
            assert _decrypt_token(row["sb_access_token_enc"])
            assert _decrypt_token(row["sb_refresh_token_enc"])
        finally:
            _cleanup_registered_user(app, user_id)


class TestApiV1GoogleLogin:
    """POST /api/v1/auth/google — gerçek bir Google ID token'ı test ortamında
    ÜRETİLEMEZ (gerçek bir Google hesabı/cihaz gerektirir, bkz. native Kotlin
    dispatch'inde manuel doğrulanacak) — bu yüzden testler sadece REDDETME
    yollarını kanıtlar (mock yok, gerçek Supabase'in geçersiz bir token'ı
    gerçekten reddettiğini doğrular). İkisi de login:{ip} PAYLAŞILAN
    bütçesini kullanır (login()'deki AYNI anahtar) — sadece 2 çağrı, dosya
    genelindeki bütçeye dikkatle eklendi."""

    def test_google_login_missing_token_returns_400(self, client):
        resp = client.post("/api/v1/auth/google", json={})
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "missing_token"

    def test_google_login_invalid_token_returns_401(self, client):
        resp = client.post("/api/v1/auth/google", json={"id_token": "not-a-real-google-id-token"})
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "invalid_token"


class TestApiV1RealtimeSessionCrypto:
    """_encrypt_token/_decrypt_token round-trip — .env'deki gerçek
    REALTIME_TOKEN_ENCRYPTION_KEY (Fernet) ile çalışır, mock YOK. Hiçbir
    Flask route'unu (login/register/google) TETİKLEMEZ, bu yüzden dosyanın
    login:{ip}/register:{ip} paylaşılan bütçesine hiç dokunmaz."""

    def test_encrypt_decrypt_round_trip(self):
        from app.realtime_session import _decrypt_token, _encrypt_token
        raw = "sample-jwt-payload." + secrets.token_urlsafe(24)
        enc = _encrypt_token(raw)
        assert enc is not None
        assert enc != raw
        assert _decrypt_token(enc) == raw

    def test_decrypt_invalid_ciphertext_returns_none(self):
        """Bozuk/rastgele bir string InvalidToken'a düşer — exception dışarı
        SIZMAMALI (bkz. _decrypt_token docstring'i)."""
        from app.realtime_session import _decrypt_token
        assert _decrypt_token("bu-gecerli-bir-fernet-tokeni-degil") is None
        assert _decrypt_token("") is None
        assert _decrypt_token(None) is None


class TestApiV1RealtimeSessionFailOpen:
    """REALTIME_TOKEN_ENCRYPTION_KEY yokken login() akışının ÇEKİRDEĞİ
    (token issuance) hâlâ 200 dönmeli — Realtime sessizce devre dışı kalır
    ama giriş asla kırılmaz (bkz. app/realtime_session.py modül docstring'i).

    BÜTÇE NOTU: bu sınıfta TEK bir gerçek /api/v1/auth/login çağrısı var.
    Dosya genelinde bu noktaya kadarki login:{ip} kullanımı: TestApiV1Login
    (2) + TestApiV1TwoFactor (4, mfa login akışları) + TestApiV1GoogleLogin
    (2) = 8/10. Bu test 9/10 yapar — limit (10/300sn) hâlâ aşılmıyor."""

    def test_login_without_encryption_key_still_returns_200(
        self, app, client, test_user_factory, monkeypatch
    ):
        monkeypatch.delenv("REALTIME_TOKEN_ENCRYPTION_KEY", raising=False)
        user = test_user_factory(email="apiv1_realtime_failopen@example.com", password="TestPass123!")

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("token")

        # Fernet yoktu — _store_realtime_session no-op kaldı, kolonlar boş
        with app.app_context():
            row = get_sb().table("api_tokens").select(
                "sb_access_token_enc, sb_refresh_token_enc"
            ).eq("user_id", user["id"]).execute().data[0]
        assert row["sb_access_token_enc"] is None
        assert row["sb_refresh_token_enc"] is None

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1RealtimeToken:
    """GET /api/v1/realtime-token — token üretimi _api_token_for() ile YAPILIR
    (login:{ip} bütçesini KULLANMAZ); gerçek access/refresh çifti ise
    test_user_factory'nin DOĞRUDAN (Flask route'undan GEÇMEYEN, dolayısıyla
    login:{ip}'yi hiç artırmayan) sign_in_with_password çağrısından gelir.
    Bu sınıftaki HİÇBİR test dosyanın login:{ip}/register:{ip} bütçesini
    artırmaz."""

    def _seed_realtime_row(self, app, token_hash, access_token, refresh_token, expires_at_iso):
        from app.realtime_session import _encrypt_token
        with app.app_context():
            get_sb().table("api_tokens").update({
                "sb_access_token_enc": _encrypt_token(access_token),
                "sb_refresh_token_enc": _encrypt_token(refresh_token),
                "sb_token_expires_at": expires_at_iso,
            }).eq("token_hash", token_hash).execute()

    def test_realtime_token_without_bearer_returns_401(self, client):
        resp = client.get("/api/v1/realtime-token")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_realtime_token_unavailable_without_stored_session(self, app, client, test_user_factory):
        """Bu satır hiç enroll edilmemiş (sb_refresh_token_enc NULL) — unavailable."""
        user = test_user_factory(email="apiv1_realtime_unavail@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.get("/api/v1/realtime-token", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 503
        assert resp.get_json().get("error") == "unavailable"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_realtime_token_refreshes_when_expired(self, app, client, test_user_factory):
        """sb_token_expires_at GEÇMİŞTE → endpoint GERÇEK bir Supabase refresh
        grant'ı tetikler; dönen access_token'ın DB'de YENİDEN okunan
        sb_token_expires_at'i artık GELECEKTE olmalı (gerçek bir yenilemenin kanıtı)."""
        user = test_user_factory(email="apiv1_realtime_refresh@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._seed_realtime_row(app, token_hash, user["access_token"], user["refresh_token"], past)

        resp = client.get("/api/v1/realtime-token", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body.get("access_token")
        assert body.get("supabase_url")
        assert body.get("supabase_publishable_key")

        with app.app_context():
            row = get_sb().table("api_tokens").select("sb_token_expires_at").eq(
                "token_hash", token_hash
            ).execute().data[0]
        new_expires = datetime.fromisoformat(row["sb_token_expires_at"].replace("Z", "+00:00"))
        assert new_expires > datetime.now(timezone.utc)

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_realtime_token_relogin_required_when_refresh_token_dead(self, app, client, test_user_factory):
        """Refresh token KESİN geçersizse (Supabase 400/401/403 döner) endpoint
        relogin_required döner VE satırdaki sb_* kolonlarını NULL'a çeker
        (spec: /realtime-token adım 4 — 'kesin ret' dalı)."""
        user = test_user_factory(email="apiv1_realtime_dead@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        # Gerçek biçimde ama KESİNLİKLE geçersiz bir refresh token — Supabase'in
        # KENDİ token endpoint'i bunu 400 ile REDDEDER (mock yok, gerçek ret).
        self._seed_realtime_row(app, token_hash, user["access_token"], "invalid-refresh-token-xyz", past)

        resp = client.get("/api/v1/realtime-token", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "relogin_required"

        with app.app_context():
            row = get_sb().table("api_tokens").select(
                "sb_access_token_enc, sb_refresh_token_enc, sb_token_expires_at"
            ).eq("token_hash", token_hash).execute().data[0]
        assert row["sb_access_token_enc"] is None
        assert row["sb_refresh_token_enc"] is None
        assert row["sb_token_expires_at"] is None

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1Blocks:
    """POST /api/v1/block/<username> (toggle) + GET /api/v1/blocked —
    blocks.py toggle_block()/blocked_list()'in aynı mantığı."""

    def test_cannot_block_self(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_block_self@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])

        resp = client.post(
            f"/api/v1/block/{user['username']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "cannot_block_self"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_block_toggle_appears_in_blocked_list_and_unblock_removes_it(
        self, app, client, test_user_factory
    ):
        me = test_user_factory(email="apiv1_block_me@example.com", password="TestPass123!")
        target = test_user_factory(email="apiv1_block_target@example.com", password="TestPass123!")
        token = _api_token_for(app, me["id"])
        headers = {"Authorization": f"Bearer {token}"}

        block_resp = client.post(f"/api/v1/block/{target['username']}", headers=headers)
        assert block_resp.status_code == 200
        assert block_resp.get_json() == {"ok": True, "blocked": True}

        list_resp = client.get("/api/v1/blocked", headers=headers)
        assert list_resp.status_code == 200
        usernames = [u["username"] for u in list_resp.get_json()["users"]]
        assert target["username"] in usernames

        unblock_resp = client.post(f"/api/v1/block/{target['username']}", headers=headers)
        assert unblock_resp.status_code == 200
        assert unblock_resp.get_json() == {"ok": True, "blocked": False}

        list_after_resp = client.get("/api/v1/blocked", headers=headers)
        usernames_after = [u["username"] for u in list_after_resp.get_json()["users"]]
        assert target["username"] not in usernames_after

        with app.app_context():
            sb = get_sb()
            sb.table("blocks").delete().eq("blocker_id", me["id"]).eq("blocked_id", target["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", me["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", target["id"]).execute()

    def test_blocking_removes_mutual_follow(self, app, client, test_user_factory):
        """toggle_block()'ın yan etkisi: engelleyince her iki yöndeki takip de kopar."""
        me = test_user_factory(email="apiv1_block_follow_me@example.com", password="TestPass123!")
        target = test_user_factory(email="apiv1_block_follow_target@example.com", password="TestPass123!")
        token_me = _api_token_for(app, me["id"])
        token_target = _api_token_for(app, target["id"])

        # Karşılıklı takip kur
        client.post(
            f"/api/v1/profile/{target['username']}/follow",
            headers={"Authorization": f"Bearer {token_me}"},
        )
        client.post(
            f"/api/v1/profile/{me['username']}/follow",
            headers={"Authorization": f"Bearer {token_target}"},
        )

        client.post(
            f"/api/v1/block/{target['username']}",
            headers={"Authorization": f"Bearer {token_me}"},
        )

        with app.app_context():
            sb = get_sb()
            follows = sb.table("follows").select("follower_id, following_id").or_(
                f"and(follower_id.eq.{me['id']},following_id.eq.{target['id']}),"
                f"and(follower_id.eq.{target['id']},following_id.eq.{me['id']})"
            ).execute().data
            assert follows == []

            sb.table("blocks").delete().eq("blocker_id", me["id"]).eq("blocked_id", target["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", me["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", target["id"]).execute()


def _insert_token_with_device(app, user_id, device_name):
    """_api_token_for()'ın device_name'li versiyonu — Aktif Oturumlar
    testlerinde birden fazla "cihaz" simüle etmek için."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with app.app_context():
        row = get_sb().table("api_tokens").insert({
            "user_id": user_id, "token_hash": token_hash, "device_name": device_name,
        }).execute().data[0]
    return raw_token, row["id"]


class TestApiV1Sessions:
    """GET /api/v1/sessions + POST /sessions/<id>/revoke + /sessions/revoke-others
    — api_tokens üzerinde çalışır (web'in user_sessions'ından FARKLI mekanizma,
    bkz. api_v1.py bölüm başı yorumu)."""

    def test_lists_only_own_active_sessions_with_correct_is_current(
        self, app, client, test_user_factory
    ):
        user = test_user_factory(email="apiv1_sessions_list@example.com", password="TestPass123!")
        other_user = test_user_factory(email="apiv1_sessions_other_user@example.com", password="TestPass123!")
        token_a, id_a = _insert_token_with_device(app, user["id"], "Pixel 8")
        token_b, id_b = _insert_token_with_device(app, user["id"], "Samsung S21")
        _other_token, _other_id = _insert_token_with_device(app, other_user["id"], "Başkasının Telefonu")

        resp = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        sessions = resp.get_json()["sessions"]
        ids = {s["id"] for s in sessions}
        assert ids == {id_a, id_b}  # sadece KENDİ cihazları, başkasınınki yok
        by_id = {s["id"]: s for s in sessions}
        assert by_id[id_a]["is_current"] is True
        assert by_id[id_b]["is_current"] is False
        assert by_id[id_a]["device_name"] == "Pixel 8"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", other_user["id"]).execute()

    def test_cannot_revoke_someone_elses_session(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_sessions_forbid_a@example.com", password="TestPass123!")
        other_user = test_user_factory(email="apiv1_sessions_forbid_b@example.com", password="TestPass123!")
        token_a, _id_a = _insert_token_with_device(app, user["id"], "Cihaz A")
        _token_b, id_b = _insert_token_with_device(app, other_user["id"], "Başkasının Cihazı")

        resp = client.post(f"/api/v1/sessions/{id_b}/revoke", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "forbidden"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("api_tokens").delete().eq("user_id", other_user["id"]).execute()

    def test_cannot_revoke_own_current_session_via_this_endpoint(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_sessions_selfrevoke@example.com", password="TestPass123!")
        token_a, id_a = _insert_token_with_device(app, user["id"], "Cihaz A")

        resp = client.post(f"/api/v1/sessions/{id_a}/revoke", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "use_logout"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_revoke_other_device_removes_it_from_list_and_invalidates_its_token(
        self, app, client, test_user_factory
    ):
        user = test_user_factory(email="apiv1_sessions_revoke@example.com", password="TestPass123!")
        token_a, id_a = _insert_token_with_device(app, user["id"], "Cihaz A")
        token_b, id_b = _insert_token_with_device(app, user["id"], "Cihaz B")
        headers_a = {"Authorization": f"Bearer {token_a}"}

        revoke_resp = client.post(f"/api/v1/sessions/{id_b}/revoke", headers=headers_a)
        assert revoke_resp.status_code == 200
        assert revoke_resp.get_json()["ok"] is True

        list_resp = client.get("/api/v1/sessions", headers=headers_a)
        ids = {s["id"] for s in list_resp.get_json()["sessions"]}
        assert ids == {id_a}

        # İptal edilen token artık GERÇEKTEN geçersiz mi (kendi isteğinde de)
        me_resp_b = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"})
        assert me_resp_b.status_code == 401

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_revoke_others_keeps_current_and_invalidates_rest(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_sessions_revokeothers@example.com", password="TestPass123!")
        token_a, id_a = _insert_token_with_device(app, user["id"], "Cihaz A")
        token_b, id_b = _insert_token_with_device(app, user["id"], "Cihaz B")
        token_c, id_c = _insert_token_with_device(app, user["id"], "Cihaz C")
        headers_a = {"Authorization": f"Bearer {token_a}"}

        resp = client.post("/api/v1/sessions/revoke-others", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        list_resp = client.get("/api/v1/sessions", headers=headers_a)
        ids = {s["id"] for s in list_resp.get_json()["sessions"]}
        assert ids == {id_a}

        # A hâlâ çalışıyor, B ve C GERÇEKTEN iptal edildi
        assert client.get("/api/v1/auth/me", headers=headers_a).status_code == 200
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"}
        ).status_code == 401
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_c}"}
        ).status_code == 401

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()


class TestApiV1HashtagTrending:
    """GET /api/v1/hashtag/<tag> + POST /hashtag/<tag>/follow + GET /trending
    — hashtags.py hashtag_posts()/toggle_hashtag_follow()/_trending_hashtags()'in
    JSON API karşılığı (Faz 4 sonrası eksik giderme, native Android gündem/hashtag
    sayfası). Post şekli /api/v1/feed ile AYNI sözleşme (_attach_post_metrics/
    attach_polls çıktısı) — ayrı bir serialization testi YAPILMIYOR, sadece
    kritik alanların (like_count/comment_count/liked_by_me) varlığı doğrulanır."""

    def test_hashtag_posts_without_token_returns_401(self, client):
        resp = client.get("/api/v1/hashtag/deneme")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_hashtag_posts_shows_real_post_with_that_tag(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_hashtag_posts@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}
        tag = "apiv1hashtagposttest"

        create_resp = client.post(
            "/api/v1/posts",
            data={"content": f"api_v1 hashtag sayfası testi #{tag}"},
            headers=headers,
        )
        assert create_resp.status_code == 200
        post_id = create_resp.get_json()["post"]["id"]

        resp = client.get(f"/api/v1/hashtag/{tag}", headers=headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "posts" in body and "is_following" in body
        assert body["is_following"] is False
        matched = next((p for p in body["posts"] if p["id"] == post_id), None)
        assert matched is not None
        # /api/v1/feed ile AYNI sözleşme (_attach_post_metrics çıktısı)
        assert "like_count" in matched and "comment_count" in matched and "liked_by_me" in matched

        # Büyük/küçük harf duyarsız (hashtag_posts()'daki AYNI davranış: tag.lower())
        resp_upper = client.get(f"/api/v1/hashtag/{tag.upper()}", headers=headers)
        assert resp_upper.status_code == 200
        assert any(p["id"] == post_id for p in resp_upper.get_json()["posts"])

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("post_hashtags").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_hashtag_posts_unknown_tag_returns_empty_list_not_error(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_hashtag_unknown@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/v1/hashtag/hicbirzamankullanilmayacakbiretiket999", headers=headers)
        assert resp.status_code == 200
        assert resp.get_json() == {"posts": [], "is_following": False}

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()

    def test_toggle_hashtag_follow_flips_state(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_hashtag_follow@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}
        tag = "apiv1hashtagfollowtest"

        follow_resp = client.post(f"/api/v1/hashtag/{tag}/follow", headers=headers)
        assert follow_resp.status_code == 200
        assert follow_resp.get_json()["following"] is True

        # is_following artık gerçekten True (hashtag_posts sözleşmesiyle doğrulanır)
        check_resp = client.get(f"/api/v1/hashtag/{tag}", headers=headers)
        assert check_resp.get_json()["is_following"] is True

        unfollow_resp = client.post(f"/api/v1/hashtag/{tag}/follow", headers=headers)
        assert unfollow_resp.status_code == 200
        assert unfollow_resp.get_json()["following"] is False

        check_resp2 = client.get(f"/api/v1/hashtag/{tag}", headers=headers)
        assert check_resp2.get_json()["is_following"] is False

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("hashtag_follows").delete().eq("user_id", user["id"]).execute()
            sb.table("hashtags").delete().eq("tag", tag).execute()

    def test_trending_returns_200_and_includes_freshly_created_hashtag(
        self, app, client, test_user_factory
    ):
        """Cache-bypass kararı: ayrı bir cache-atlama mekanizması İCAT EDİLMEDİ —
        api_create_post() zaten içerikli her post oluşturulduğunda
        invalidate("trending:") çağırıyor (api_v1.py api_create_post(), web'in
        create_post()'uyla AYNI yan etki). Bu yüzden testte post oluşturmak
        120sn'lik TTL cache'i kendiliğinden temizliyor ve hemen ardından gelen
        /trending isteği TAZE hesaplanıyor — ayrı bir bypass'a gerek kalmadı."""
        user = test_user_factory(email="apiv1_trending@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}
        tag = "apiv1trendingtesttag"

        create_resp = client.post(
            "/api/v1/posts",
            data={"content": f"api_v1 gündem testi #{tag}", "visibility": "public"},
            headers=headers,
        )
        assert create_resp.status_code == 200
        post_id = create_resp.get_json()["post"]["id"]

        resp = client.get("/api/v1/trending", headers=headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "tags" in body and isinstance(body["tags"], list)
        assert any(t["tag"] == tag for t in body["tags"])

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", user["id"]).execute()
            sb.table("post_hashtags").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()


class TestApiV1Repost:
    def test_repost_without_token_returns_401(self, client):
        resp = client.post("/api/v1/posts/nonexistent/repost")
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_repost_creates_new_post_and_notifies_author(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_repost_author@example.com", password="TestPass123!")
        reposter = test_user_factory(email="apiv1_repost_reposter@example.com", password="TestPass123!")
        token = _api_token_for(app, reposter["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 repost testi için orijinal post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        resp = client.post(f"/api/v1/posts/{post_id}/repost", json={}, headers=headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        repost_id = body["post_id"]

        with app.app_context():
            sb = get_sb()
            repost_row = sb.table("posts").select("repost_of_id, user_id, content").eq(
                "id", repost_id
            ).execute().data[0]
            assert repost_row["repost_of_id"] == post_id
            assert repost_row["user_id"] == reposter["id"]

            notif = sb.table("notifications").select("id").eq(
                "recipient_id", author["id"]
            ).eq("type", "repost").eq("post_id", post_id).execute().data
            assert notif

        # Aynı orijinali içeriksiz ikinci kez repost etmek 409 döner
        dup_resp = client.post(f"/api/v1/posts/{post_id}/repost", json={}, headers=headers)
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"] == "already_reposted"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", reposter["id"]).execute()
            sb.table("notifications").delete().eq("post_id", post_id).execute()
            sb.table("posts").delete().eq("id", repost_id).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_repost_of_private_account_post_returns_403(self, app, client, test_user_factory):
        owner = test_user_factory(email="apiv1_repost_priv_owner@example.com", password="TestPass123!")
        reposter = test_user_factory(email="apiv1_repost_priv_reposter@example.com", password="TestPass123!")
        token = _api_token_for(app, reposter["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            sb.table("profiles").update({"is_private": True}).eq("id", owner["id"]).execute()
            post_row = sb.table("posts").insert({
                "user_id": owner["id"],
                "content": "gizli hesap postu",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        resp = client.post(f"/api/v1/posts/{post_id}/repost", json={}, headers=headers)
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "private_account"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", reposter["id"]).execute()
            sb.table("profiles").update({"is_private": False}).eq("id", owner["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()


class TestApiV1Report:
    def test_report_without_token_returns_401(self, client):
        resp = client.post("/api/v1/report", json={"target_type": "post", "target_id": "x"})
        assert resp.status_code == 401
        assert resp.get_json().get("error") == "unauthorized"

    def test_report_post_then_duplicate_returns_409(self, app, client, test_user_factory):
        author = test_user_factory(email="apiv1_report_author@example.com", password="TestPass123!")
        reporter = test_user_factory(email="apiv1_report_reporter@example.com", password="TestPass123!")
        token = _api_token_for(app, reporter["id"])
        headers = {"Authorization": f"Bearer {token}"}

        with app.app_context():
            sb = get_sb()
            post_row = sb.table("posts").insert({
                "user_id": author["id"],
                "content": "api_v1 şikayet testi için post",
                "visibility": "public",
                "is_draft": False,
                "is_archived": False,
            }).execute().data[0]
        post_id = post_row["id"]

        resp = client.post(
            "/api/v1/report",
            json={"target_type": "post", "target_id": post_id},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        dup_resp = client.post(
            "/api/v1/report",
            json={"target_type": "post", "target_id": post_id},
            headers=headers,
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"] == "already_reported"

        with app.app_context():
            sb = get_sb()
            sb.table("api_tokens").delete().eq("user_id", reporter["id"]).execute()
            sb.table("reports").delete().eq("reporter_id", reporter["id"]).execute()
            sb.table("posts").delete().eq("id", post_id).execute()

    def test_report_invalid_target_type_returns_400(self, app, client, test_user_factory):
        user = test_user_factory(email="apiv1_report_invalid@example.com", password="TestPass123!")
        token = _api_token_for(app, user["id"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/v1/report",
            json={"target_type": "not_a_type", "target_id": "x"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_request"

        with app.app_context():
            get_sb().table("api_tokens").delete().eq("user_id", user["id"]).execute()
