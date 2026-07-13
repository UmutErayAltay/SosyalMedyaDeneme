---
name: rls-migration-verify
description: Supabase RLS politikası veya realtime.messages (private broadcast kanal) migration'ı her uygulanacağında/uygulandığında kullan — özellikle calls:/typing:/messages: gibi realtime kanal yetkilendirmesi, veya herhangi bir tabloya yeni RLS policy eklerken. Bu proje canlıda çalışıyor ve bu sınıf değişiklik 2026-07-10'da gerçek bir production kesintisine (CHANNEL_ERROR) yol açmıştı — "kararlılık > özellik hızı" gereği kod değişmeden ÖNCE izole test, SONRA gerçek uygulama koduyla uçtan uca test şart. Kısayol arama, direkt migration uygulayıp app kodunu değiştirme.
---

# RLS / realtime migration doğrulama metodolojisi

Bu proje canlı kullanımda ve bu sınıf değişiklik (RLS policy, private broadcast kanal) daha önce gerçek bir kesintiye yol açtı (Sprint 67, 2026-07-10 — kök neden: `calls:` kanalının SELECT policy'si asimetrikti, arayan taraf hiç JOIN olamıyordu). O yüzden bu adımların HİÇBİRİ atlanmaz.

## Sıra — asla değiştirme

1. **Onay al.** Supabase MCP `apply_migration`, prod şema değişikliği olduğu için tek seferlik açık onay ister. "Her şeyi yap" gibi genel talimatlar bu onayı KARŞILAMAZ — migration dosyasının tam içeriğini `AskUserQuestion` ile göster, "Evet, uygula" onayı bekle.
2. **Migration'ı uygula** (`mcp__supabase__apply_migration`). Idempotent yaz (`drop policy if exists` + `create policy`, sonda `NOTIFY pgrst, 'reload schema';`).
3. **Kod değişmeden ÖNCE izole test.** Uygulama kodunu (chat.js/call.js vb.) HENÜZ değiştirme. 3 taze test kullanıcısı oluştur (`scripts/make_test_users.py --prefix <benzersiz> --conversation`), Playwright ile sırayla giriş yap (Playwright sekmeleri aynı cookie jar'ı paylaşır — GERÇEK eşzamanlı çoklu oturum test edilemez, bu yüzden login/test/logout döngüsüyle sırayla test et):
   - Aynı topic için önce `window.supabaseClient.getChannels().forEach(c => window.supabaseClient.removeChannel(c))` çalıştır (yoksa eski public kanal nesnesi sonucu maskeler).
   - `window.supabaseClient.channel(topic, {config:{broadcast:{self:false}, private:true}}).subscribe(status => ...)` ile gerçek durumu (`SUBSCRIBED` / `CHANNEL_ERROR`) doğrula.
   - Yetkili kullanıcı: `SUBSCRIBED` bekle.
   - Yetkisiz kullanıcı (C): `CHANNEL_ERROR` + "Unauthorized: You do not have permissions..." bekle.
   - `postgres_changes` kullanan bir kanalsa (örn. mesaj teslimi), gerçek bir DB INSERT ile callback'in hâlâ ateşlendiğini doğrula — `private:true` bunu ETKİLEMEMELİ (ayrı yetkilendirme yolu).
4. **Ancak testler geçtikten SONRA** ilgili `channel()` çağrısına `private: true` ekle (app kodu).
5. **Gerçek uygulama koduyla uçtan uca test.** Sentetik script değil, gerçek login formu + gerçek sayfa + gerçek buton/form. Kontrol et:
   - `window.supabaseClient.getChannels()` ilgili topic için `state: "joined"` gösteriyor mu.
   - Konsol tamamen temiz mi (sıfır hata).
   - Herhangi bir fallback/degrade mekanizması tetiklenmemiş mi (örn. chat.js'te `window._chatPollTimer` falsy kalmalı — realtime'ın 4 sn'lik yoklamaya düşmediğinin kanıtı).
6. **`mcp__supabase__get_logs(service="realtime")` kontrol et.** Her "Unauthorized" kaydının kasıtlı negatif teste ait olduğunu doğrula; açıklanamayan kayıt varsa DURUP araştır.
7. **Temizlik.** `scripts/cleanup_test_users.py --prefix <aynı-prefix> --conversation-id <id>` ile test kullanıcı/sohbet/mesajlarını sil. Playwright sekmesini `about:blank`'e al (arka planda reconnect loop'u önler).
8. **Dokümantasyon + commit.** `.context/active_context.md`'ye yeni sprint girdisi (migration, doğrulama adımları, sonuç), sonra tek commit.

## Kritik ayrım — postgres_changes vs broadcast

`realtime.messages` tablosundaki RLS policy'leri SADECE `extension = 'broadcast'` (ve `'presence'`) için geçerlidir. `postgres_changes` (INSERT/UPDATE/DELETE aboneliği) TAMAMEN AYRI bir yetkilendirme yoludur — altındaki tablonun KENDİ RLS'i ile korunur, `private:true` bundan etkilenmez. Bu ikisini karıştırıp "private:true mesaj teslimini bozar mı" diye tereddüt etme — adım 3'teki gerçek INSERT testi bunu her seferinde kanıtlar.

## Ne zaman KULLANMA

- Salt okunur/select sorgu değişikliklerinde (RLS policy eklemeyen) — bu ağır süreç gereksiz.
- `realtime.messages` veya broadcast/presence kanalı içermeyen sıradan tablo migration'larında (örn. yeni sütun ekleme) — sadece adım 1-2 ve normal test yeterli, 3-7 arası bu spesifik riskli sınıf için var.
