# Migration takibi — nasıl çalışır, nasıl denetlenir

## Versiyonlama zaten var (Supabase'in kendi altyapısı)

Bu proje ayrı bir migration-runner (Alembic/Flyway vb.) KULLANMIYOR —
`mcp__supabase__apply_migration` çağrıldığında Supabase BUNU OTOMATİK
`supabase_migrations.schema_migrations` tablosuna yazıyor: `version`
(zaman damgalı, örn. `20260716183842`), `name`, `statements` (çalıştırılan
tam SQL), ve `rollback` (geri alma SQL'i — **bugüne kadar hiç
doldurulmadı**, aşağıya bak).

Bu tablo PostgREST üzerinden (`sb.table(...)`) SORGULANAMAZ — sadece
`public` şeması dışa açık. Denetim SADECE Supabase MCP'nin
`mcp__supabase__execute_sql` aracıyla (veya Supabase dashboard'ın SQL
editor'ünden) doğrudan sorgulanarak yapılabilir:

```sql
select version, name from supabase_migrations.schema_migrations order by version;
```

## 2026-07-17 denetimi ve bulgular

68 dosyalık `sql/` klasörü ile 29 kayıtlı migration karşılaştırıldı
(`scripts/`'ta kalıcı bir otomasyon YOK — yukarıdaki PostgREST kısıtı
yüzünden bu ancak MCP erişimi olan bir oturumda elle yapılabiliyor).
Sonuç: **5 migration** Supabase'in takibinde vardı ama `sql/`'da hiç
dosyası yoktu — `statements` kolonundan kurtarılıp şu dosyalar olarak
geri eklendi:

- `migration_update_profile_page_data_rpc_private_accounts.sql`
- `migration_fix_profile_page_data_rpc_liked_posts_private_check.sql`
- `migration_add_is_private_filtering_to_feed_and_discover_rpc.sql`
- `migration_update_feed_page_posts_with_mutes.sql`
- `migration_notification_prefs_missing_columns.sql`

Ayrıca isim uyuşmazlığı bulundu (dosya var ama Supabase'deki `name` farklı
yazılmış — gerçek bir kayıp değil, sadece karşılaştırmayı zorlaştırıyor):
`migration_discover_pagination.sql` ↔ tracked adı
`discover_page_posts_pagination`; `migration_drop_discover_page_posts_2arg_overload.sql`
↔ tracked adı `drop_orphaned_discover_page_posts_2arg_overload`;
`migration_message_edit.sql` ↔ tracked adı `message_edit_edited_at`.
Bunlar geriye dönük yeniden adlandırılmadı (git geçmişini bozar, değeri
düşük) — ama BUNDAN SONRA (.claude/rules/sql.md kuralı) dosya adı ile
`apply_migration`'a verilen `name` HER ZAMAN birebir aynı olmalı.

**`rollback` kolonu HİÇBİR migration'da doldurulmamış** — geri alma için
şu ana kadar hiç plan yapılmamış. 68 dosyanın tamamı için geriye dönük
rollback SQL'i yazmak (çoğu additive/veri taşıyan değişiklik, bazıları
pratikte tersine çevrilemez) değerli değil — bunun yerine BUNDAN SONRA
yazılan migration'larda mümkün olduğunca bir rollback notu eklenmesi
tercih edilir (aşağıya bak).

## Bundan sonraki convention

1. Migration SQL dosyasını ÖNCE `sql/migration_<isim>.sql` olarak yaz/oku.
2. `apply_migration` çağrılırken `name` parametresi bu dosyanın adıyla
   (`migration_` öneki ve `.sql` uzantısı hariç) BİREBİR aynı olsun.
3. Mümkünse dosyanın sonuna bir `-- ROLLBACK:` yorum bloğu ekle (geri alma
   SQL'i, otomatik çalıştırılmaz ama bir insan/AI hızlıca geri alabilsin
   diye belgeli kalır) — özellikle DROP/ALTER gibi yıkıcı olabilecek
   değişikliklerde.
4. Parametre SAYISI değişen `CREATE OR REPLACE FUNCTION` migration'larında
   eski overload'ın öksüz kalıp kalmadığını kontrol et (bkz.
   `.claude/rules/sql.md`).
5. Periyodik denetim (elle, MCP erişimi olan bir oturumda):
   ```sql
   select name from supabase_migrations.schema_migrations;
   ```
   çıktısını `ls sql/migration_*.sql` ile karşılaştır — eksik olan varsa
   `statements` kolonundan kurtarıp dosya olarak ekle.
