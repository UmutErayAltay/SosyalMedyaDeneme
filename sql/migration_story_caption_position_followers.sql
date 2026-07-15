-- Hikaye altyazısı (caption) sürüklenebilir konum + "sadece takipçiler"
-- görünürlüğü.
-- caption_position_x/y: anket widget'ındaki position_x/position_y ile AYNI
-- desen (0-1 arası oran, composer'da sürüklenip görüntüleyicide aynı orana
-- göre konumlandırılır). visibility check constraint'ine 'followers'
-- eklendi — posts'taki 3 seviyeli görünürlükle (public/followers/close_friends)
-- artık hikayeler de aynı seçenekleri destekliyor.

alter table public.stories
add column if not exists caption_position_x double precision not null default 0.5;

alter table public.stories
add column if not exists caption_position_y double precision not null default 0.75;

alter table public.stories drop constraint if exists stories_visibility_check;
alter table public.stories add constraint stories_visibility_check check (
    visibility = any (array['public'::text, 'followers'::text, 'close_friends'::text])
);
