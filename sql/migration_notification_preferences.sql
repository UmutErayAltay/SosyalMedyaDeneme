-- Bildirim tercihleri: kullanıcı hangi bildirim türlerini almak istediğini
-- kapatabilir (opt-out modeli). Satır olmayan kullanıcı = hiç tercih
-- belirlememiş = tüm bildirimler varsayılan AÇIK (backend bunu satır-yoksa-
-- hepsi-true mantığıyla ele alır, bu yüzden burada varsayılan satır
-- oluşturulmaz — lazy: sadece kullanıcı ayar değiştirdiğinde satır oluşur).
-- Idempotent.

create table if not exists notification_preferences (
    user_id uuid primary key references profiles(id) on delete cascade,
    notify_like boolean not null default true,
    notify_comment boolean not null default true,
    notify_reply boolean not null default true,
    notify_comment_like boolean not null default true,
    notify_follow boolean not null default true,
    notify_message boolean not null default true,
    notify_mention boolean not null default true,
    notify_hashtag_post boolean not null default true,
    updated_at timestamptz not null default now()
);

alter table notification_preferences enable row level security;

-- Service-role backend RLS'i bypass eder (defense-in-depth için politika
-- yine de tanımlanır) — kullanıcı SADECE kendi tercih satırını
-- görebilir/oluşturabilir/güncelleyebilir. Delete gereksiz: satır
-- kullanıcıyla birlikte cascade silinir.
drop policy if exists "notification_preferences_select_own" on notification_preferences;
create policy "notification_preferences_select_own" on notification_preferences
    for select using (auth.uid() = user_id);

drop policy if exists "notification_preferences_insert_own" on notification_preferences;
create policy "notification_preferences_insert_own" on notification_preferences
    for insert with check (auth.uid() = user_id);

drop policy if exists "notification_preferences_update_own" on notification_preferences;
create policy "notification_preferences_update_own" on notification_preferences
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
