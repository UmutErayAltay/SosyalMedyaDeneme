-- Mesaj iletme (forward): iletilen mesajlarda "İletildi" etiketi için bayrak

alter table public.messages
add column if not exists is_forwarded boolean not null default false;
