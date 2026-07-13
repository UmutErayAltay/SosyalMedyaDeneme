---
name: server-preflight
description: Bu projede dev sunucusunu (run.py) başlatmadan veya Playwright/canlı testten önce kullan. run.py debug=True + Werkzeug reloader kullandığı için eski python süreçleri arka planda takılı kalabilir ve yeni testleri sessizce bozar (feedback-flask-server-testing.md'de kayıtlı tuzak). Kullanıcı "sunucuyu başlat", "test edelim", "canlı test" dediğinde veya Playwright ile UI doğrulamasına geçmeden önce proaktif kullan.
---

# Sunucu test öngüzergahı

CLAUDE.md kuralı: "sunucu testinden önce eski python süreçlerini kapat". Bu skill bunu tek komuta indirger.

## Kullanım

Sadece eski süreçleri temizle (sen `run.py`'yi ayrı başlatacaksan):

```powershell
pwsh .claude/skills/server-preflight/scripts/preflight.ps1
```

Temizle + `run.py`'yi arka planda başlat + `http://127.0.0.1:5000/` yanıt verene kadar bekle (varsayılan 20 sn):

```powershell
pwsh .claude/skills/server-preflight/scripts/preflight.ps1 -Start
```

## Ne zaman kullan

- Playwright ile UI doğrulamasına başlamadan hemen önce.
- `serve.py` (canlı) değil, `run.py` (dev) testinden önce her zaman.
- "Sunucu yanıt vermiyor" / "eski davranış görünüyor" gibi belirsiz testte önce bunu çalıştır — reloader kalıntısı ihtimalini eler.

## Ne zaman KULLANMA

- `serve.py` (production/canlı) üzerinde — bu script sadece `run.py` komut satırını hedefler, canlı süreçlere dokunmaz.
- Sunucu zaten yeni başlatıldıysa ve hiç eski süreç şüphesi yoksa (gereksiz kill/restart döngüsüne girme).
