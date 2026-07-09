-- ============================================================
-- YORUM EMOJİ TEPKİLERİ
-- message_reactions ile birebir aynı desen (bkz.
-- migration_reactions_schedule_location.sql), yorumlara uyarlanmış.
-- comment_likes (♥ beğeni) tablosundan AYRI bir katman — o kalır.
-- Supabase Dashboard → SQL Editor'de çalıştır. Idempotent.
-- ============================================================

create table if not exists public.comment_reactions (
  comment_id uuid not null references public.comments(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  reaction text not null,
  created_at timestamp with time zone default now(),
  primary key (comment_id, user_id)
);

create index if not exists idx_comment_reactions_comment_id
  on public.comment_reactions(comment_id);

create index if not exists idx_comment_reactions_user_id
  on public.comment_reactions(user_id);

alter table public.comment_reactions enable row level security;

-- Herkes okuyabilir (yorumlar zaten herkese görünür bağlamda; post
-- görünürlük kontrolü app katmanında yapılıyor, comment_likes ile aynı desen)
drop policy if exists "comment_reactions_select" on public.comment_reactions;
create policy "comment_reactions_select"
  on public.comment_reactions for select
  using (true);

drop policy if exists "comment_reactions_insert" on public.comment_reactions;
create policy "comment_reactions_insert"
  on public.comment_reactions for insert
  with check (user_id = auth.uid());

drop policy if exists "comment_reactions_delete" on public.comment_reactions;
create policy "comment_reactions_delete"
  on public.comment_reactions for delete
  using (user_id = auth.uid());

notify pgrst, 'reload schema';
