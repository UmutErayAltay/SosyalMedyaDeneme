---
name: commit-hazirla
description: Bu projede bir özellik/bugfix bitip commit atılmadan HEMEN ÖNCE kullan. CLAUDE.md'nin sırayla gerektirdiği üç adımı (doğrulama → .context/active_context.md güncelleme → commit) tek akışta toplar; adımlardan birini atlamayı önler. Commit'i SADECE ana oturum atar — bu skill bir subagent içinden çağrılıyorsa commit adımını atla, sadece doğrulama+dokümantasyon yap ve ana oturuma devret.
---

# Commit öncesi kontrol listesi

CLAUDE.md sırası: doğrula → `.context/active_context.md` güncelle → Türkçe commit (`Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` ile biten) → SORMADAN commit at (ama sadece ana oturum).

## Adımlar

1. **`post-change-verify` çalıştır** — `python .claude/skills/post-change-verify/scripts/check_changes.py`. Hata varsa DUR, düzelt, tekrar çalıştır.
2. **Davranışsal doğrulama yapıldı mı kontrol et** — sadece sözdizimi yetmez. RLS/realtime değiştiyse `rls-migration-verify` metodolojisi tamamlanmış olmalı; backend mantığıysa `flask-smoke-test` ile en az bir gerçek istek atılmış olmalı; UI/CSS değiştiyse gerçek tarayıcıda (Playwright) görülmüş olmalı. Hiçbiri yapılmadıysa commit'ten önce yap, atlama.
3. **`.context/active_context.md`'yi güncelle** — yeni bir sprint/girdi olarak: ne değişti, neden, nasıl doğrulandı. Bu dosya commit'e DAHİL edilmez (proje kuralı: `.context/` commit edilmez) ama içeriği güncel tutulur.
4. **Commit'i ana oturumda at** — Türkçe mesaj, `git add` ile SADECE ilgili dosyaları seç (`-A`/`.` yasak — CLAUDE.md git güvenlik protokolü), mesaj sonu:
   ```
   Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
   ```
   Kullanıcıya SORMADAN commit at (proje kuralı zaten bunu onaylıyor) — ama push'u kullanıcı istemeden yapma.

## Kritik kısıtlar

- **Subagent'lar commit ATAMAZ.** Bu skill bir subagent'ta çalışıyorsa 1-3. adımları yap, sonucu ana oturuma bildir, commit'i ana oturum atsın.
- `.env`, kimlik bilgisi içeren dosyalar asla stage edilmez — `git status` sonrası şüpheli bir dosya görürsen içeriğini kontrol et.

## Ne zaman KULLANMA

- Deneysel/yarım kalan değişiklikler için — önce işi bitir, sonra bu akışı çalıştır.
- Kullanıcı açıkça "commitleme, sadece dosyaları bırak" dediyse.
