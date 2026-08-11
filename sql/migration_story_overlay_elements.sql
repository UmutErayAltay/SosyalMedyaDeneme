-- Hikaye üzerine serbest konumlandırılabilir GIF/etiket görsel katmanları
-- (overlay) — ÇOKLU eleman desteği. Önceki migration
-- (migration_story_overlay_image.sql) TEK bir overlay için 4 skaler kolon
-- eklemişti; native tarafı ikinci bir GIF/sticker eklendiğinde birincinin
-- YERİNE geçtiğini bildirdi (tek kolon = tek değer). Bugün eklendiği ve
-- CANLI KULLANICI VERİSİ olmadığı için geriye dönük uyumluluk gözetilmeden
-- DÜZ DEĞİŞİM yapılıyor: 4 skaler kolon DÜŞÜRÜLÜP tek bir `jsonb` dizi
-- kolonuyla değiştiriliyor (en fazla 3 eleman, uygulama katmanında
-- sınırlanır — bkz. app/api_v1/stories.py::api_create_story). Her eleman
-- {"url": str, "position_x": 0..1, "position_y": 0..1, "scale": 0.3..3}
-- şeklinde. NULL = overlay yok ("yokluk = render etme" deseni AYNEN korunur,
-- boş dizi [] yerine NULL tercih edilir).

alter table public.stories
drop column if exists overlay_image_url;

alter table public.stories
drop column if exists overlay_image_position_x;

alter table public.stories
drop column if exists overlay_image_position_y;

alter table public.stories
drop column if exists overlay_image_scale;

alter table public.stories
add column if not exists overlay_elements jsonb default null;

-- ROLLBACK:
-- alter table public.stories drop column if exists overlay_elements;
-- alter table public.stories add column if not exists overlay_image_url text;
-- alter table public.stories add column if not exists overlay_image_position_x double precision;
-- alter table public.stories add column if not exists overlay_image_position_y double precision;
-- alter table public.stories add column if not exists overlay_image_scale double precision;
