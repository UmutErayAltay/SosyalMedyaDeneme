-- ============================================================
-- SABİTLENMİŞ POST — profilde en üstte gösterilecek 1 post.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

-- Tek kolon (birden fazla değil) — doğası gereği "en fazla 1 sabit post"
-- kısıtını ayrı bir UNIQUE/partial-index kurallarına gerek kalmadan garanti eder.
alter table public.profiles
    add column if not exists pinned_post_id uuid references public.posts(id) on delete set null;

NOTIFY pgrst, 'reload schema';
