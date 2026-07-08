# sosyal

Arkadaş grubu için geliştirilen küçük ölçekli bir sosyal medya web uygulaması. Feed, profil, mesajlaşma (bireysel + grup, sesli/görüntülü arama, sticker/GIF, emoji tepkileri), bildirimler (web push dahil), hikayeler, anketler, hashtag/keşfet ve arama özelliklerini içerir.

## Teknoloji Yığını

- **Backend:** Flask (Python)
- **Veritabanı / Auth / Storage / Realtime:** Supabase (Postgres)
- **Frontend:** Jinja2 şablonları + vanilla JavaScript (framework/bundler yok)
- **Gerçek zamanlı iletişim:** Supabase Realtime (mesajlaşma, tepkiler, "yazıyor..." göstergesi) + WebRTC (sesli/görüntülü arama)
- **Bildirimler:** Web Push (VAPID + Service Worker)
- **Üretim sunucusu:** Waitress

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

# Opsiyonel — yoksa ilgili özellik sessizce devre dışı kalır
KLIPY_API_KEY=<gif-arama-icin-klipy-api-anahtari>
VAPID_PRIVATE_KEY=<web-push-icin-vapid-private-key>
VAPID_PUBLIC_KEY=<web-push-icin-vapid-public-key>
VAPID_CLAIM_EMAIL=mailto:sen@example.com
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
├── __init__.py         # Uygulama fabrikası, blueprint kayıtları, context processor'lar
├── config.py           # .env'den yapılandırma
├── routes/             # Feed, post yaşam döngüsü, profil, keşfet, arama
├── messaging/          # Mesajlaşma: gönderme, görüntüleme, tepkiler, grup yönetimi
├── social.py           # Beğeni, yorum, takip, kaydetme
├── notifications.py    # Bildirimler + web push entegrasyonu
├── push.py             # Web Push abonelik yönetimi ve gönderimi
├── stickers.py         # Çıkartmalar
├── gifs.py             # GIF arama proxy'si (Klipy)
├── stories.py          # 24 saatlik hikayeler
├── polls.py            # Anketler
├── hashtags.py         # Hashtag çıkarma + gündem
├── close_friends.py    # Yakın arkadaş listesi
├── blocks.py           # Engelleme
├── admin.py            # Admin paneli
├── templates/          # Jinja2 şablonları (paylaşılan parçalar `_` ile başlar)
└── static/
    ├── js/              # Sayfa/özellik başına ayrı dosya
    ├── css/style.css    # Tek global stylesheet
    └── sw.js            # Service worker (statik önbellek + web push)

sql/                     # Idempotent migration dosyaları
```

## Notlar

- Test suite otomatikleştirilmiş değildir; doğrulama Flask `test_client()` betikleri ve gerçek sunucuya karşı manuel testlerle yapılır.
- Bu proje küçük bir arkadaş grubu için tasarlanmıştır — güvenlik temel seviyede ele alınmıştır (CSRF koruması, sahiplik kontrolleri, RLS) ama büyük ölçekli/genel kullanım için ek sertleştirme gerekebilir.
