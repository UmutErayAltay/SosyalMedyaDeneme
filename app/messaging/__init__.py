"""Birebir mesajlaşma (DM) + grup sohbeti + Paylaşım özellikleri — tek bir
`bp` (blueprint adı: "messaging") altında konuya göre birden fazla dosyaya
bölünmüş (475 satırlık tek dosya okunabilirlik için ayrıştırıldı):

- `views.py`    — inbox() + conversation() (görüntüleme)
- `sending.py`  — send_message()/share_post()/share_post_multiple() (gönderme)
- `creation.py` — start_conversation()/create_group()/share_targets() (başlatma)
- `_common.py`  — yukarıdakilerin paylaştığı yardımcılar (route yok)

Model: bir 'conversation' satırı, conversation_participants (many-to-many)
üzerinden N kullanıcıya bağlanır — 1:1 DM de grup sohbeti de AYNI şema,
sadece conversations.is_group/name grup meta bilgisini taşır (bkz.
sql/migration_group_chat.sql).

Grup sohbetinde okundu bilgisi (✓✓) BİLEREK gösterilmiyor — messages.read_at
tek bir kolon, "kim okudu" bilgisini tutamaz (N kişiden biri okuyunca ✓✓
göstermek yanıltıcı olurdu). 1:1'de mevcut davranış aynen korunuyor.

Bölünme SADECE dosya organizasyonu — endpoint isimleri (`messaging.inbox`,
`messaging.conversation` vb.) ve URL'ler DEĞİŞMEDİ, hepsi hâlâ AYNI `bp`
nesnesine route ekliyor (bkz. `app/routes/__init__.py`'deki aynı desen notu).
"""
from flask import Blueprint

bp = Blueprint("messaging", __name__)

from . import views, sending, creation  # noqa: E402,F401
