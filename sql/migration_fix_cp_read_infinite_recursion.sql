-- migration_lock_down_open_insert_select_policies.sql'deki "cp read"
-- politikası KENDİ tablosunu (conversation_participants) subquery'de
-- referans alıyordu — Postgres bu iç sorguyu değerlendirirken AYNI
-- politikayı tekrar uygulamaya çalışıp "infinite recursion detected in
-- policy for relation conversation_participants" (42P17) hatası veriyordu
-- (izole test sırasında GERÇEK 3 kullanıcıyla yakalandı, canlıya hiç
-- gitmedi). Standart Supabase/Postgres çözümü: kontrolü bir
-- SECURITY DEFINER fonksiyona taşımak — fonksiyon tablo sahibi (postgres,
-- RLS'i bypass eder) olarak çalıştığından iç sorgu politikayı tekrar
-- TETİKLEMEZ.
create or replace function public.is_conversation_participant(p_conversation_id uuid, p_user_id uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select exists (
        select 1 from conversation_participants
        where conversation_id = p_conversation_id and user_id = p_user_id
    );
$$;

drop policy if exists "cp read" on public.conversation_participants;
create policy "cp read" on public.conversation_participants
    for select using (
        public.is_conversation_participant(conversation_id, auth.uid())
    );

NOTIFY pgrst, 'reload schema';
