-- Arama geçmişi: kullanıcı başına son aramalar (silinebilir, tekli veya toplu).
-- Idempotent.

create table if not exists search_history (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles(id) on delete cascade,
    query text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_search_history_user_created
    on search_history(user_id, created_at desc);

alter table search_history enable row level security;

-- Service-role backend RLS'i bypass eder (defense-in-depth için politika
-- yine de tanımlanır) — kullanıcı SADECE kendi arama geçmişini görebilir/silebilir.
drop policy if exists "search_history_select_own" on search_history;
create policy "search_history_select_own" on search_history
    for select using (auth.uid() = user_id);

drop policy if exists "search_history_insert_own" on search_history;
create policy "search_history_insert_own" on search_history
    for insert with check (auth.uid() = user_id);

drop policy if exists "search_history_delete_own" on search_history;
create policy "search_history_delete_own" on search_history
    for delete using (auth.uid() = user_id);
