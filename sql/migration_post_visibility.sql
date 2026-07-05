-- ============================================================
-- POST GİZLİLİĞİ — Herkese açık / Sadece takipçiler
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- 1) visibility kolonu (varsayılan 'public' — mevcut postlar etkilenmez)
alter table public.posts add column if not exists visibility text not null default 'public';

alter table public.posts drop constraint if exists posts_visibility_check;
alter table public.posts add constraint posts_visibility_check
    check (visibility in ('public', 'followers'));

create index if not exists posts_visibility_idx on public.posts (visibility);

-- 2) RLS — service-role backend bypass eder ama defense-in-depth için politika
-- da gerçek görünürlük mantığını yansıtsın: herkese açık VEYA kendi postun
-- VEYA (sadece takipçilere özel VE sen o kişiyi takip ediyorsan).
drop policy if exists "posts read" on public.posts;
create policy "posts read" on public.posts for select
    using (
        visibility = 'public'
        or user_id = auth.uid()
        or (
            visibility = 'followers'
            and exists (
                select 1 from public.follows f
                where f.follower_id = auth.uid() and f.following_id = posts.user_id
            )
        )
    );

-- 3) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
