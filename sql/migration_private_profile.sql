-- ============================================================
-- Gizli/özel profil modu: profiles.is_private + follows.status.
-- Gerçek kapı backend/RPC tarafında (service-role); buradaki RLS
-- güncellemesi defense-in-depth (realtime broadcast kanalları gibi
-- runtime yetkilendirme kapısı DEĞİL). Idempotent.
-- ============================================================

alter table public.profiles add column if not exists is_private boolean not null default false;

-- Mevcut follows satırları zaten karşılıklı kurulmuş kabul edilir (geriye
-- dönük uyumluluk) — backfill DEFAULT 'accepted'. Yeni insert'lerde backend
-- hedef is_private ise 'pending' yazacak (bkz. app/social.py toggle_follow).
alter table public.follows add column if not exists status text not null default 'accepted';
alter table public.follows drop constraint if exists follows_status_check;
alter table public.follows add constraint follows_status_check check (status in ('pending', 'accepted'));

NOTIFY pgrst, 'reload schema';
