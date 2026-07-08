---
paths:
  - "app/static/css/**"
---
# CSS Kuralları

- `hidden` özniteliğiyle gizlenecek eleman explicit `display` değeri olan bir class taşıyorsa (`.btn` gibi), o class'ın `[hidden]` varyantını da EKLE (`.btn[hidden] { display: none; }`) — yoksa eleman görünür kalır. Bu hata projede 3+ kez tekrarlandı.
- 13px+ küçük metinde kullanılacak yeni renk: aynı gün WCAG AA (4.5:1) kontrast doğrula; koyu varyantı tercih et (örn. `#7A6F63`).
- Tek global stylesheet (`style.css`); açık/koyu tema CSS custom properties ile.
