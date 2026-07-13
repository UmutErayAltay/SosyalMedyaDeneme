---
name: theme-screenshot-sweep
description: Frontend/CSS değişikliği sonrası (özellikle dark mode, tema, layout) görsel doğrulama gerektiğinde kullan. Bu proje light + [data-theme="dark"] emüle edilmiş dark mode destekliyor (app/static/css/style.css); ana sayfalarda (akış/feed, profil, mesajlar, bildirimler) her iki temada da ekran görüntüsü alıp karşılaştırma iş akışını standartlaştırır — daha önce elle yapılmış bir tarama (01-feed-light.png .. 08-messages-emulated-dark.png dosyaları) bu deseni doğruluyor.
---

# Light/dark tema ekran görüntüsü taraması

CSS/layout değişikliğinin gerçekten iki temada da doğru göründüğünü kanıtlamanın standart yolu. Script'e dökülemez çünkü Playwright MCP araçları üzerinden (tarayıcı) yürütülür — bu yüzden burada adım listesi var.

## Adımlar

1. `server-preflight` skill'i ile dev sunucusunu (`run.py`) temiz başlat.
2. Playwright ile gerçek bir kullanıcıyla giriş yap (test kullanıcısı gerekiyorsa `flask-smoke-test`/`rls-migration-verify` script'lerindeki üretim deseniyle bir tane oluştur, işin sonunda sil).
3. Her hedef sayfa için (genelde: akış/feed, profil, mesajlar paneli, bildirimler — proje kökündeki eski `0X-<sayfa>-light.png` adlandırmasıyla tutarlı kal):
   - Sayfaya git, `browser_take_screenshot` ile **light** modda kaydet: `<n>-<sayfa>-light.png`.
   - Dark'ı emüle et: `browser_evaluate` ile `document.documentElement.setAttribute('data-theme', 'dark')` çalıştır (gerçek OS-level `prefers-color-scheme` emülasyonu yerine — proje `[data-theme="dark"]` seçicisini kullanıyor, bkz. `style.css` başındaki not).
   - Tekrar `browser_take_screenshot`: `<n>-<sayfa>-emulated-dark.png`.
   - Bir sonraki sayfaya geçmeden `data-theme` attribute'unu kaldır/`light`'a çevir (sayfa geçişinde SPA değilse zaten sıfırlanır, tam sayfa navigasyonuysa gerek yok).
4. Ekran görüntülerini kullanıcıya sun/karşılaştır; kontrast, okunabilirlik, taşma (overflow) veya unutulmuş sabit renk (hardcoded light-only renk) ara.
5. Geçici test verisi/kullanıcı oluşturduysan temizle; ekran görüntüleri proje köküne değil `scratchpad`'e ya da kullanıcının açıkça istediği yere kaydedilir (proje kökü zaten eski taramalardan gelen PNG'lerle dolu — yeni turda aynı isimlerle KARIŞTIRMA, farklı bir alt klasör kullan).

## Ne zaman kullan

- `app/static/css/style.css` içinde `[data-theme="dark"]` bloğuna dokunan bir değişiklikten sonra.
- Yeni bir sayfa/bileşen eklendiğinde, iki temada da test edilmemişse.

## Ne zaman KULLANMA

- Salt JS mantık değişikliğinde (görsel etkisi yoksa) — gereksiz tur.
