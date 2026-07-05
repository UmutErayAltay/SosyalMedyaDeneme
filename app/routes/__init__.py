"""Ana rotalar paketi — tek bir `bp` (blueprint adı: "routes") altında
birden fazla dosyaya bölünmüş: dosya sayısı arttıkça (Sprint 1→27) routes.py
760 satıra çıkmıştı, okunabilirlik için konuya göre ayrıştırıldı:

- `posts.py`    — feed, post paylaşma/düzenleme/silme/taslak/sabitleme
- `profile.py`  — profil sayfası, takipçi listeleri, profil düzenleme, istatistikler
- `discovery.py`— arama, algoritmik keşfet
- `_common.py`  — yukarıdaki üçünün paylaştığı küçük yardımcılar

ÖNEMLİ: Bölünme SADECE dosya organizasyonu — endpoint isimleri (`routes.feed`,
`routes.profile` vb.) ve URL'ler DEĞİŞMEDİ, çünkü hepsi hâlâ AYNI `bp` nesnesine
route ekliyor (hangi dosyada tanımlandığı fark etmez). Bu yüzden şablonlardaki
ve diğer modüllerdeki onlarca `url_for('routes.xxx')` çağrısının HİÇBİRİNE
dokunmaya gerek kalmadı.
"""
from flask import Blueprint

bp = Blueprint("routes", __name__)

# hashtags.py `_attach_post_metrics`'i döngüsel import'u önlemek için lazy
# import ediyor: `from .routes import _attach_post_metrics`. Paket haline
# gelince de bu import yolunun çalışmaya devam etmesi için re-export edilir.
from ._common import _attach_post_metrics  # noqa: E402,F401

# Alt modüller `from . import bp` ile bu paketin `bp`sine route ekler —
# import edilmeleri (yan etkili) bu satırlarda tetiklenir, en sonda olmalı
# (yukarıdaki `bp` tanımından SONRA, aksi halde döngüsel import patlar).
from . import posts, profile, discovery  # noqa: E402,F401
