-- Native app (Faz 1 — token tabanlı JSON API) için opak bearer token'lar.
-- Ham token SADECE üretim anında (login) client'a bir kez döner ve DB'de HİÇ
-- tutulmaz — burada saklanan token_hash, o ham token'ın tek yönlü hash'i
-- (örn. sha256). Backend her istekte gelen Authorization header'ındaki
-- ham token'ı aynı algoritmayla hash'leyip token_hash + revoked_at is null
-- ile arar. Böylece DB dump/sızıntısı olsa bile ham token geri çıkarılamaz
-- (session-cookie mimarisine ek, ayrı bir kimlik kanalı — CSRF'ye tabi değil,
-- bu yüzden token'ın kendisi sır olarak kalmalı).

create table if not exists public.api_tokens (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null references public.profiles(id) on delete cascade,
    token_hash     text not null unique,
    device_name    text,
    created_at     timestamptz not null default now(),
    last_used_at   timestamptz,
    revoked_at     timestamptz
);

create index if not exists idx_api_tokens_user on public.api_tokens(user_id);

-- Backend her istekte SADECE token_hash ile arayıp revoked_at is null şartını
-- kontrol edecek — kısmi index bu sorguyu (aktif token'lar) hızlandırır.
-- unique kısıtı (token_hash) zaten tam eşleşmeyi garanti ediyor, bu index
-- "aktif token" filtresini ucuzlaştırmak için ayrıca var.
create index if not exists idx_api_tokens_hash_active on public.api_tokens(token_hash)
    where revoked_at is null;

-- RLS: backend service-role ile çalışır (bypass) — politikalar defense-in-depth
-- (proje deseni: her tabloda RLS açık kalır). Sahip sadece KENDİ token'larını
-- görür/siler (örn. profil ayarlarından "bu cihazı çıkış yaptır"); insert/
-- update backend'den (login/last_used_at güncellemesi) geldiği için
-- authenticated'a açık değil.
alter table public.api_tokens enable row level security;

drop policy if exists "api_tokens owner select" on public.api_tokens;
create policy "api_tokens owner select" on public.api_tokens
    for select using (user_id = auth.uid());

drop policy if exists "api_tokens owner delete" on public.api_tokens;
create policy "api_tokens owner delete" on public.api_tokens
    for delete using (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';

-- ROLLBACK:
-- drop table if exists public.api_tokens;
