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
            json={"content": "api_v1 mesajlaşma testi - merhaba"},
            headers=headers_a,
        )
        assert send_resp.status_code == 200
        sent = send_resp.get_json()["message"]
        assert sent["content"] == "api_v1 mesajlaşma testi - merhaba"
        assert sent["sender_id"] == user_a["id"]

        # reply_to_id ile B'den cevap
        reply_resp = client.post(
            f"/api/v1/messages/conversations/{cid}/send",
            json={"content": "api_v1 mesajlaşma testi - cevap", "reply_to_id": sent["id"]},
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
            json={"content": "izinsiz mesaj"},
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
            json={"content": "api_v1 okundu testi"},
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
                json={"content": f"rl-mesaj-{i}"},
                headers=headers_a,
            )
            statuses.append(resp.status_code)

        assert statuses[:30] == [200] * 30
        assert statuses[30] == 429

        _cleanup_conversation(app, cid, user_a["id"], user_b["id"])


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
