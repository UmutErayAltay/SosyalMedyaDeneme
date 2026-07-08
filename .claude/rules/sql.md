---
paths:
  - "sql/**"
---
# Migration Kuralları

- Migration'lar idempotent yazılır (`IF NOT EXISTS` vb.) ve Supabase MCP `apply_migration` ile DOĞRUDAN uygulanır — insan-inceleme adımı kullanıcının bilinçli kararıyla kaldırıldı (2026-07-05), "önceden böyleydi" diye geri alma.
- Migration uygulanmadan ÖNCE de sayfalar kırılmamalı — backend'de graceful degradation testi yap.
- Her tabloda RLS politikası tanımlı kalır (service-role bypass etse de defense-in-depth).
