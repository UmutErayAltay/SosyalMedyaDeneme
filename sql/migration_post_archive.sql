-- ============================================================
-- Gönderi arşivleme: is_draft ile aynı desen (soft-flag, gerçek DELETE
-- değil). Feed/keşfet/profil/arama RPC+Python fallback filtrelerine
-- is_draft'ın göründüğü her yere ayrıca eklenir (bkz. app/routes/posts.py,
-- discovery.py, profile.py, sql/migration_feed_rpc.sql,
-- migration_discover_profile_rpc.sql). Idempotent.
-- ============================================================

alter table public.posts add column if not exists is_archived boolean not null default false;
alter table public.posts add column if not exists archived_at timestamptz;

NOTIFY pgrst, 'reload schema';
