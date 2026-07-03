-- ============================================================
-- ÇOKLU GÖRSEL DESTEĞİ — image_urls ARRAY kolonu
-- Supabase Dashboard → SQL Editor'de çalıştır.
-- Idempotent (tekrar çalıştırılabilir).
-- ============================================================

-- 1) posts tablosuna image_urls ARRAY kolonu ekle (yoksa)
alter table public.posts add column if not exists image_urls text[] default '{}';

-- 2) Eski tekil image_url verisini image_urls'a taşı (sadece image_urls boş olanlara)
update public.posts
set image_urls = array[image_url]
where image_url is not null
  and (image_urls is null or array_length(image_urls, 1) is null);

-- 3) PostgREST schema cache yenile
NOTIFY pgrst, 'reload schema';
