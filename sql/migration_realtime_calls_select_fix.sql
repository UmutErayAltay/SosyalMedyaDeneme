-- ============================================================
-- calls:<userId> kanalı SELECT policy düzeltmesi.
--
-- KÖK NEDEN (izole test ile doğrulandı, bkz. .context/active_context.md):
-- supabase-js private kanallarda .send() öncesi .subscribe() ile JOIN
-- yapmayı gerektiriyor, ve JOIN SELECT policy'sine tabi. Eski policy
-- SADECE kanal sahibinin (hedefin) subscribe olmasına izin veriyordu —
-- bu da ARAYAN tarafın (getOutboundChannel, INSERT hakkı olan ama SAHİP
-- olmayan taraf) kendi gönderdiği sinyali yollamak için gereken JOIN'i
-- hiç yapamaması anlamına geliyordu: her arama denemesi CHANNEL_ERROR
-- ile başarısız olurdu (muhtemelen 2026-07-10'daki orijinal olayın asıl
-- nedeni buydu, "bayat token" değil).
--
-- DÜZELTME: SELECT policy'i INSERT policy'iyle simetrik hale getirir —
-- hedefin KENDİSİ ya da hedefle ORTAK bir konuşması olan biri (yani
-- zaten arayabilecek biri) kanala abone olabilir. Idempotent.
-- ============================================================

drop policy if exists "calls channel: owner can receive" on realtime.messages;
create policy "calls channel: owner can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'calls'
    and (
        split_part((select realtime.topic()), ':', 2) = (select auth.uid())::text
        or exists (
            select 1
            from conversation_participants cp1
            join conversation_participants cp2 on cp1.conversation_id = cp2.conversation_id
            where cp1.user_id = (select auth.uid())
              and cp2.user_id = (split_part((select realtime.topic()), ':', 2))::uuid
        )
    )
);

NOTIFY pgrst, 'reload schema';
