-- ============================================================
-- ADMİN PANELİ — profiles.is_admin/is_banned + reports.status/resolved_*.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

alter table public.profiles add column if not exists is_admin  boolean not null default false;
alter table public.profiles add column if not exists is_banned boolean not null default false;

-- Kullanıcı 2026-07-06'da onayladı: "admin" kullanıcı adı ilk admin olarak işaretlenir.
update public.profiles set is_admin = true where username = 'admin';

alter table public.reports add column if not exists status text not null default 'pending';
alter table public.reports drop constraint if exists reports_status_check;
alter table public.reports add constraint reports_status_check
    check (status in ('pending', 'reviewed', 'dismissed'));
alter table public.reports add column if not exists resolved_by uuid references public.profiles(id) on delete set null;
alter table public.reports add column if not exists resolved_at timestamptz;

create index if not exists reports_status_idx on public.reports (status);

NOTIFY pgrst, 'reload schema';
