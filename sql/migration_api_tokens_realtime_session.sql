-- Native app (Faz 5 — gerçek Supabase Realtime desteği) için api_tokens
-- tablosuna, native cihazın kendi opak bearer token'ının YANINDA, periyodik
-- yenilenen bir Supabase Auth oturumunu da saklayacak 3 kolon ekler.
--
-- NEDEN GEREKLİ: Realtime kanalları (örn. messages tablosu postgres_changes)
-- gerçek RLS ile korunuyor — dinlemek için kullanıcının GERÇEK bir Supabase
-- Auth JWT'sine (access_token) ihtiyaç var, anon key veya bu tablonun kendi
-- opak token_hash'i (bkz. migration_api_tokens.sql) bu iş için yeterli değil.
--
-- GÜVENLİK — invaryant korunuyor: migration_api_tokens.sql'in kendi
-- docstring'i "ham token SADECE üretim anında client'a bir kez döner ve DB'de
-- HİÇ tutulmaz" diyor. Bu iki yeni kolon (sb_access_token_enc,
-- sb_refresh_token_enc) PLAINTEXT DEĞİL — uygulama tarafında
-- cryptography.fernet.Fernet ile (DB dışında bir env var'dan gelen anahtarla)
-- şifrelenmiş, base64 metin olarak saklanır. Bir DB dump'ı TEK BAŞINA bu
-- tokenları kullanılabilir kılmaz — şifre çözme anahtarı DB'de değil.
--
-- Kolon adlarında BİLEREK "_enc" son eki var: kodun ileride bu kolonları
-- yanlışlıkla şifresiz metin sanıp decode etmeden DOĞRUDAN kullanmasını
-- zorlaştırmak için.
--
-- sb_token_expires_at şifrelenmemiş (timestamptz) — JWT içeriği şifreli
-- olduğu için exp'i okumak için her seferinde decode etmek yerine, backend
-- yazarken bunu ayrıca düz metin kaydeder; /realtime-token endpoint'i şifre
-- çözmeden bu kolona bakıp yenileme gerekip gerekmediğine karar verebilir.
--
-- Geriye dönük dolgu YOK: mevcut satırlar için varsayılan NULL yeterli,
-- sadece login/register/google-login'in BUNDAN SONRAKİ çağrıları dolduracak.

alter table public.api_tokens
    add column if not exists sb_access_token_enc  text,
    add column if not exists sb_refresh_token_enc text,
    add column if not exists sb_token_expires_at   timestamptz;

-- RLS: mevcut api_tokens politikaları (owner select/delete, auth.uid() ile)
-- row-level olduğu için bu yeni kolonlara da otomatik uygulanır — ek bir
-- politika gerekmiyor. Backend zaten service-role (get_sb()) ile yazıyor/
-- okuyor, RLS'i bypass ediyor; buradaki politikalar defense-in-depth.

NOTIFY pgrst, 'reload schema';

-- ROLLBACK:
-- alter table public.api_tokens
--     drop column if exists sb_access_token_enc,
--     drop column if exists sb_refresh_token_enc,
--     drop column if exists sb_token_expires_at;
