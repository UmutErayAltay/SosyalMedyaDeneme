-- Reels özelliği: dikey kısa video akışı (posts tablosuna is_reel kolonu)
alter table public.posts
add column if not exists is_reel boolean not null default false;

-- Reels akışı sorguları hızlandır (video + is_reel = true olanları en yeniyi önce getir)
create index if not exists posts_is_reel_created_idx on public.posts(created_at desc)
where is_reel = true and video_url is not null;

NOTIFY pgrst, 'reload schema';
