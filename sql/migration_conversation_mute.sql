-- Sohbet bazlı bildirim sessize alma: katılımcı başına is_muted bayrağı
-- (_notify_conversation muted katılımcılara bildirim/push üretmez;
--  mesajlar yine gelir ve okunmamış sayılır, sadece bildirim kesilir)

alter table public.conversation_participants
add column if not exists is_muted boolean not null default false;
