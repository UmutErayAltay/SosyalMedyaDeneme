-- GERİ KAZANILDI (2026-07-17): apply_migration ile uygulanmış (version
-- 20260714203054) ama dosya olarak repoya hiç eklenmemiş — kurtarıldı.
-- TEKRAR UYGULANMASINA GEREK YOK, zaten canlıda (IF NOT EXISTS olduğu
-- için tekrar çalıştırılsa da zararsız).
alter table public.notification_preferences
add column if not exists notify_follow_request boolean not null default true;

alter table public.notification_preferences
add column if not exists notify_follow_accept boolean not null default true;
