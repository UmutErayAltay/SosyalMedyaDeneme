# Staging/prod Supabase ayrımı — runbook

## Neden gerekli

Şu an geliştirme, test (pytest + Playwright suite'leri — gerçek Supabase'e
karşı çalışıyorlar) ve üretim (`serve.py`) AYNI (tek) Supabase projesini
paylaşıyor. Bunun somut belirtisi: `auth.users` tablosunda haftalardır
birikmiş onlarca test kullanıcısı (`test_*@example.com`,
`*@sosyal-test.local` vb.) — testler gerçek/üretim verisiyle aynı ortamda
çalışıyor demek.

## Neden otomatik yapılamadı

Bu ayrımı kurmak **hesap seviyesinde** bir işlem gerektiriyor (yeni bir
Supabase projesi oluşturmak) — Claude'un elindeki hiçbir MCP aracı
(`apply_migration`, `execute_sql`, `list_tables` vb.) bunu yapamaz, hepsi
VAR OLAN tek proje üzerinde çalışır. Supabase'in "branching" özelliği
(`list_branches`/`create_branch`) da denendi, bu proje için
`"Project reference is missing when validating permissions"` hatasıyla
başarısız oldu — muhtemelen mevcut plan (free tier) branching'i
desteklemiyor.

**Kod tarafı zaten hazır**: `app/config.py` ve `app/supabase_client.py`
hiçbir yerde proje URL'i/anahtarı HARDCODE etmiyor, hepsi `.env`'den
okunuyor (doğrulandı, 2026-07-17). Yani ayrı bir Supabase projesine
geçmek için TEK gereken şey `.env`'deki 3 değeri değiştirmek — kod
değişikliği gerekmiyor.

## Sizin yapmanız gereken adımlar

1. **Yeni (ücretsiz) bir Supabase projesi oluşturun** (dashboard'dan,
   "New Project") — bunu "staging/test" projesi olarak kullanın.
2. **Şemayı senkronize edin**: `sql/` klasöründeki TÜM `migration_*.sql`
   dosyalarını (68+ dosya, `ls sql/migration_*.sql | sort` ile
   kronolojik-ish sırayla) yeni projede çalıştırın — Supabase MCP'nin
   proje referansını (bağlı olduğu proje) staging projesine çevirip
   `apply_migration` ile, ya da doğrudan yeni projenin SQL editor'ünden
   elle. `sql/MIGRATIONS.md`'deki denetim sürecini yeni projede de
   uygulayın (`supabase_migrations.schema_migrations` karşılaştırması).
3. **Yeni projenin bilgilerini alın**: Settings → API → Project URL,
   anon/publishable key, service_role key.
4. **`.env`'i güncelleyin** (yerel geliştirme + testler İÇİN):
   `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`,
   `SUPABASE_JWKS_URL`'i staging projesinin değerleriyle değiştirin.
   Üretim (`serve.py`'nin çalıştığı sunucu) kendi `.env`'inde ESKİ
   (gerçek) proje bilgilerini KORUMALI — iki ortam artık farklı `.env`
   dosyalarına sahip olacak.
5. **GitHub Actions secret'larını güncelleyin** (`SUPABASE_URL`,
   `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`,
   `SUPABASE_JWKS_URL`, `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD`) — CI'daki
   pytest/Playwright artık staging projesine karşı çalışsın, üretime değil.
6. **Staging'de kendi test kullanıcınızı oluşturun** (2FA/discover/vb.
   testlerin kullandığı e-posta+şifre) ve `E2E_ADMIN_EMAIL`/
   `E2E_ADMIN_PASSWORD`'ü buna göre güncelleyin.

## Devam eden maliyet (bilinçli tradeoff)

Bundan sonra YENİ bir migration yazıldığında **iki projeye de**
uygulanması gerekecek (staging + production) — `sql/MIGRATIONS.md`'deki
kayıt/senkron disiplini bu yüzden daha da önemli hale geliyor. Bu, ayrımın
getirdiği gerçek bir operasyonel yük; tek-proje modelinin basitliğinden
vazgeçilmiş oluyor ama karşılığında test/geliştirme artık üretim verisini
kirletmiyor.
