-- Mesaj bildirimleri artık sohbet başına tek satırda toplanıyor ("Ali sana
-- N mesaj gönderdi") — her mesaj için ayrı satır açmak hem bildirim
-- listesini hem navbar rozetini mesaj sayısı kadar şişiriyordu (kullanıcı
-- isteği). `count` bu satırın kaç mesajı temsil ettiğini tutar; diğer
-- bildirim türleri için hep 1 kalır. Idempotent.

alter table notifications add column if not exists count int not null default 1;
