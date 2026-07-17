-- Hesap deaktivasyonu: profiles tablosuna is_deactivated kolonu ekle
alter table public.profiles
add column if not exists is_deactivated boolean not null default false;

-- Deaktive edilen hesapları filtreleyen sorguları hızlandır
create index if not exists profiles_is_deactivated_idx on public.profiles(is_deactivated)
where is_deactivated = true;

NOTIFY pgrst, 'reload schema';
