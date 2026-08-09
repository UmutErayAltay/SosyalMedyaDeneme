-- Hikaye üzerine serbest konumlandırılabilir GIF/sticker görsel katmanı
-- (overlay). caption_position_x/y ve poll'daki position_x/y/scale ile AYNI
-- desen (0-1 arası oran) ama caption'dan FARKLI olarak burada NOT NULL +
-- default YOK — 4 kolon da NULLABLE, overlay'i olmayan bir hikayede hepsi
-- null kalır ("yokluk = render etme" deseni, mevcut background_color'ın
-- nullable oluşuyla aynı yaklaşım). overlay_image_url CLIENT'ın önceden
-- yüklediği/çözdüğü bir GIF CDN veya sticker görsel URL'i — burada YENİ bir
-- dosya yükleme akışı YOK (posts'taki gif_url ile aynı "sadece URL" deseni).

alter table public.stories
add column if not exists overlay_image_url text;

alter table public.stories
add column if not exists overlay_image_position_x double precision;

alter table public.stories
add column if not exists overlay_image_position_y double precision;

alter table public.stories
add column if not exists overlay_image_scale double precision;

-- ROLLBACK:
-- alter table public.stories drop column if exists overlay_image_url;
-- alter table public.stories drop column if exists overlay_image_position_x;
-- alter table public.stories drop column if exists overlay_image_position_y;
-- alter table public.stories drop column if exists overlay_image_scale;
