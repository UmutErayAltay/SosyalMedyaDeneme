-- 2026-08-21: Yayın-öncesi denetim (Supabase security advisor'ın "Public/
-- Signed-In Can Execute SECURITY DEFINER Function" bulgusu araştırılıp
-- GERÇEK bir açık olduğu doğrulandı, sadece bir uyarı değil).
--
-- Üç fonksiyon anon/authenticated rollerine EXECUTE veriyordu ama HİÇBİRİ
-- doğrudan PostgREST RPC'siyle (/rest/v1/rpc/<fn>) çağrılmak İÇİN
-- tasarlanmamıştı — app her zaman get_sb() (service_role) üzerinden çağırıyor
-- (bkz. CLAUDE.md "DB erişimi her zaman get_sb()"). SECURITY DEFINER olduğu
-- için RLS'i BYPASS ediyorlar, bu yüzden anon/authenticated'e açık kalmaları
-- gerçek bir yetkilendirme atlatma riskiydi:
--
--   - leave_group_and_reassign_admin(p_conversation_id, p_user_id): p_user_id
--     parametresi auth.uid() ile HİÇ karşılaştırılmıyor — KİMLİK DOĞRULAMASI
--     BİLE GEREKMEDEN (anon rolü execute edebiliyordu) HERHANGİ BİR grup
--     sohbetinden HERHANGİ BİR kullanıcıyı çıkarabilir, sohbeti silebilir
--     (son üye çıkarsa) ve admin yeniden atayabilirdi. Gerçek, sömürülebilir
--     bir güvenlik açığıydı — sadece bir "uyarı" değil.
--   - is_conversation_participant(p_conversation_id, p_user_id): anon'a açıktı,
--     kimlik doğrulaması olmadan herhangi bir kullanıcının herhangi bir
--     sohbette olup olmadığını sorgulayabilme (üyelik sızıntısı/enumeration).
--   - handle_new_user(): parametresiz bir TRIGGER fonksiyonu, trigger
--     bağlamı DIŞINDA çağrılırsa zaten hata verir (NEW tanımsız) ama
--     hijyen için anon/authenticated'e EXECUTE'un hiç GEREKMEMESİ AÇIK.
--
-- service_role'ün execute hakkı KORUNUR (Flask backend HER ZAMAN bu rolü
-- kullanıyor, app'in kendi çalışması bu revoke'tan HİÇ ETKİLENMEZ).

-- NOT (2026-08-21, takip): bu REVOKE'lar TEK BAŞINA YETERSİZ kaldı —
-- Postgres'in EXECUTE'u otomatik verdiği `PUBLIC` rolü ayrıca revoke
-- edilmedikçe anon/authenticated PUBLIC üzerinden hakkı miras almaya
-- devam ediyordu. Asıl düzeltme migration_revoke_public_rpc_execute_from_public_role.sql'de.
revoke execute on function public.leave_group_and_reassign_admin(uuid, uuid) from anon, authenticated;
revoke execute on function public.is_conversation_participant(uuid, uuid) from anon, authenticated;
revoke execute on function public.handle_new_user() from anon, authenticated;

-- ROLLBACK:
-- grant execute on function public.leave_group_and_reassign_admin(uuid, uuid) to anon, authenticated;
-- grant execute on function public.is_conversation_participant(uuid, uuid) to anon, authenticated;
-- grant execute on function public.handle_new_user() to anon, authenticated;
