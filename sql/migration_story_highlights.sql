-- ============================================================
-- HİKAYE HIGHLIGHT'LARI (Öne Çıkanlar) — Şema migration
-- Supabase MCP apply_migration ile uygulanır. Idempotent.
-- ============================================================

-- story_highlights: profildeki kalıcı koleksiyon (Instagram tarzı "öne
-- çıkan"). stories.id'ye FK YOK — items medyayı KOPYALAYARAK saklar, çünkü
-- orijinal hikaye 24 saat sonra silinse bile (bkz. migration_stories.sql
-- _cleanup_expired_stories) highlight kalıcı kalmalı.
create table if not exists public.story_highlights (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references public.profiles(id) on delete cascade,
    title      text not null,
    cover_url  text,
    created_at timestamptz not null default now()
);

create index if not exists story_highlights_user_idx on public.story_highlights (user_id, created_at desc);

-- story_highlight_items: highlight içindeki tek tek hikaye kopyaları.
-- Doğrudan user_id kolonu yok — sahiplik her zaman highlight_id üzerinden
-- dolaylı kontrol edilir (RLS'te subquery ile).
create table if not exists public.story_highlight_items (
    id                  uuid primary key default gen_random_uuid(),
    highlight_id        uuid not null references public.story_highlights(id) on delete cascade,
    image_url           text,
    video_url           text,
    caption             text,
    original_created_at timestamptz,
    added_at            timestamptz not null default now()
);

create index if not exists story_highlight_items_highlight_idx on public.story_highlight_items (highlight_id, added_at);

alter table public.story_highlights enable row level security;
alter table public.story_highlight_items enable row level security;

drop policy if exists "story_highlights read"   on public.story_highlights;
drop policy if exists "story_highlights insert" on public.story_highlights;
drop policy if exists "story_highlights update" on public.story_highlights;
drop policy if exists "story_highlights delete" on public.story_highlights;
create policy "story_highlights read"   on public.story_highlights for select using (true);
create policy "story_highlights insert" on public.story_highlights for insert with check (auth.uid() = user_id);
create policy "story_highlights update" on public.story_highlights for update using (auth.uid() = user_id);
create policy "story_highlights delete" on public.story_highlights for delete using (auth.uid() = user_id);

-- items tablosunda user_id olmadığı için sahiplik kontrolü highlight
-- üzerinden subquery ile yapılır (aynı highlight'ın sahibi mi).
drop policy if exists "story_highlight_items read"   on public.story_highlight_items;
drop policy if exists "story_highlight_items insert" on public.story_highlight_items;
drop policy if exists "story_highlight_items delete" on public.story_highlight_items;
create policy "story_highlight_items read" on public.story_highlight_items for select using (true);
create policy "story_highlight_items insert" on public.story_highlight_items for insert with check (
    exists (
        select 1 from public.story_highlights h
        where h.id = highlight_id and h.user_id = auth.uid()
    )
);
create policy "story_highlight_items delete" on public.story_highlight_items for delete using (
    exists (
        select 1 from public.story_highlights h
        where h.id = highlight_id and h.user_id = auth.uid()
    )
);

NOTIFY pgrst, 'reload schema';
