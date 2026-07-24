-- KRİTİK güvenlik düzeltmesi: conversation_participants.cp_ins/cp_read
-- politikaları "true" (kısıtlama yok) idi — herhangi bir giriş yapmış
-- kullanıcı kendi gerçek access token'ıyla (window.SB_ACCESS_TOKEN,
-- _supabase_core.html'de her sayfada gömülü) doğrudan PostgREST'e:
--   1) conversation_participants tablosunu TAMAMEN okuyup her sohbetin
--      conversation_id/user_id çiftini görebiliyordu (cp read: true),
--   2) kendini HERHANGİ bir sohbete katılımcı olarak EKLEYEBİLİYORDU
--      (cp ins: true) — bu satır eklenince messages tablosunun "msg read"
--      politikası (EXISTS ... cp.user_id = auth.uid()) sahte biçimde
--      sağlanmış oluyor, saldırgan platformdaki HERHANGİ BİR özel sohbetin
--      tüm mesaj geçmişini okuyabiliyordu (private:true realtime kanalına
--      da meşru abone olabiliyordu).
--
-- Uygulama (Flask, service-role client — RLS'i bypass eder) bu tabloya
-- HİÇ client-side (supabase-js) erişmiyor (grep ile doğrulandı) — bu
-- politikaların hiçbir meşru kullanım senaryosu yok, sadece saldırı yüzeyi.
--
-- cp read: "kendi bulunduğun sohbetlerin TÜM katılımcılarını görebilirsin"
-- (self-only DEĞİL — realtime broadcast RLS'indeki "calls channel: send to
-- conversation partner" politikası cp1/cp2 çapraz JOIN'iyle BAŞKA bir
-- katılımcının satırını okuyabilmeyi gerektiriyor, bkz. rls-migration-verify
-- skill notu). cp ins: client'ın hiç ihtiyacı yok, tamamen kapatıldı.
drop policy if exists "cp read" on public.conversation_participants;
create policy "cp read" on public.conversation_participants
    for select using (
        exists (
            select 1 from public.conversation_participants cp2
            where cp2.conversation_id = conversation_participants.conversation_id
              and cp2.user_id = auth.uid()
        )
    );

drop policy if exists "cp ins" on public.conversation_participants;
create policy "cp ins" on public.conversation_participants
    for insert with check (false);

-- ORTA öncelikli aynı-kök-neden düzeltmeleri: client hiçbir zaman bu
-- tablolara doğrudan yazmıyor (grep ile doğrulandı), "true" INSERT
-- politikaları spam/vandalizm vektörü (örn. başkasının anketine sahte
-- seçenek enjekte etme, başkasının postuna rastgele hashtag etiketleme).
drop policy if exists "conv ins" on public.conversations;
create policy "conv ins" on public.conversations
    for insert with check (false);

drop policy if exists "hashtags insert" on public.hashtags;
create policy "hashtags insert" on public.hashtags
    for insert with check (false);

drop policy if exists "polls insert" on public.polls;
create policy "polls insert" on public.polls
    for insert with check (false);

drop policy if exists "poll_options insert" on public.poll_options;
create policy "poll_options insert" on public.poll_options
    for insert with check (false);

-- post_hashtags zaten ownership-scoped bir DELETE politikasına sahip
-- (posts.user_id = auth.uid()) — INSERT'i AYNI desenle sınırlıyoruz
-- (tamamen kapatmak yerine, ileride meşru bir client-side kullanım
-- ihtiyacı çıkarsa kendi postuna etiket eklemeye izin verir).
drop policy if exists "post_hashtags insert" on public.post_hashtags;
create policy "post_hashtags insert" on public.post_hashtags
    for insert with check (
        exists (
            select 1 from public.posts
            where posts.id = post_hashtags.post_id
              and posts.user_id = auth.uid()
        )
    );

NOTIFY pgrst, 'reload schema';
