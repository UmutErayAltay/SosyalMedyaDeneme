-- Mesaj alıntılama/cevap özelliği: reply_to_id kolonu ekle
-- Bir mesaj başka bir mesaja cevap verebilir (reply/quote)
-- on delete set null: orijinal mesaj silinirse sadece bağlantı koparsın, cevap kalır

alter table public.messages
add column if not exists reply_to_id uuid references public.messages(id) on delete set null;
