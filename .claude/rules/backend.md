---
paths:
  - "app/**/*.py"
---
# Backend Kuralları

- Yeni view'da `render_template()`'e `me=session.get("user")` ekle — unutulursa hata VERMEZ, navbar sessizce kaybolur.
- Migration'ı belirsiz yeni özellikler `try/except` ile korunur; çekirdekte (like/auth) bu tolerans YOK.
- N+1 yasak: `_attach_post_metrics()` deseni (embedded count + tek `IN`); sayı için `count="exact", head=True`.
- ThreadPoolExecutor paralelleştirmesinde early-return yolları YERİNDE kalmalı — diff'i satır satır oku, `py_compile` yakalamaz.
- Post kartına yeni alan = HEM `_attach_post_metrics()` HEM `enrich_post_json()` RPC güncellenir.
- Yeni cache'te (`app/cache.py`) veriyi değiştiren HER yere `invalidate()` ekle.
