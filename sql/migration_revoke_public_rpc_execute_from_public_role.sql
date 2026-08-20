-- 2026-08-21 (takip): migration_revoke_public_rpc_execute.sql'deki
-- "REVOKE ... FROM anon, authenticated" TEK BAŞINA yetersiz kaldı —
-- doğrulamada anon hâlâ execute edebiliyordu. Kök neden: Postgres
-- fonksiyon oluşturulunca EXECUTE'u OTOMATİK olarak `PUBLIC` rolüne
-- (her rolün DOLAYLI üyesi olduğu özel bir rol) verir; sadece anon/
-- authenticated'den REVOKE etmek PUBLIC'in verdiği hakkı GERİ ALMAZ,
-- roller PUBLIC'in izinlerinin BİRLEŞİMİNİ de miras alır. Doğru düzeltme:
-- PUBLIC'ten de REVOKE edip service_role'e AÇIKÇA GERİ vermek (Flask
-- backend'in get_sb() service_role kullanımı bu grant'a bağlı).
--
-- Doğrulandı: revoke sonrası has_function_privilege(anon, ..., 'execute')
-- = false, has_function_privilege(service_role, ..., 'execute') = true
-- (üç fonksiyon için de).

revoke execute on function public.leave_group_and_reassign_admin(uuid, uuid) from public;
revoke execute on function public.is_conversation_participant(uuid, uuid) from public;
revoke execute on function public.handle_new_user() from public;
grant execute on function public.leave_group_and_reassign_admin(uuid, uuid) to service_role;
grant execute on function public.is_conversation_participant(uuid, uuid) to service_role;
grant execute on function public.handle_new_user() to service_role;

-- ROLLBACK:
-- grant execute on function public.leave_group_and_reassign_admin(uuid, uuid) to public;
-- grant execute on function public.is_conversation_participant(uuid, uuid) to public;
-- grant execute on function public.handle_new_user() to public;
