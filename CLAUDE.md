# CLAUDE.md

Flask + Supabase (Postgres/Auth/Storage/Realtime) + Jinja2 + vanilla JS sosyal medya uygulaması. Canlı kullanımda: güvenlik ve kararlılık > özellik hızı. "Ne inşa edildi / neredeyiz" için kodu taramadan ÖNCE `.context/architecture.md` ve `.context/active_context.md`'ye bak. Dizine özel kurallar `.claude/rules/` altında otomatik yüklenir.

## Komutlar
- Her değişiklik sonrası: `python -m py_compile app/*.py app/routes/*.py app/messaging/*.py`
- Kalıcı test suite `tests/` altında (`python -m pytest tests/ -v`, `pip install -r requirements-dev.txt`) — auth/2FA/keşfet gibi güvenlik-kritik yollar için gerçek Supabase test kullanıcısıyla (`sb.auth.admin.create_user` + fixture cleanup) çalışır, mock yok. Yeni güvenlik-kritik bir route/akış eklenince oraya da kalıcı test eklenmesi tercih edilir; tek seferlik doğrulama script'leri (Playwright dahil) hâlâ ad-hoc/UI değişiklikleri için kullanılabilir
- Paket kurulumu HER ZAMAN `python -m pip install` (çıplak `pip` yanlış Python'a kurar)
- Sunucu: dev `run.py`, canlı `serve.py`; sunucu testinden önce eski python süreçlerini kapat
- Sembol bazlı arama/yeniden adlandırma işlerinde Serena MCP araçlarını tercih et (`find_symbol`, `find_referencing_symbols`, `rename_symbol`)

## Kısıtlar
- `.env` okunmaz ve commit edilmez; `.context/` commit edilmez ama içeriği serbestçe güncellenir
- DB erişimi her zaman `get_sb()` (service-role); RLS defense-in-depth olarak açık kalır
- İş bitince SORMADAN commit: mesaj Türkçe, sonu `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`; commit'ten önce `.context/active_context.md` güncellenir
- Commit'i SADECE ana oturum atar, subagent asla
- Basit yeniden adlandırma/formatlama işinde kullanıcıya `/model haiku` öner

# Compact instructions
When compacting, preserve: code changes, test results, error messages, and decisions made. Discard: failed exploration attempts, redundant explanations, successful command outputs.

# Çalışma modeli (2026-07-13)
Ana oturum mimar/orkestratördür: geliştirme işini `.claude/agents/` altındaki ilgili ajana (backend/frontend/database/security/testing/performance/documentation) devret — kendisi SADECE küçük sorun/tek dosyalık eklemeyle bizzat ilgilenir. code-reviewer/security ajanları geliştirme SIRASINDA değil, "Proje tamamlandı" dendiğinde devreye girer. Commit yetkisi hiçbir ajanda yok, SADECE ana oturumda kalır.

Bir geliştirme isteği gelince öneri sunmadan/koda başlamadan ÖNCE bunun zaten yapılıp yapılmadığını kontrol et: dosya dosya taramak yerine `notebooklm-research` skill'i ile sor (.context zaten NotebookLM'de kayıtlı, bedava ve daha az token). "Zaten var" çıkarsa kullanıcıya söyle, tekrar yapma; NotebookLM'in cevabı dondurulmuş olabilir, somut bir öneri sunmadan önce ilgili dosyayı/kodu doğrula.
