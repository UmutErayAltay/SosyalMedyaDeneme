-- group_admin.py leave_group() önceden 3 ayrı HTTP round-trip'e bölünmüştü
-- (delete -> remaining'i select et -> koşullu admin ata). İki üye/admin AYNI
-- grup'tan AYNI ANDA ayrılırsa bu read-then-act deseni race condition'a açıktı
-- (en kötü durumda zararsız/redundant bir promote, ama düzeltmek ucuz).
-- pg_advisory_xact_lock(conversation_id hash'i) ile aynı konuşma için eşzamanlı
-- çağrılar transaction bitene kadar serialize edilir; tüm mantık TEK bir
-- fonksiyon çağrısında (dolayısıyla tek transaction'da) çalışır.
create or replace function public.leave_group_and_reassign_admin(p_conversation_id uuid, p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_was_admin boolean;
begin
  perform pg_advisory_xact_lock(hashtext(p_conversation_id::text));

  select is_admin into v_was_admin from conversation_participants
  where conversation_id = p_conversation_id and user_id = p_user_id;

  delete from conversation_participants
  where conversation_id = p_conversation_id and user_id = p_user_id;

  if not exists (select 1 from conversation_participants where conversation_id = p_conversation_id) then
    delete from conversations where id = p_conversation_id;
    return;
  end if;

  if v_was_admin and not exists (
    select 1 from conversation_participants
    where conversation_id = p_conversation_id and is_admin = true
  ) then
    update conversation_participants
    set is_admin = true
    where conversation_id = p_conversation_id
      and user_id = (
        select user_id from conversation_participants
        where conversation_id = p_conversation_id
        order by created_at
        limit 1
      );
  end if;
end;
$$;

NOTIFY pgrst, 'reload schema';
