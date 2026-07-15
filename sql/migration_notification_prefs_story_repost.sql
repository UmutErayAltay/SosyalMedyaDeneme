-- notification_preferences'a eksik opt-out kolonları.
-- NOT: NOTIFICATION_TYPES listesine (app/notifications.py) yeni tipler
-- eklendikçe (follow_request/follow_accept, story_reaction) kolonları hiç
-- oluşturulmamıştı — tercih sayfasının POST'u upsert'te bilinmeyen kolon
-- yüzünden O GÜNDEN BERİ sessizce başarısız oluyordu ("Bildirim tercihleri
-- henüz kullanılamıyor" flash'ı). Repost sprint'inde fark edilip TÜM eksikler
-- birden tamamlandı.

alter table public.notification_preferences
add column if not exists notify_follow_request boolean not null default true;

alter table public.notification_preferences
add column if not exists notify_follow_accept boolean not null default true;

alter table public.notification_preferences
add column if not exists notify_story_reaction boolean not null default true;

alter table public.notification_preferences
add column if not exists notify_repost boolean not null default true;
