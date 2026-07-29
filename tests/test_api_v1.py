"""JSON REST API (Faz 1, native Android yol haritası) testleri.

Gerçek Supabase test kullanıcısıyla çalışır (mock yok, test_user_factory
fixture'ı bkz. tests/conftest.py) — auth/token akışı güvenlik-kritik.
"""
import hashlib
import secrets

import pytest

from app.supabase_client import get_sb


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

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": viewer["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": searcher["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]
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
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]

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
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "TestPass123!"},
        )
        token = login_resp.get_json()["token"]
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
