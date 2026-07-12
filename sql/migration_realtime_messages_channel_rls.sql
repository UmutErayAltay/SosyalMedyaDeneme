-- ============================================================
-- messages:<conversationId> kanalı RLS — bugüne kadar bilerek EN SON
-- bırakılan kanal (mesaj teslimi hattı en kırılgan parça, bkz.
-- migration_realtime_broadcast_rls.sql başındaki not).
--
-- Bu kanal İKİ şey taşır:
--   1) postgres_changes (INSERT/UPDATE/DELETE on public.messages) — zaten
--      `messages` tablosunun KENDİ RLS'i üzerinden korunuyor, private/public
--      fark etmez (Supabase: "Private and public channels can subscribe to
--      Postgres Changes"). Bu policy'ler SADECE extension='broadcast' için
--      yazıldı, postgres_changes'e DOKUNMUYOR.
--   2) broadcast 'msg-preview' eventi (hızlı önizleme, chat.js) — buraya
--      kadar korumasızdı: kanal adını (conversation_id, UUID) bilen/tahmin
--      eden biri mesaj içeriğini dinleyebilirdi.
--
-- typing:<conversationId> ile SİMETRİK: hem SELECT hem INSERT, konuşmanın
-- TÜM katılımcılarına (calls:<userId>'deki gibi sahip/hedef asimetrisi yok).
-- Idempotent.
-- ============================================================

drop policy if exists "messages channel: participants can receive" on realtime.messages;
create policy "messages channel: participants can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'messages'
    and exists (
        select 1 from conversation_participants
        where conversation_id = (split_part((select realtime.topic()), ':', 2))::uuid
          and user_id = (select auth.uid())
    )
);

drop policy if exists "messages channel: participants can send" on realtime.messages;
create policy "messages channel: participants can send"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'messages'
    and exists (
        select 1 from conversation_participants
        where conversation_id = (split_part((select realtime.topic()), ':', 2))::uuid
          and user_id = (select auth.uid())
    )
);

NOTIFY pgrst, 'reload schema';
