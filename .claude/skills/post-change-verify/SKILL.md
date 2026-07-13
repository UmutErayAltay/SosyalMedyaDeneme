---
name: post-change-verify
description: Bu projede (Flask+Supabase+Jinja2+vanilla JS) herhangi bir .py, .js veya .html dosyası değiştirildikten sonra, özellikle commit'ten ÖNCE kullan. CLAUDE.md'nin gerektirdiği "her değişiklik sonrası py_compile" kuralını tek komutla değişen TÜM dosyalar (py_compile + node --check + Jinja parse) için otomatik çalıştırır — dosyaları tek tek elle kontrol etmek yerine.
---

# Değişiklik-sonrası doğrulama zinciri

CLAUDE.md kuralı: "Her değişiklik sonrası: `python -m py_compile app/*.py app/routes/*.py app/messaging/*.py`". Bu skill bunu genelleştirir: sadece o an değişmiş dosyaları bulur ve türüne göre doğru kontrolü otomatik seçer.

## Kullanım

```
python .claude/skills/post-change-verify/scripts/check_changes.py
```

Argümansız çalıştırıldığında `git diff` + `git status --porcelain` ile değişen/yeni dosyaları kendisi bulur. Belirli dosyaları kontrol etmek istersen argüman olarak ver:

```
python .claude/skills/post-change-verify/scripts/check_changes.py app/auth.py app/static/js/chat.js
```

## Ne yapar

- `.py` dosyaları → `python -m py_compile`
- `.js` dosyaları → `node --check`
- `.html` dosyaları → Jinja2 `Environment.parse()` (syntax hatalarını yakalar, render etmez — DB'ye dokunmaz)

Tüm kontroller temizse `OK` ile 0 döner; herhangi biri patlarsa hatayı gösterip 1 döner.

## Ne zaman kullan

- Bir özellik/bugfix'i bitirip commit atmadan hemen önce.
- Birden fazla dosyayı art arda düzenledikten sonra, hangisini unuttuğundan emin değilsen.

## Ne zaman KULLANMA

- Sadece `.context/`, `.md`, `sql/*.sql` gibi bu üç türe girmeyen dosyalar değiştiyse (script zaten bunları atlar, boşuna çalıştırma).
- Canlı DB'ye karşı gerçek doğrulama gerekiyorsa (test_client / Playwright) — bu skill sadece SÖZDİZİMİ kontrolü yapar, davranışı doğrulamaz. Ondan sonra `/verify` veya testing ajanı gerekebilir.
