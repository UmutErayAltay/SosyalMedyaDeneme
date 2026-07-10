-- Bildirim rozetlerinin gerçek-zamanlı tazelenmesi: notifications tablosu
-- supabase_realtime yayınına eklenir. İstemci postgres_changes ile kendi
-- satırlarına abone olur (recipient_id=eq.<uid>); RLS SELECT policy'si
-- ("notifications read": recipient_id = auth.uid()) zaten var — WALRUS
-- başka kullanıcının bildirimini asla iletmez (messages tablosuyla aynı
-- çalışan desen). İdempotent: yayında zaten varsa dokunmaz.
-- UYGULANDI: 2026-07-11 (Supabase MCP apply_migration)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime' AND tablename = 'notifications'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.notifications;
    END IF;
END $$;
