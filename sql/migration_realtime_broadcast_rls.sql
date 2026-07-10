-- Realtime BROADCAST kanalları (arama sinyalleşmesi calls:<userId>, sohbetteki
-- "yazıyor..." göstergesi typing:<conversationId>) önceden SADECE kanal adını
-- bilmeye dayalıydı — anon key'i olan herkes doğru adı bilir/tahmin ederse
-- kanala girip sinyal dinleyebilir/gönderebilirdi. Bu migration bu iki kanal
-- ailesi için realtime.messages üzerine RLS policy'leri ekler; client tarafında
-- (call.js, chat.js) kanallar `private: true` ile açılır (bkz. ilgili commit).
--
-- ÖNEMLİ: Mesaj İÇERİĞİ kanalı (messages:<conversationId>, postgres_changes)
-- BİLEREK private YAPILMADI ve bu migration'da ELE ALINMADI — Postgres Changes
-- zaten `messages` tablosunun kendi RLS'i üzerinden korunuyor ve private/public
-- fark etmeksizin çalışıyor (Supabase dokümantasyonu: "Private and public
-- channels can subscribe to Postgres Changes"). Bu kanala dokunmak, hâlihazırda
-- çalışan/kırılgan geçmişi olan mesajlaşma realtime hattını gereksiz yere
-- riske atardı.
--
-- realtime.messages üzerinde RLS zaten platform tarafından AÇIK (rls_enabled=true,
-- list_tables ile doğrulandı) — burada sadece policy ekleniyor. Idempotent
-- (create policy önce drop if exists ile).

-- --- calls:<userId> — arama sinyalleşmesi ---

-- Alıcı SADECE kendi kanalını dinleyebilir (calls:<meId>)
drop policy if exists "calls channel: owner can receive" on realtime.messages;
create policy "calls channel: owner can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'calls'
    and split_part((select realtime.topic()), ':', 2) = (select auth.uid())::text
);

-- Gönderici, hedefle ORTAK bir konuşması varsa sinyal gönderebilir (arama
-- zaten sadece açık bir 1:1 konuşma panelinden başlatılabiliyor — aynı kısıt
-- burada da uygulanıyor, rastgele bir kullanıcıya sahte arama sinyali
-- gönderilemez)
drop policy if exists "calls channel: send to conversation partner" on realtime.messages;
create policy "calls channel: send to conversation partner"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'calls'
    and exists (
        select 1
        from conversation_participants cp1
        join conversation_participants cp2 on cp1.conversation_id = cp2.conversation_id
        where cp1.user_id = (select auth.uid())
          and cp2.user_id = (split_part((select realtime.topic()), ':', 2))::uuid
    )
);

-- --- typing:<conversationId> — "yazıyor..." göstergesi ---

-- Sadece o konuşmanın katılımcıları dinleyebilir
drop policy if exists "typing channel: participants can receive" on realtime.messages;
create policy "typing channel: participants can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'typing'
    and exists (
        select 1 from conversation_participants
        where conversation_id = (split_part((select realtime.topic()), ':', 2))::uuid
          and user_id = (select auth.uid())
    )
);

-- Sadece o konuşmanın katılımcıları gönderebilir
drop policy if exists "typing channel: participants can send" on realtime.messages;
create policy "typing channel: participants can send"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part((select realtime.topic()), ':', 1) = 'typing'
    and exists (
        select 1 from conversation_participants
        where conversation_id = (split_part((select realtime.topic()), ':', 2))::uuid
          and user_id = (select auth.uid())
    )
);
