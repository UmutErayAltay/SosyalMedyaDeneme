-- ============================================================
-- Web Push abonelikleri: tarayıcının PushManager.subscribe() ile ürettiği
-- {endpoint, keys:{p256dh, auth}} üçlüsü. Bir kullanıcının birden fazla
-- cihazı/tarayıcısı olabilir, bu yüzden user_id başına çoklu satır.
-- ============================================================

create table if not exists public.push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_push_subscriptions_user_id
  on public.push_subscriptions(user_id);

alter table public.push_subscriptions enable row level security;

-- Service-role backend RLS'i bypass eder (defense-in-depth için politikalar
-- yine de tanımlı) — kullanıcı sadece kendi abonelik satırlarını görebilir/silebilir.
drop policy if exists "push_subscriptions_select_own" on public.push_subscriptions;
create policy "push_subscriptions_select_own"
  on public.push_subscriptions for select
  using (auth.uid() = user_id);

drop policy if exists "push_subscriptions_insert_own" on public.push_subscriptions;
create policy "push_subscriptions_insert_own"
  on public.push_subscriptions for insert
  with check (auth.uid() = user_id);

drop policy if exists "push_subscriptions_delete_own" on public.push_subscriptions;
create policy "push_subscriptions_delete_own"
  on public.push_subscriptions for delete
  using (auth.uid() = user_id);
