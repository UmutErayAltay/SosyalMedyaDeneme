# Native Android API — Web Özellik Karşılaştırması

**Otomatik oluşturuldu: 2026-07-31**

Sosyal medya uygulamasında web sitesi (session-cookie tabanlı Jinja2 render) ile native Android uygulaması (Bearer token JSON API) arasında işlevsellik farkını anlatır. URL'ler birebir aynı olmak zorunda değil; işlevsellik/işlem bazında karşılaştırma yapılmıştır.

---

## ✅ Native'e Eklenenler (api_v1'de karşılığı var)

### Kimlik Doğrulama
- **Kayıt olma** — web: `app/auth.py:register()`, native: `app/api_v1/auth.py:api_register()` (`/auth/register` POST)
- **Giriş yapma** — web: `app/auth.py:login()`, native: `app/api_v1/auth.py:api_login()` (`/auth/login` POST)
- **Google OAuth girişi** — web: `app/auth.py:auth_google_callback()` + `auth_google_complete()`, native: `app/api_v1/auth.py:api_google_auth()` (`/auth/google` POST)
- **Çıkış yapma** — web: `app/auth.py:logout()`, native: `app/api_v1/auth.py:api_logout()` (`/auth/logout` POST)
- **Beni getir (profil bilgisi)** — web: session'dan okunur, native: `app/api_v1/auth.py:api_me()` (`/auth/me`)
- **Realtime token (WebSocket)** — web: `app/auth.py:get_realtime_token()`, native: `app/api_v1/auth.py:get_realtime_token()` (`/realtime-token`)
- **Token senkronizasyonu** — web: `app/auth.py:sync_tokens()` (`/auth/sync-tokens` POST), native: —

### 2FA (İki Faktörlü Doğrulama)
- **2FA durumu kontrolü** — web: session'dan, native: `app/api_v1/twofa.py:api_twofa_status()` (`/2fa/status`)
- **2FA kaydı başlatma** — web: `app/auth.py:enroll_2fa()`, native: `app/api_v1/twofa.py:api_enroll_2fa()` (`/2fa/enroll` POST)
- **2FA kaydı doğrulama** — web: `app/auth.py:verify_2fa_enroll()`, native: `app/api_v1/twofa.py:api_verify_2fa_enroll()` (`/2fa/enroll/verify` POST)
- **2FA devre dışı bırakma** — web: `app/auth.py:disable_2fa()`, native: `app/api_v1/twofa.py:api_disable_2fa()` (`/2fa/disable` POST)
- **2FA doğrulama (giriş sırasında)** — web: `app/auth.py:verify_2fa()` (ayrı adım/route), native: **AYRI bir endpoint DEĞİL** — `/auth/login` (ve `/auth/google`) şifre doğruysa VE `code` alanı eksikse `403 mfa_required` döner, client aynı isteği `code` ile tekrar atar (bkz. `app/api_v1/auth.py` docstring'i) — işlevsel olarak KARŞILANIYOR, sadece ayrı bir endpoint yerine login akışına gömülü

### Feed + Keşfet
- **Ana akış (feed)** — web: `app/routes/posts.py:feed()`, native: `app/api_v1/feed.py:api_feed()` (`/feed`)
- **Keşfet (algoritmik)** — web: `app/routes/discovery.py:explore()` (`/kesfet`), native: `app/api_v1/feed.py:api_discover()` (`/discover`)
- **Arama** — web: `app/routes/discovery.py:search()`, native: `app/api_v1/feed.py:api_search()` (`/search`)
- **Arama geçmişi kayıt** — web: `app/routes/discovery.py:save_search()`, native: `app/api_v1/feed.py:api_save_search()` (`/search/save` POST)
- **Arama geçmişi silme (tümü)** — web: `app/routes/discovery.py:clear_search_history()`, native: `app/api_v1/feed.py:api_clear_search_history()` (`/search/history/clear` POST)
- **Arama geçmişi silme (tek öğe)** — web: `app/routes/discovery.py:delete_search_history()`, native: `app/api_v1/feed.py:api_delete_search_history_item()` (`/search/history/<item_id>/delete` POST)
- **Kaydedilen aramaları silme** — web: `app/routes/discovery.py:delete_saved_search()`, native: `app/api_v1/feed.py:api_delete_saved_search_item()` (`/search/saved/<item_id>/delete` POST)

### Post İşlemleri (Temel)
- **Post oluşturma** — web: `app/routes/posts.py:create_post()`, native: `app/api_v1/interactions.py:api_create_post()` (`/posts` POST)
  - Native sınırlaması: **TEK görsel** (web: 4'e kadar), **metin sadece**, video/GIF/anket desteklemez
- **Post detayı getir (+ yorumlar)** — web: `app/routes/posts.py:post_detail()`, native: `app/api_v1/interactions.py:api_post_detail()` (`/posts/<post_id>`)
- **Beğeni / Tepki** — web: `app/social.py:toggle_like()`, native: `app/api_v1/interactions.py:api_toggle_like()` (`/posts/<post_id>/like` POST)
  - Tepki türleri (like, love, haha, wow, sad) AYNI

### Yorum İşlemleri (Temel)
- **Yorum ekleme + Yanıt** — web: `app/social.py:add_comment()` / `reply_comment()`, native: `app/api_v1/interactions.py:api_add_comment()` (`/posts/<post_id>/comments` POST)
  - Native sınırlaması: **Metin sadece** (web: sticker/GIF yorum da var)
  - Yanıt: JSON body'de `parent_comment_id` alanıyla belirtilir

### Profil
- **Profil görüntüleme** — web: `app/routes/profile.py:profile()` (`/u/<username>`), native: `app/api_v1/profile.py:api_profile()` (`/profile/<username>`)
- **Takipçiler listesi** — web: `app/routes/profile.py:followers()`, native: `app/api_v1/profile.py:api_followers()` (`/profile/<username>/followers`)
- **Takip edilen listesi** — web: `app/routes/profile.py:following()`, native: `app/api_v1/profile.py:api_following()` (`/profile/<username>/following`)
- **İstatistikler (profil verisi içinde)** — web: `app/routes/profile.py:insights()`, native: `app/api_v1/profile.py:api_insights()` (`/profile/insights`)
- **Profil düzenleme** — web: `app/routes/profile.py:edit_profile()`, native: `app/api_v1/settings.py:api_edit_profile()` (`/profile/edit` POST)
- **Hesap deaktivasyonu** — web: `app/routes/profile.py:deactivate_account()`, native: `app/api_v1/settings.py:api_deactivate_account()` (`/profile/deactivate` POST)

### Takip
- **Takip etme/bırakma** — web: `app/social.py:toggle_follow()`, native: `app/api_v1/profile.py:api_toggle_follow()` (`/profile/<username>/follow` POST)
- **Takip istekleri listesi** — web: `app/social.py:follow_requests()`, native: `app/api_v1/profile.py:api_follow_requests()` (`/follow-requests`)
- **Takip isteği kabul etme** — web: `app/social.py:accept_follow_request()`, native: `app/api_v1/profile.py:api_accept_follow_request()` (`/follow-requests/<follower_id>/accept` POST)
- **Takip isteği reddetme** — web: `app/social.py:reject_follow_request()`, native: `app/api_v1/profile.py:api_reject_follow_request()` (`/follow-requests/<follower_id>/reject` POST)

### Mesajlaşma (1:1, Grup)
- **Sohbet listesi (konuşmalar)** — web: `app/messaging/views.py:conversations()`, native: `app/api_v1/messaging.py:api_conversations()` (`/messages/conversations`)
- **Sohbet açma** — web: `app/messaging/views.py:conversation_view()`, native: `app/api_v1/messaging.py:api_conversation_view()` (`/messages/conversations/<conversation_id>`)
- **Mesaj gönderme** — web: `app/messaging/sending.py:send_message()`, native: `app/api_v1/messaging.py:api_send_message()` (`/messages/conversations/<conversation_id>/send` POST)
- **1:1 sohbet başlatma** — web: `app/messaging/creation.py:start_direct_message()`, native: `app/api_v1/messaging.py:api_start_conversation()` (`/messages/start/<username>` POST)
- **Sohbet mesajları okundu işaretle** — web: `app/messaging/views.py:mark_read()`, native: `app/api_v1/messaging.py:api_mark_read()` (`/messages/conversations/<conversation_id>/mark-read` POST)
- **Grup sohbet oluşturma** — web: `app/messaging/creation.py:create_group()`, native: `app/api_v1/messaging.py:api_create_group()` (`/messages/group/new` POST)
- **Grup adı değiştirme** — web: `app/messaging/group_admin.py:rename_group()`, native: `app/api_v1/messaging.py:api_rename_group()` (`/messages/group/<conversation_id>/rename` POST)
- **Grup üye listeleme** — web: template'te inline, native: `app/api_v1/messaging.py:api_group_members()` (`/messages/group/<conversation_id>/members`)
- **Grup üye ekleme** — web: `app/messaging/group_admin.py:add_member()`, native: `app/api_v1/messaging.py:api_add_member()` (`/messages/group/<conversation_id>/members/add` POST)
- **Grup üye kaldırma** — web: `app/messaging/group_admin.py:remove_member()`, native: `app/api_v1/messaging.py:api_remove_member()` (`/messages/group/<conversation_id>/members/<user_id>/remove` POST)
- **Grup yönetici toggle** — web: `app/messaging/group_admin.py:toggle_admin()`, native: `app/api_v1/messaging.py:api_toggle_admin()` (`/messages/group/<conversation_id>/members/<user_id>/toggle-admin` POST)
- **Gruptan ayrılma** — web: `app/messaging/group_admin.py:leave_group()`, native: `app/api_v1/messaging.py:api_leave_group()` (`/messages/group/<conversation_id>/leave` POST)

### Bildirimler
- **Bildirim listesi** — web: `app/notifications.py:notifications()`, native: `app/api_v1/notifications.py:api_notifications()` (`/notifications`)
- **Bildirim tercihleri (GET/POST)** — web: `app/notifications.py:notification_preferences()`, native: `app/api_v1/settings.py:api_notification_preferences()` (`/notifications/preferences` GET/POST)
- **Okunmamış bildirim sayısı** — web: `app/notifications.py:unread_count()`, native: `app/api_v1/notifications.py:api_unread_count()` (`/notifications/unread-count`)

### Hashtag + Gündem
- **Hashtag sayfası** — web: `app/hashtags.py:hashtag_page()`, native: `app/api_v1/hashtags.py:api_hashtag_page()` (`/hashtag/<tag>`)
- **Hashtag takip etme/bırakma** — web: `app/hashtags.py:toggle_follow_hashtag()`, native: `app/api_v1/hashtags.py:api_toggle_follow_hashtag()` (`/hashtag/<tag>/follow` POST)
- **Gündem (trending hashtags)** — web: `app/hashtags.py:trending()` (`/gundem`), native: `app/api_v1/hashtags.py:api_trending()` (`/trending`)

### Engelleme
- **Kullanıcı engelleme** — web: `app/blocks.py:block_user()`, native: `app/api_v1/blocks.py:api_block_user()` (`/block/<username>` POST)
- **Engellenenler listesi** — web: `app/blocks.py:blocked_list()`, native: `app/api_v1/blocks.py:api_blocked_list()` (`/blocked`)

### Yakın Arkadaşlar
- **Yakın arkadaşlar listesi** — web: `app/close_friends.py:view_close_friends()`, native: `app/api_v1/settings.py:api_close_friends()` (`/close-friends`)
- **Yakın arkadaş ekleme** — web: `app/close_friends.py:add_close_friend()`, native: `app/api_v1/settings.py:api_add_close_friend()` (`/close-friends/add` POST)
- **Yakın arkadaş kaldırma** — web: `app/close_friends.py:remove_close_friend()`, native: `app/api_v1/settings.py:api_remove_close_friend()` (`/close-friends/<user_id>/remove` POST)

### Oturumlar (Aktif Cihazlar)
- **Oturumlar listesi** — web: `app/routes/profile.py:sessions()`, native: `app/api_v1/sessions.py:api_sessions()` (`/sessions`)
- **Oturum iptal etme (tek)** — web: `app/routes/profile.py:revoke_session()`, native: `app/api_v1/sessions.py:api_revoke_session()` (`/sessions/<session_id>/revoke` POST)
- **Tüm diğer oturumları iptal etme** — web: `app/routes/profile.py:revoke_other_sessions()`, native: `app/api_v1/sessions.py:api_revoke_others()` (`/sessions/revoke-others` POST)

### Reels
- **Reels sayfası** — web: `app/routes/reels.py:reels()`, native: `app/api_v1/reels.py:api_reels()` (`/reels`)

---

## ❌ Native'e Eklenmeyenler (Web'de var, Native'de YOK)

### Post İşlemleri (Gelişmiş)
- **Post düzenleme** — web: `app/routes/posts.py:edit_post()` (`/post/<post_id>/edit` POST)
  - Durum: Native'de henüz YAZILMADI
- **Post silme** — web: `app/routes/posts.py:delete_post()` (`/post/<post_id>/delete` POST)
  - Durum: Native'de henüz YAZILMADI
- **Repost (boost/quote)** — web: `app/routes/posts.py:create_repost()` (`/post/<post_id>/repost` POST)
  - Durum: Native'de henüz YAZILMADI
- **Post arşivleme** — web: `app/routes/posts.py:toggle_archive()` (`/post/<post_id>/archive` POST)
  - Durum: Native'de henüz YAZILMADI
- **Taslak listesi** — web: `app/routes/posts.py:drafts_list()` (`/taslaklar`)
  - Durum: Native'de henüz YAZILMADI (taslak oluşturma yok)
- **Taslağı yayınla** — web: `app/routes/posts.py:publish_draft()` (`/post/<post_id>/publish` POST)
  - Durum: Native'de henüz YAZILMADI
- **Post sabitleme (profilde)** — web: `app/routes/posts.py:toggle_pin()` (`/post/<post_id>/pin` POST)
  - Durum: Native'de henüz YAZILMADI

### Post Oluşturma (Gelişmiş Özellikler)
- **Çoklu görsel (4'e kadar)** — web: `app/routes/posts.py:create_post()` (`/post/new` POST)
  - Durum: Native API `/posts` POST SADECE tek görsel destekler
- **Video/Reel oluşturma** — web: `app/routes/posts.py:create_post()` (reel seçeneği)
  - Durum: Native'de henüz YAZILMADI
- **GIF paylaşma** — web: `app/routes/posts.py:create_post()` (GIF URL alanı)
  - Durum: Native'de henüz YAZILMADI
- **Anket oluşturma** — web: `app/routes/posts.py:create_post()` (poll_option_1-4)
  - Durum: Partial — post oluştururken native `/posts` POST'ta anket alanları yok
- **Konum ekleme** — web: `app/routes/posts.py:create_post()` (location_name/lat/lng)
  - Durum: Native'de henüz YAZILMADI
- **Planlanmış post (scheduled_at)** — web: `app/routes/posts.py:create_post()` (action=schedule)
  - Durum: Native'de henüz YAZILMADI

### Yorum İşlemleri (Gelişmiş)
- **Yorum silme** — web: `app/social.py:delete_comment()` (`/comment/<comment_id>/delete` POST)
  - Durum: Native'de henüz YAZILMADI
- **Yorum beğenme** — web: `app/social.py:toggle_comment_like()` (`/comment/like/<comment_id>` POST)
  - Durum: Native'de henüz YAZILMADI
- **Yorum tepkisi** — web: `app/social.py:comment_react()` (`/comment/<comment_id>/react` POST)
  - Durum: Native'de henüz YAZILMADI
- **Sticker yorum** — web: `app/social.py:add_comment()` (sticker_id alanı)
  - Durum: Native `api_add_comment()` SADECE metin destekler, sticker YAZILMADI
- **GIF yorum** — web: `app/social.py:add_comment()` (gif_url alanı)
  - Durum: Native `api_add_comment()` SADECE metin destekler, GIF YAZILMADI

### Yer İşareti (Bookmarks) + Koleksiyonlar
- **Yer işareti ekleme** — web: `app/social.py:bookmark_post()` (`/bookmark/<post_id>` POST)
  - Durum: Native'de henüz YAZILMADI
  - Not: Native profil endpoint'i bookmark'ı OKUYOR ama yazma (POST) yok
- **Yer işareti kaldırma** — web: `app/social.py:bookmark_post()` (toggle)
  - Durum: Native'de henüz YAZILMADI
- **Yer işareti koleksiyonlarına ekle** — web: `app/social.py:add_to_collection()` (`/bookmark/<post_id>/collection` POST)
  - Durum: Native'de henüz YAZILMADI
- **Koleksiyonlar listesi** — web: `app/social.py:collections()` (`/collections`)
  - Durum: Native'de henüz YAZILMADI
- **Koleksiyon oluşturma** — web: `app/social.py:create_collection()` (`/collections/new` POST)
  - Durum: Native'de henüz YAZILMADI
- **Koleksiyon silme** — web: `app/social.py:delete_collection()` (`/collections/<collection_id>/delete` POST)
  - Durum: Native'de henüz YAZILMADI

### Hikayeler (Stories)
- **Hikaye oluşturma** — web: `app/stories.py:create_story()` (`/stories/new` POST)
  - Durum: TAMAMEN yok — native'de `app/api_v1/` içinde Story endpoint'i yok
- **Hikaye görüntüleme (kullanıcının)** — web: `app/stories.py:user_stories()` (`/stories/user/<user_id>`)
  - Durum: TAMAMEN yok
- **Hikaye tepkisi (emoji)** — web: `app/stories.py:react_to_story()` (`/stories/<story_id>/react` POST)
  - Durum: TAMAMEN yok
- **Hikaye yanıtı** — web: `app/stories.py:reply_to_story()` (`/stories/<story_id>/reply` POST)
  - Durum: TAMAMEN yok
- **Hikaye silme** — web: `app/stories.py:delete_story()` (`/stories/<story_id>/delete` POST)
  - Durum: TAMAMEN yok
- **Highlight oluşturma** — web: `app/stories.py:save_highlight()` (`/stories/<story_id>/save-highlight` POST)
  - Durum: TAMAMEN yok
- **Highlights görüntüleme** — web: `app/stories.py:highlights()` (`/stories/highlights/<user_id>`)
  - Durum: TAMAMENTE yok
- **Highlight silme** — web: `app/stories.py:delete_highlight()` (`/stories/highlights/<highlight_id>/delete` POST)
  - Durum: TAMAMEN yok

### Anketler (Polls)
- **Anket oy verme** — web: `app/polls.py:vote_poll()` (`/poll/<poll_id>/vote` POST)
  - Durum: Native'de henüz YAZILMADI
  - Not: Post oluştururken anket seçenekleri eklenebilse de, oy verme endpoint'i yok

### Sessize Alma (Mute)
- **Kullanıcı sessize alma (feed'den gizle)** — web: `app/mutes.py:mute_user()` (`/mute/<user_id>` POST)
  - Durum: Native'de henüz YAZILMADI
- **Post sessize alma** — web: `app/post_mutes.py:mute_post()` (`/post/<post_id>/mute` POST)
  - Durum: Native'de henüz YAZILMADI
- **Sohbet sessize alma** — web: `app/messaging/sending.py:mute_conversation()` (`/<conversation_id>/mute` POST)
  - Durum: Native'de henüz YAZILMADI

### Şikayet Etme (Reports)
- **Post/kullanıcı şikayet etme** — web: `app/reports.py:report_content()` (`/report` POST)
  - Durum: Native'de henüz YAZILMADI
- **Yönetici rapor paneli** — web: `app/admin.py:reports()` (`/admin/reports`)
  - Durum: Kapsam dışı (yönetici paneli mobil app'te gerekmeyebilir)

### Sticker Mesajları
- **Kendi sticker'larım** — web: `app/stickers.py:my_stickers()` (`/stickers/mine` GET)
  - Durum: Native'de henüz YAZILMADI
- **Sticker detayı** — web: `app/stickers.py:sticker_detail()` (`/stickers/<sticker_id>` GET)
  - Durum: Native'de henüz YAZILMADI
- **Sticker oluşturma (upload)** — web: `app/stickers.py:create_sticker()` (`/stickers/new` POST)
  - Durum: Native'de henüz YAZILMADI
- **Sticker kaydetme (favoriler)** — web: `app/stickers.py:save_sticker()` (`/stickers/<sticker_id>/save` POST)
  - Durum: Native'de henüz YAZILMADI
- **Sticker kaldırma** — web: `app/stickers.py:remove_sticker()` (`/stickers/<sticker_id>/remove` POST)
  - Durum: Native'de henüz YAZILMADI

### GIF Arama
- **GIF arama (Klipy API)** — web: `app/gifs.py:search_gifs()` (`/gif/search`)
  - Durum: Native'de henüz YAZILMADI

### Push Bildirimleri (FCM)
- **VAPID public key (web push)** — web: `app/push.py:get_vapid_key()` (`/vapid-public-key`)
  - Durum: Native'de geçerli değil (web push yerine FCM kullanılır)
- **Push subscription ekleme** — web: `app/push.py:subscribe()` (`/subscribe` POST)
  - Durum: Native'de henüz YAZILMADI (native OS push kullanır)
- **Push subscription kaldırma** — web: `app/push.py:unsubscribe()` (`/unsubscribe` POST)
  - Durum: Native'de henüz YAZILMADI

### Diğer Özellikler
- **Mesaj arama (sohbet içinde)** — web: `app/messaging/views.py:search_in_conversation()` (`/<conversation_id>/search`)
  - Durum: Native'de henüz YAZILMADI
- **Tüm sohbetlerde mesaj arama** — web: `app/messaging/views.py:search_messages()` (`/search`)
  - Durum: Native'de henüz YAZILMADI
- **Mesaj silme** — web: `app/messaging/sending.py:delete_message()` (`/message/<message_id>/delete` POST)
  - Durum: Native'de henüz YAZILMADI
- **Mesaj düzenleme** — web: `app/messaging/sending.py:edit_message()` (`/message/<message_id>/edit` POST)
  - Durum: Native'de henüz YAZILMADI
- **Mesaj sabitme** — web: `app/messaging/sending.py:pin_message()` (`/message/<message_id>/pin` POST)
  - Durum: Native'de henüz YAZILMADI
- **Mesaj tepkisi** — web: `app/messaging/reactions.py:react_to_message()` (`/message/<message_id>/react` POST)
  - Durum: Native'de henüz YAZILMADI
- **Post mesaj'a ilet (share)** — web: `app/messaging/sending.py:share_post()` (`/<conversation_id>/share-post/<post_id>` POST)
  - Durum: Native'de henüz YAZILMADI
- **Mesajı ilet** — web: `app/messaging/sending.py:forward_message()` (`/message/<message_id>/forward` POST)
  - Durum: Native'de henüz YAZILMADI
- **Görülür durum (aktif)** — web: `app/messaging/views.py:set_active()` (`/<conversation_id>/active` POST)
  - Durum: Native'de henüz YAZILMADI
- **Sesli/görüntülü arama token'i** — web: `app/messaging/group_calls.py:get_call_token()` (`/<conversation_id>/call-token` POST)
  - Durum: Native'de henüz YAZILMADI (native Android'de arama desteği?)
- **@mention arama** — web: `app/social.py:mention_search()` (`/mentions/search`)
  - Durum: Native'de henüz YAZILMADI

### Yönetici Paneli (Kapsam Dışı)
- **Yönetici anasayfa** — web: `app/admin.py:admin_home()` (`/admin/`)
- **Raporlar (yönetim)** — web: `app/admin.py:admin_reports()` (`/admin/reports`)
- **Rapor çözülmüş işaretle** — web: `app/admin.py:resolve_report()` (`/admin/reports/<report_id>/resolve` POST)
- **İçeriği sil (rapordan)** — web: `app/admin.py:delete_target()` (`/admin/reports/<report_id>/delete-target` POST)
- **Kullanıcı listesi** — web: `app/admin.py:admin_users()` (`/admin/users`)
- **Kullanıcı yönetici yap/kaldır** — web: `app/admin.py:toggle_admin()` (`/admin/users/<user_id>/toggle-admin` POST)
- **Kullanıcı yasakla/aç** — web: `app/admin.py:toggle_ban()` (`/admin/users/<user_id>/toggle-ban` POST)
- **Durum**: Bilinçli olarak kapsam dışı — yönetici paneli mobil app'te gerekli değildir.

### Parola Yönetimi
- **Şifremi unuttum** — web: `app/auth.py:forgot_password()` (`/forgot-password` POST)
  - Durum: Native'de henüz YAZILMADI
- **Şifreyi sıfırla** — web: `app/auth.py:reset_password()` (`/reset-password` POST)
  - Durum: Native'de henüz YAZILMADI

---

## 📊 Özet

### Genel Rakamlar
- **Toplam web özellik alanı**: ~100+ endpoint (27 dosya × 3-5 endpoint ortalama)
- **Native'e taşınanlar**: ~50 endpoint (api_v1 13 dosya)
- **Eksik/ertelenmiş**: ~50 endpoint

### Kategori Bazında
| Kategori | Web | Native | Parity |
|----------|-----|--------|--------|
| Kimlik doğrulama | 8 | 6 | 75% |
| Feed + Keşfet | 7 | 7 | 100% |
| Post (temel) | 3 | 2 | 67% |
| Post (gelişmiş) | 7 | 0 | 0% |
| Yorum | 5 | 1 | 20% |
| Profil | 6 | 6 | 100% |
| Takip | 4 | 4 | 100% |
| Mesajlaşma | 14 | 14 | 100% |
| Bildirimler | 3 | 2 | 67% |
| Hashtag + Gündem | 3 | 3 | 100% |
| Engelleme | 2 | 2 | 100% |
| Yakın arkadaşlar | 3 | 3 | 100% |
| Oturumlar | 3 | 3 | 100% |
| Reels | 1 | 1 | 100% |
| **Eksik kategoriler:** | | | |
| Hikayeler (Stories) | 8 | 0 | 0% |
| Anket (Poll) | 1 | 0 | 0% |
| Yer işareti + Koleksiyonlar | 6 | 0 | 0% |
| Sessize alma (Mute) | 3 | 0 | 0% |
| Şikayet etme (Reports) | 1 | 0 | 0% |
| Sticker mesajları | 5 | 0 | 0% |
| GIF arama | 1 | 0 | 0% |
| Push bildirimleri | 3 | 0 | 0% |
| Mesaj gelişmiş (edit/delete/react) | 5 | 0 | 0% |
| Parola sıfırlama | 2 | 0 | 0% |
| Yönetici paneli | 6 | 0 | 0% (kapsam dışı) |

### Yapılan + Ertelenenler (Faz 4 Sonu — 2026-07-31)
**Yapılan (Faz 4 bitiş sonrası):**
- ✅ Hashtag + trending (commit 764bccd)
- ✅ Aktif oturumlar yönetimi (sessions)
- ✅ Bildirim tercihleri
- ✅ Yakın arkadaşlar
- ✅ 2FA (enroll/verify/disable)

**Ertelenenler (Faz 5 ve sonrası):**
- ❌ Post gelişmiş işlemleri (edit/delete/repost/archive/pin)
- ❌ Yorum gelişmiş işlemleri (delete/like/react/sticker/gif)
- ❌ Hikayeler (tamamen) — bkz. `/context/active_context.md`
- ❌ Anket oy verme
- ❌ Yer işareti + Koleksiyonlar
- ❌ Sessize alma (Mute)
- ❌ Şikayet etme (Reports)
- ❌ Sticker mesajları
- ❌ GIF arama
- ❌ Push bildirimleri (FCM)
- ❌ Mesaj gelişmiş işlemleri (edit/delete/react/pin/forward)

---

## 📝 Notlar
1. **URL biçimi**: Web route'ları `/prefix` (Flask blueprint) kullanır, native `/api/v1/prefix` altında gruplandırılır. İşlevsellik karşılaştırması yapılmıştır.
2. **Sınırlamalar**: Native API, tasarım kararı olarak bazı özellikleri kısmen uygular:
   - Post oluşturma: tek görsel, metin sadece (web: 4 görsel + video + GIF + anket)
   - Yorum: metin sadece (web: sticker/GIF da)
3. **Sticker yorum**: Post detayında (`api_post_detail()`) var olan sticker'ler GÖSTERİLİR, ancak yeni sticker yorum YAZMA henüz yok.
4. **Yönetici paneli**: Bilinçli olarak ertelendi — mobil uygulamada yönetici işlevleri mobil için optimize etme gerekir (farklı UX).
5. **Realtime**: Web + native AYNI `/realtime-token` endpoint'i kullanır (Supabase WebSocket için).
6. **Oturum** (`sessions.py`): Faz 4 sonrası eklendi, aktif cihaz yönetimi için native'de tam parity'si var.

---

**Raporlandığı tarih**: 2026-07-31  
**Araştırma yöntemi**: Grep + pattern match (`@bp.route`) web ve native dosyaları dahil  
**Doğrulama**: Dosya incelemesi (interactions.py, feed.py, profile.py, settings.py, …)
