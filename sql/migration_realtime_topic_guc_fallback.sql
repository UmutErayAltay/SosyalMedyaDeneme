-- ============================================================
-- realtime.messages politikalarında topic kaynağına GERİ DÜŞÜŞ (fallback).
--
-- KÖK NEDEN (2026-08-07, canlı Postgres + Realtime logları ve kontrollü
-- SQL deneyiyle KANITLANDI — aylardır süren "Unauthorized" olayının asıl
-- sebebi budur):
--
-- Realtime, bir private kanala JOIN isteğinde izin kontrolünü
-- realtime.messages'a GEÇİCİ bir "probe" satırı INSERT ederek yapıyor.
-- Bizim politikalarımız kanal tipini SADECE `realtime.topic()` session
-- değişkeninden (GUC) okuyordu. Bu probe sırasında GUC boş kalıyor:
--     split_part(NULL, ':', 1) -> NULL
--     NULL = 'typing'          -> NULL (TRUE değil)
-- => 3 PERMISSIVE INSERT politikasının HİÇBİRİ eşleşmiyor
-- => Postgres: "new row violates row-level security policy for table
--    messages" (canlı postgres loglarında, Realtime'ın "Unauthorized"
--    satırlarıyla AYNI saniyelerde görüldü)
-- => Realtime bunu istemciye "You do not have permissions to read from
--    this Channel topic" diye raporluyor (INSERT hatası "read" hatası
--    gibi görünüyor — teşhisi bu kadar zorlaştıran şey buydu).
--
-- Kontrollü deney (transaction içinde, rollback'li):
--   GUC set EDİLMİŞ  + eski politika -> INSERT BAŞARILI
--   GUC set EDİLMEMİŞ + eski politika -> "new row violates RLS" (canlı hatanın AYNISI)
--   GUC set EDİLMEMİŞ + bu politika  -> INSERT BAŞARILI
--
-- DÜZELTME: `(select realtime.topic())` yerine
--           `coalesce((select realtime.topic()), realtime.messages.topic)`.
-- GUC varsa aynen eskisi gibi davranır; yoksa satırın KENDİ topic
-- kolonuna düşer. GÜVENLİK AYNI KALIR: hangi kaynaktan gelirse gelsin
-- topic'ten çözülen conversation/user için katılımcılık kontrolü
-- (conversation_participants) DEĞİŞMEDEN uygulanmaya devam eder —
-- kullanıcı yine sadece kendi konuşmalarının kanallarına erişebilir.
--
-- NOT: Realtime, istemci başına politikaları BAĞLANTI SÜRESİNCE cache'ler
-- (Supabase dokümanı) — bu migration'dan sonra açık sekmelerin yeniden
-- bağlanması (sayfa yenileme) gerekir.
--
-- Idempotent.
-- ============================================================

-- ---------- calls:<user_id> ----------

drop policy if exists "calls channel: owner can receive" on realtime.messages;
create policy "calls channel: owner can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'calls'
    and (
        split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2)
            = (select auth.uid())::text
        or exists (
            select 1
            from conversation_participants cp1
            join conversation_participants cp2 on cp1.conversation_id = cp2.conversation_id
            where cp1.user_id = (select auth.uid())
              and cp2.user_id = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
        )
    )
);

drop policy if exists "calls channel: send to conversation partner" on realtime.messages;
create policy "calls channel: send to conversation partner"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'calls'
    and exists (
        select 1
        from conversation_participants cp1
        join conversation_participants cp2 on cp1.conversation_id = cp2.conversation_id
        where cp1.user_id = (select auth.uid())
          and cp2.user_id = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
    )
);

-- ---------- messages:<conversation_id> ----------

drop policy if exists "messages channel: participants can receive" on realtime.messages;
create policy "messages channel: participants can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'messages'
    and exists (
        select 1
        from conversation_participants
        where conversation_participants.conversation_id
                = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
          and conversation_participants.user_id = (select auth.uid())
    )
);

drop policy if exists "messages channel: participants can send" on realtime.messages;
create policy "messages channel: participants can send"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'messages'
    and exists (
        select 1
        from conversation_participants
        where conversation_participants.conversation_id
                = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
          and conversation_participants.user_id = (select auth.uid())
    )
);

-- ---------- typing:<conversation_id> ----------

drop policy if exists "typing channel: participants can receive" on realtime.messages;
create policy "typing channel: participants can receive"
on realtime.messages
for select
to authenticated
using (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'typing'
    and exists (
        select 1
        from conversation_participants
        where conversation_participants.conversation_id
                = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
          and conversation_participants.user_id = (select auth.uid())
    )
);

drop policy if exists "typing channel: participants can send" on realtime.messages;
create policy "typing channel: participants can send"
on realtime.messages
for insert
to authenticated
with check (
    realtime.messages.extension = 'broadcast'
    and split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 1) = 'typing'
    and exists (
        select 1
        from conversation_participants
        where conversation_participants.conversation_id
                = (split_part(coalesce((select realtime.topic()), realtime.messages.topic), ':', 2))::uuid
          and conversation_participants.user_id = (select auth.uid())
    )
);

NOTIFY pgrst, 'reload schema';
