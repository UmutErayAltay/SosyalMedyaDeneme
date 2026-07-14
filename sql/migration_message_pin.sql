-- Sohbette mesaj sabitleme: pinned_at zaman damgası (null = sabitli değil)
-- Herhangi bir katılımcı sabitleyebilir/kaldırabilir; sohbet başlığının
-- altındaki şerit en son sabitlenen mesajı gösterir.

alter table public.messages
add column if not exists pinned_at timestamptz;
