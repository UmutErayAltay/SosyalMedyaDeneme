---
paths:
  - "sql/**"
---
# Migration Kuralları

- Migration'lar idempotent yazılır (`IF NOT EXISTS` vb.) ve Supabase MCP `apply_migration` ile DOĞRUDAN uygulanır — insan-inceleme adımı kullanıcının bilinçli kararıyla kaldırıldı (2026-07-05), "önceden böyleydi" diye geri alma.
- Migration uygulanmadan ÖNCE de sayfalar kırılmamalı — backend'de graceful degradation testi yap.
- Her tabloda RLS politikası tanımlı kalır (service-role bypass etse de defense-in-depth).
- **`apply_migration`'a verilen `name` parametresi, ÖNCESİNDE oluşturulmuş
  `sql/migration_<name>.sql` dosyasının adıyla BİREBİR aynı olmalı** (2026-07-17
  kuralı — bkz. `sql/MIGRATIONS.md`). Sıra: ÖNCE dosyayı yaz, SONRA aynı ismi
  kullanarak `apply_migration` çağır. Bu proje 68 dosyalık geçmişinde 5
  migration bu kurala uyulmadığı için Supabase'in kendi takip tablosunda
  (`supabase_migrations.schema_migrations`) var ama repoda HİÇ dosyası
  olmayan "kayıp" migration olarak bulundu (2026-07-17'de kurtarıldı) — aynı
  hata tekrarlanmasın diye bu kural eklendi.
- CREATE OR REPLACE FUNCTION ile parametre SAYISI değiştiriliyorsa (örn.
  yeni bir parametre eklemek) bu YENİ bir overload yaratır, ESKİSİNİ SİLMEZ
  (bkz. `sql/migration_drop_discover_page_posts_2arg_overload.sql`'ın neden
  var olduğu) — parametre sayısı değişen migration'larda eski overload'ı
  `DROP FUNCTION IF EXISTS` ile AYRICA temizlemeyi düşün.
