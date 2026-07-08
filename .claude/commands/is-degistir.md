---
description: İş değişimi — oturumu adlandır ve temiz başlangıç (/clear) öner
disable-model-invocation: true
---
Kullanıcı farklı bir işe geçiyor:

1. Mevcut işin durumunu 1-2 cümleyle kapat (bitti mi, nerede kaldı?). Kalıcı olması gereken bağlam varsa ŞİMDİ `.context/active_context.md`'ye yaz.
2. Kullanıcıya sırayla şunları öner (ikisini de kendin çalıştıramazsın):
   - `/rename <biten-işin-kısa-adı>` — oturum geçmişte bulunabilir kalsın
   - `/clear` — yeni iş için temiz başlangıç. İlgisiz işe geçerken compact DEĞİL clear: eski bağlamı taşımak her mesajda token yakar, özet bile gereksiz yüktür.
