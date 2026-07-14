-- Hikaye anketinin serbest sürükle-bırak konumu + boyutu.
-- position_x/y: hikaye canvas'ına göre 0-1 arası oran (görüntüleyici boyutundan
-- bağımsız, her ekranda aynı göreli yerde çıkar). scale: 0.5-2.0 arası çarpan.
-- Post anketlerinde bu kolonlar hep varsayılan kalır (sadece hikaye anketi kullanır).
alter table public.polls add column if not exists position_x real not null default 0.5;
alter table public.polls add column if not exists position_y real not null default 0.75;
alter table public.polls add column if not exists scale real not null default 1.0;

NOTIFY pgrst, 'reload schema';
