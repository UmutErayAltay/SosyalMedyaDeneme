---
paths:
  - "app/templates/**"
  - "app/static/js/**"
---
# Frontend Kuralları

- Paylaşılan makrolar SADECE `_*.html` partial dosyalarında yaşar (`{% extends %}` OLMADAN) — sayfa template'inden makro import etmek Jinja'yı çökertir.
- Her POST formu `{{ csrf_token() }}` hidden input içerir; her fetch POST `X-CSRF-Token` header gönderir.
- JS: sayfa/özellik başına ayrı dosya, bundler yok. AJAX ile yeniden yüklenen paneller (mesajlaşma gibi) için document-level delegation kullan — doğrudan addEventListener stale kalır.
- Değişen her JS dosyası için `node --check <dosya>` çalıştır.
