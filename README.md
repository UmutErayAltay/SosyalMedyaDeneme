# sosyal

Arkadaş grubu için geliştirilen küçük ölçekli bir sosyal medya web uygulaması. Feed, profil, mesajlaşma (bireysel + grup, sesli/görüntülü arama, sticker/GIF, emoji tepkileri), bildirimler (web push dahil), hikayeler, anketler, hashtag/keşfet ve arama özelliklerini içerir.

## Teknoloji Yığını

- **Backend:** Flask (Python) — web arayüzü + native Android istemcisi için versioned REST API (`app/api_v1/`, Bearer token auth)
- **Veritabanı / Auth / Storage / Realtime:** Supabase (Postgres)
- **Frontend:** Jinja2 şablonları + vanilla JavaScript (framework yok; `app/static/js/*.js` `npm run build:js` ile `app/static/dist/*.bundle.js`'e derlenir — script'ler tek dosyada birleştirilip esbuild ile minify edilir, gerçek modül bundling'i değil)
- **Gerçek zamanlı iletişim:** Supabase Realtime (mesajlaşma, tepkiler, "yazıyor..." göstergesi) + WebRTC (1:1 sesli/görüntülü arama) + LiveKit (grup sesli/görüntülü arama)
- **Bildirimler:** Web Push (VAPID + Service Worker) + Firebase Cloud Messaging (native Android istemcisi için)
- **Üretim sunucusu:** Waitress; opsiyonel Redis (cache/rate-limit backend'i, `REDIS_URL` ayarlıysa — yoksa bellek-içi fallback)
- **CI/CD:** GitHub Actions (`main`'e her push/PR'da `py_compile` + `node --check` + Jinja parse + gerçek Supabase'e karşı pytest suite)

## Kurulum

### Gereksinimler

- Python 3.11+
- Bir Supabase projesi (URL + API anahtarları)

### Adımlar

```bash
python -m pip install -r requirements.txt
```

Proje kök dizininde bir `.env` dosyası oluştur:

```
FLASK_SECRET_KEY=<rastgele-uzun-bir-string>

SUPABASE_URL=<supabase-proje-url'in>
SUPABASE_PUBLISHABLE_KEY=<supabase-anon/publishable-key>
SUPABASE_SECRET_KEY=<supabase-service-role-key>
SUPABASE_JWKS_URL=<supabase-jwks-url>

# Opsiyonel — yoksa ilgili özellik sessizce devre dışı kalır / fail-open olur
KLIPY_API_KEY=<gif-arama-icin-klipy-api-anahtari>
VAPID_PRIVATE_KEY=<web-push-icin-vapid-private-key>
VAPID_PUBLIC_KEY=<web-push-icin-vapid-public-key>
VAPID_CLAIM_EMAIL=mailto:sen@example.com
REALTIME_TOKEN_ENCRYPTION_KEY=<realtime-token-sifreleme-icin-fernet-anahtari>
REDIS_URL=<opsiyonel-redis-baglanti-dizesi>

# Sadece Playwright E2E testleri (npm run test:e2e) için
E2E_ADMIN_EMAIL=<test-kullanicisi-e-postasi>
E2E_ADMIN_PASSWORD=<test-kullanicisi-sifresi>
```

VAPID anahtar çifti üretmek için:

```bash
python -c "
from py_vapid import Vapid02
import base64
v = Vapid02(); v.generate_keys()
priv = v.private_key.private_numbers().private_value.to_bytes(32, 'big')
pub = v.public_key.public_bytes(
    encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.X962,
    format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PublicFormat']).PublicFormat.UncompressedPoint,
)
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode()
print('VAPID_PRIVATE_KEY=' + b64(priv))
print('VAPID_PUBLIC_KEY=' + b64(pub))
"
```

Veritabanı şeması `sql/migration_*.sql` dosyalarında idempotent olarak tanımlı — Supabase SQL Editor'de sırayla çalıştırılabilir (veya Supabase MCP bağlıysa doğrudan uygulanabilir).

### Çalıştırma

Geliştirme (debug açık, otomatik yeniden yükleme):

```bash
python run.py
```

Üretim (Waitress, debug kapalı):

```bash
python serve.py
```

Uygulama varsayılan olarak `http://0.0.0.0:5000` üzerinde çalışır.

## Proje Yapısı

```
app/
├── __init__.py          # Uygulama fabrikası, blueprint kayıtları, context processor'lar
├── config.py            # .env'den yapılandırma
├── auth.py              # Giriş/kayıt, Google OAuth, oturum yönetimi
├── decorators.py        # login_required vb. ortak decorator'lar
├── routes/              # Web (Jinja2) uçları: feed, post CRUD, profil, keşfet, reels
├── api_v1/              # Native (Android) istemcisi için versioned REST API (Bearer token)
├── messaging/           # Mesajlaşma: oluşturma, gönderme, tepkiler, grup yönetimi, grup aramaları
├── social.py            # Beğeni, yorum, takip, kaydetme
├── notifications.py     # Bildirimler + web push entegrasyonu
├── push.py / fcm.py     # Web Push (VAPID) ve Firebase Cloud Messaging (native) gönderimi
├── stickers.py          # Çıkartmalar
├── gifs.py              # GIF arama proxy'si (Klipy)
├── stories.py           # 24 saatlik hikayeler
├── polls.py             # Anketler
├── hashtags.py          # Hashtag çıkarma + gündem
├── mentions.py          # @kullanıcı bahsetme çıkarma + bildirimi
├── link_preview.py / linkify_utils.py  # Paylaşılan linkleri tıklanabilir yapma + Open Graph önizleme kartı
├── close_friends.py     # Yakın arkadaş listesi
├── blocks.py / mutes.py / post_mutes.py  # Engelleme, kullanıcı/post sessize alma
├── reports.py           # Şikayet
├── memories.py          # "Bugün ne oldu" hatırlatmaları
├── presence.py          # Çevrimiçi/son görülme durumu
├── realtime_session.py / realtime_topics.py  # Supabase Realtime token/kanal yönetimi
├── cache.py / rate_limit.py / redis_client.py  # Redis destekli (yoksa bellek-içi) cache + hız sınırlama
├── storage_helper.py    # Supabase Storage yükleme yardımcıları
├── supabase_client.py   # Servis-rolü Supabase istemcisi (get_sb())
├── user_sessions.py     # Aktif oturum listesi/sonlandırma
├── visibility.py        # Post görünürlük (herkese açık/takipçi) kontrolü
├── admin.py             # Admin paneli
├── templates/           # Jinja2 şablonları (paylaşılan parçalar `_` ile başlar)
└── static/
    ├── js/              # Kaynak JS (sayfa/özellik başına ayrı dosya)
    ├── dist/            # `npm run build:js` çıktısı — template'ler BUNU yükler, js/ değil
    ├── css/style.css    # Tek global stylesheet
    └── sw.js            # Service worker (statik önbellek + web push)

sql/                      # Idempotent migration dosyaları
tests/                    # Kalıcı pytest suite (gerçek Supabase'e karşı, mock yok)
e2e/                      # Playwright uçtan uca testleri
```

## Notlar

- **Test suite:** `tests/` altında kalıcı bir pytest suite var — gerçek Supabase test kullanıcılarıyla çalışır (mock yok), auth/2FA/rate-limit/realtime/WebRTC gibi güvenlik-kritik yolları kapsar. Çalıştırmak için `pip install -r requirements-dev.txt` sonra `python -m pytest tests/ -v`. UI/JS değişiklikleri için ayrıca `npm run test:e2e` (Playwright, gerçek sunucuya karşı, `.env`'de `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` gerekir). `main`'e her push/PR'da GitHub Actions bu pytest suite'ini otomatik çalıştırır (bkz. `.github/workflows/ci.yml`).
- Bu proje küçük bir arkadaş grubu için tasarlanmıştır — güvenlik temel seviyede ele alınmıştır (CSRF koruması, sahiplik kontrolleri, RLS) ama büyük ölçekli/genel kullanım için ek sertleştirme gerekebilir.
- Ayrı bir native Android istemcisi (kardeş repo) bu backend'in `app/api_v1/` altındaki REST API'sini kullanır.
