-- discover_page_posts'un 2-arg overload'u (p_me, p_limit) ÖLÜ KOD.
--
-- Geçmiş: migration_drop_discover_page_posts_2arg_overload.sql (20260716194855,
-- tracked adı drop_orphaned_discover_page_posts_2arg_overload) bu overload'ı
-- zaten silmişti. Ama hemen ertesi gün
-- migration_hide_deactivated_users_posts_from_feed_discover.sql (20260718103555)
-- deaktif kullanıcı filtresini eklerken YANLIŞLIKLA eski 2-arg imzayla
-- `create or replace function public.discover_page_posts(p_me uuid, p_limit int default 20)`
-- yazdı — parametre sayısı farklı olduğu için bu, güncel 3-arg overload'ı
-- DEĞİL, 2-arg overload'ı YENİDEN yarattı (bkz. .claude/rules/sql.md tuzağı).
-- Bir sonraki migration (hide_deactivated_users_posts_discover_3arg_overload,
-- 20260718103747) sadece 3-arg'ı düzeltti, dirilen 2-arg'ı fark etmedi.
--
-- app/routes/discovery.py SADECE 3-arg imzayı çağırıyor
-- (sb.rpc("discover_page_posts", {"p_me":..., "p_limit":..., "p_offset":...})),
-- bu yüzden 2-arg overload güvenle kaldırılabilir. pg_get_functiondef ile
-- doğrulandı: iki overload'ın mantığı şu an birebir aynı (drift yok), bu
-- sadece ölü kod temizliği.
drop function if exists public.discover_page_posts(uuid, integer);

NOTIFY pgrst, 'reload schema';

-- ROLLBACK:
-- create or replace function public.discover_page_posts(p_me uuid, p_limit int default 20)
-- returns jsonb language sql stable set search_path = public as $$
-- -- bkz. migration_hide_deactivated_users_posts_from_feed_discover.sql içeriği
-- $$;
