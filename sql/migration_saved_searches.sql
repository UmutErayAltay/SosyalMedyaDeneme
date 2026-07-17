-- Kayıtlı arama: kullanıcıların sık aramaları kalıcı olarak kaydetmesi
create table if not exists public.saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  query text not null,
  label text,
  created_at timestamptz not null default now()
);

-- Kullanıcı başına sorguları hızlandır
create index if not exists saved_searches_user_id_idx on public.saved_searches(user_id);

-- Tarihle filtreleme hızlı olsun
create index if not exists saved_searches_user_created_idx on public.saved_searches(user_id, created_at desc);

-- Row Level Security etkinleştir
alter table public.saved_searches enable row level security;

-- Kullanıcı kendi kayıtlı aramalarını görebilir
drop policy if exists "users_see_own_saved_searches" on public.saved_searches;
create policy "users_see_own_saved_searches"
on public.saved_searches for select
to authenticated
using (user_id = auth.uid());

-- Kullanıcı kendi kayıtlı araması oluşturabilir
drop policy if exists "users_create_own_saved_searches" on public.saved_searches;
create policy "users_create_own_saved_searches"
on public.saved_searches for insert
to authenticated
with check (user_id = auth.uid());

-- Kullanıcı kendi kayıtlı aramasını güncelleyebilir
drop policy if exists "users_update_own_saved_searches" on public.saved_searches;
create policy "users_update_own_saved_searches"
on public.saved_searches for update
to authenticated
using (user_id = auth.uid())
with check (user_id = auth.uid());

-- Kullanıcı kendi kayıtlı aramasını silebilir
drop policy if exists "users_delete_own_saved_searches" on public.saved_searches;
create policy "users_delete_own_saved_searches"
on public.saved_searches for delete
to authenticated
using (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';
