-- ============================================================
-- ŞİKAYET/RAPORLAMA — sadece kayıt tutuluyor, kullanıcı isteğiyle
-- ŞU AN İÇİN görüntüleyecek bir admin paneli/rolü YOK (en düşük kapsam,
-- 2026-07-06'da bilinçli seçildi). İleride bir moderasyon arayüzü
-- eklenmek istenirse bu tablo zaten hazır olacak.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.reports (
    id          uuid primary key default gen_random_uuid(),
    reporter_id uuid not null references public.profiles(id) on delete cascade,
    target_type text not null check (target_type in ('post', 'comment', 'user')),
    target_id   uuid not null,
    created_at  timestamptz not null default now(),
    unique (reporter_id, target_type, target_id)  -- aynı içeriği tekrar şikayet edemezsin
);

alter table public.reports enable row level security;

drop policy if exists "reports read"   on public.reports;
drop policy if exists "reports insert" on public.reports;
-- Sadece kendi gönderdiğin raporu görebilirsin (bookmarks ile aynı desen) —
-- admin paneli olmadığı için başkasının raporlarını gören kimse yok zaten.
create policy "reports read"   on public.reports for select using (auth.uid() = reporter_id);
create policy "reports insert" on public.reports for insert with check (auth.uid() = reporter_id);

NOTIFY pgrst, 'reload schema';
