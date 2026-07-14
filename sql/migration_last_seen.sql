-- Kalıcı "son görüldü" zaman damgası: last_seen_at kolonu ekle
-- Kullanıcının son aktif olduğu zaman, in-memory state'ten veritabanına kalıcı olarak kayıtlı

alter table public.profiles
add column if not exists last_seen_at timestamptz;
