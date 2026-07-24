-- ============================================================
-- KULLANILMAYAN INDEX TEMİZLİĞİ (SADECE gerçek duplikasyon/kapsama)
-- get_advisors(type="performance") 32 "unused_index" bulgusu raporladı.
-- Bu proje düşük trafikli (arkadaş grubu ölçeği) — "kullanılmıyor" demek
-- "gereksiz" demek değil, çoğu sorgu paterni henüz az çalıştı. Bu yüzden
-- SADECE aşağıdaki 4 index silindi, ikisi de tek başına dar kapsamlı ve
-- ZATEN VAR OLAN bir PRIMARY KEY composite index'i tarafından tamamen
-- kapsanıyor (leftmost column eşleşmesi) — geri kalan 28 unused index'e
-- dokunulmadı (rapora bakınız).
--
-- 1) idx_poll_votes_poll_id + poll_votes_poll_idx
--    İkisi de tek kolon (poll_id) btree, birbirinin BİREBİR aynısı
--    (advisor "duplicate_index" olarak da ayrıca işaretledi) VE
--    poll_votes_pkey UNIQUE INDEX (poll_id, user_id) tarafından zaten
--    kapsanıyor (poll_id leftmost). İkisi de gereksiz.
-- 2) idx_comment_reactions_comment_id
--    Tek kolon (comment_id) btree; comment_reactions_pkey UNIQUE INDEX
--    (comment_id, user_id) tarafından kapsanıyor (comment_id leftmost).
-- 3) idx_message_reactions_message_id
--    Tek kolon (message_id) btree; message_reactions_pkey UNIQUE INDEX
--    (message_id, user_id) tarafından kapsanıyor (message_id leftmost).
--
-- NOT: idx_poll_votes_user_id, idx_comment_reactions_user_id,
-- idx_message_reactions_user_id gibi "diğer taraf" tekil kolon index'leri
-- BİLEREK silinmedi — PK'daki ikinci kolon oldukları için leftmost eşleşme
-- sağlamıyorlar, farklı bir sorgu paterni (sadece user_id ile arama) için
-- hâlâ gerekliler.
-- Idempotent (IF EXISTS).
-- ============================================================

drop index if exists public.idx_poll_votes_poll_id;
drop index if exists public.poll_votes_poll_idx;
drop index if exists public.idx_comment_reactions_comment_id;
drop index if exists public.idx_message_reactions_message_id;
