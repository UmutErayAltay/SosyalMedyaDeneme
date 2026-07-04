-- ============================================================
-- MESAJ OKUNDU BİLGİSİ (read receipts) — Şema migration
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- 1) messages tablosuna read_at ekle (NULL = henüz okunmadı)
alter table public.messages add column if not exists read_at timestamptz;

-- 2) Karşı tarafın mesajları okundu işaretlemesine izin veren RLS politikası.
--    Mevcut "msg insert" politikası sadece INSERT'e izin veriyordu; burada
--    UPDATE politikası ekleniyor — sadece kendi göndermediği (karşı tarafın)
--    mesajlarda read_at alanını güncelleyebilsin.
drop policy if exists "msg update read_at" on public.messages;
create policy "msg update read_at" on public.messages for update
    using (
        sender_id <> auth.uid()
        and exists (
            select 1 from public.conversation_participants cp
            where cp.conversation_id = messages.conversation_id and cp.user_id = auth.uid()
        )
    )
    with check (
        sender_id <> auth.uid()
        and exists (
            select 1 from public.conversation_participants cp
            where cp.conversation_id = messages.conversation_id and cp.user_id = auth.uid()
        )
    );

-- 3) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
