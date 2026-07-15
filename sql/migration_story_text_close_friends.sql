-- Salt-metin hikâye + yakın arkadaşlar hikâye görünürlüğü
-- background_color: medyasız (salt-metin) hikayenin arka plan rengi (#hex)
-- visibility: 'public' (mevcut davranış) | 'close_friends' (sadece yakın
--             arkadaşlar listesindekiler + sahibi görür; posts'taki
--             close_friends deseninin hikaye karşılığı)

alter table public.stories
add column if not exists background_color text;

alter table public.stories
add column if not exists visibility text not null default 'public';

alter table public.stories drop constraint if exists stories_visibility_check;
alter table public.stories add constraint stories_visibility_check check (
    visibility = any (array['public'::text, 'close_friends'::text])
);
