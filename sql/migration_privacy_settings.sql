-- Gizlilik ayarları: "son görüldü" gizleme toggle'ı
-- (is_private zaten migration_private_profile.sql'de var; bu dosya sadece
--  hide_last_seen ekler — profil düzenleme sayfasındaki Gizlilik bölümü)

alter table public.profiles
add column if not exists hide_last_seen boolean not null default false;
