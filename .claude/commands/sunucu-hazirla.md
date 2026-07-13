---
description: Eski run.py süreçlerini kapat ve dev sunucusunu temiz başlat (server-preflight skill)
disable-model-invocation: true
---
`server-preflight` skill'ini kullan: `pwsh .claude/skills/server-preflight/scripts/preflight.ps1 -Start` çalıştır (eski python süreçlerini kapatır, `run.py`'yi arka planda başlatır, `http://127.0.0.1:5000/` yanıt verene kadar bekler).

Sadece eskileri kapatıp kendin ayrı başlatacaksan `-Start` olmadan çalıştır. Sonucu tek satırla bildir (ayakta/değil).
