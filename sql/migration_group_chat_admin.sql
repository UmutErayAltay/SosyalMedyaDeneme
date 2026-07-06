-- ============================================================
-- GRUP SOHBETİ YÖNETİCİ (ADMIN) ROLÜ
-- conversation_participants'a is_admin kolonu ekleniyor. Yeni gruplarda
-- create_group() üyeleri eklerken kurucuyu admin işaretleyecek (backend
-- tarafı); burada sadece şema + geriye dönük veri düzeltmesi var.
--
-- Geriye dönük uyumluluk: migration_group_chat.sql'den sonra oluşturulmuş
-- mevcut gruplarda hiç admin yok (o zaman kolon yoktu, herkes eşit
-- eklenmişti). Bu satır çalıştıktan sonra "kimsenin yönetemediği" bir grup
-- kalmasın diye, grubu kuran kişi (conversations.created_by) kendi
-- conversation_participants satırında otomatik admin yapılıyor — bu,
-- kullanıcının zaten "grup kurucusu" olarak bildiği kişiyle tutarlı, ekstra
-- bir karar gerektirmiyor. created_by NULL olan (eski/anonim) gruplar
-- etkilenmez, admin'siz kalır — DM'ler (is_group = false) hiç dokunulmaz.
-- Idempotent: tekrar çalıştırılabilir.
-- ============================================================

alter table public.conversation_participants
  add column if not exists is_admin boolean not null default false;

update public.conversation_participants cp
set is_admin = true
from public.conversations c
where cp.conversation_id = c.id
  and c.is_group = true
  and c.created_by is not null
  and cp.user_id = c.created_by
  and cp.is_admin = false;

NOTIFY pgrst, 'reload schema';
