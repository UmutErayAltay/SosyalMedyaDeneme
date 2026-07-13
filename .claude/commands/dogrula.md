---
description: Değişen .py/.js/.html dosyalarını otomatik doğrula (py_compile + node --check + Jinja parse)
disable-model-invocation: true
---
`post-change-verify` skill'ini kullan: `python .claude/skills/post-change-verify/scripts/check_changes.py` çalıştır (argümansız — değişen dosyaları kendisi bulur).

Hata çıkarsa dosya+satırı göster ve düzelt, sonra tekrar çalıştır. Temizse kısaca "OK" de, uzun özet yazma.
