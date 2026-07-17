# Tek Supabase projesi kararı (staging ayrımı YAPILMAYACAK)

## Karar (2026-07-17)

Bu proje küçük ölçekte (aktif kullanım az) çalıştığı için geliştirme,
test (pytest + Playwright suite'leri) ve üretim **AYNI (tek) Supabase
projesini kullanmaya devam ediyor** — ayrı bir staging/test projesi
KURULMAYACAK. Gerekçe: iki proje arasında şema senkronize tutmanın
(`sql/MIGRATIONS.md`'deki disiplin, HER migration'ı iki yere uygulama)
operasyonel maliyeti, bu ölçekte test verisinin izolasyonundan doğacak
faydadan daha ağır basıyor. Tek projenin risksiz kalması için tek şart:
**test verisi güvenilir şekilde temizlenmesi** — bu zaten mevcut
konvansiyon (`sb.auth.admin.create_user` + fixture cleanup, bkz.
`tests/conftest.py`).

Aşağıdaki (önceki karar döneminden kalan) ayrı-proje planı artık
GEÇERLİ DEĞİL — referans/tarihsel kayıt olarak bırakıldı, uygulanmayacak.

## Test verisi hijyeni — asıl önemli olan bu

- `tests/conftest.py`'deki `test_user_factory` fixture'ı zaten HER testin
  kendi kullanıcısını oluşturup (`sb.auth.admin.create_user`) test sonunda
  sildiğini garantiliyor (`yield` sonrası cleanup) — yeni testler bu
  fixture'ı kullanmaya devam etmeli, kendi ad-hoc create/delete mantığı
  yazılmamalı.
- Playwright E2E suite'i (`e2e/`) kalıcı bir test kullanıcısı KULLANIYOR
  (`.env`'deki `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD`) — bu hesap
  SİLİNMEMELİ, kalıcı test altyapısının bir parçası (login state'i tekrar
  tekrar kullanılıyor).
- Tek seferlik/ad-hoc doğrulama script'leri (scratchpad'te yazılıp silinen
  türden) HER ZAMAN `try/finally` ile temizlik yapmalı — bu oturumda
  yazılan script'lerin hepsi bu deseni izledi.
- **2026-07-17 denetimi**: `auth.users` tablosunda önceki oturumlardan
  (bu disiplin oturt-ulmadan ÖNCE) kalma ~20 kalıntı test hesabı bulundu
  (`test_*@example.com`, `*@sosyal-test.local`, `storytest@test.com`,
  `testuser@test.com` vb.) — bunlar HENÜZ temizlenmedi, kullanıcı onayı
  bekleniyor (bkz. sohbet).

## CI/CD notu

`.github/workflows/ci.yml` her push'ta gerçek (tek) projeye karşı pytest
çalıştırıyor — bu, "test verisi yükleniyor ve düzgün siliniyor" varsayımına
dayanıyor. Fixture cleanup'ın güvenilirliği bu yüzden kritik: bir testin
cleanup adımı sessizce başarısız olursa (örn. exception fixture'ın
`yield` sonrası bloğunda yutulursa) kalıntı birikmeye devam eder.
