-- Aktif oturumlar + uzaktan çıkış: her başarılı girişte bir satır açılır,
-- kullanıcı profil düzenleme sayfasından oturumlarını listeleyip tek tek
-- (veya "diğer tümü") sonlandırabilir. Satır silinince ilgili tarayıcı
-- oturumu bir sonraki istekte (en geç ~60sn, throttle penceresi) düşürülür.

create table if not exists public.user_sessions (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null references public.profiles(id) on delete cascade,
    created_at     timestamptz not null default now(),
    last_active_at timestamptz not null default now(),
    user_agent     text,
    ip             text
);

create index if not exists idx_user_sessions_user on public.user_sessions(user_id);

-- RLS: backend service-role ile çalışır (bypass) — politikalar defense-in-depth
-- (proje deseni: her tabloda RLS açık kalır). Sahip sadece KENDİ oturumlarını
-- görür/siler; insert backend'den geldiği için authenticated'a açık değil.
alter table public.user_sessions enable row level security;

drop policy if exists "sessions owner select" on public.user_sessions;
create policy "sessions owner select" on public.user_sessions
    for select using (user_id = auth.uid());

drop policy if exists "sessions owner delete" on public.user_sessions;
create policy "sessions owner delete" on public.user_sessions
    for delete using (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';
