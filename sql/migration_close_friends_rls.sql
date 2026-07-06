-- Update posts RLS policy to support close_friends visibility.
-- Adds close_friends check to existing public/followers/self visibility logic.

drop policy if exists "posts read" on public.posts;
create policy "posts read" on public.posts for select
    using (
        visibility = 'public'
        or user_id = auth.uid()
        or (
            visibility = 'followers'
            and exists (
                select 1 from public.follows f
                where f.follower_id = auth.uid() and f.following_id = posts.user_id
            )
        )
        or (
            visibility = 'close_friends'
            and exists (
                select 1 from public.close_friends cf
                where cf.owner_id = posts.user_id and cf.friend_id = auth.uid()
            )
        )
    );

NOTIFY pgrst, 'reload schema';
