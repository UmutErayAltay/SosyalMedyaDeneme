-- discover_page_posts'a p_offset eklenirken (migration_discover_pagination.sql)
-- CREATE OR REPLACE, parametre sayısı farklı olduğu için eski 2 parametreli
-- overload'ı DEĞİL, YENİ bir 3-parametreli overload oluşturdu — eskisi DB'de
-- öksüz kaldı (code-reviewer bulgusu). Uygulama artık sadece 3 parametreli
-- imzayı çağırıyor, eskisi güvenle kaldırılabilir.
drop function if exists public.discover_page_posts(uuid, integer);

NOTIFY pgrst, 'reload schema';
