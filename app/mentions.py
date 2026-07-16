"""@kullanıcı etiketleme: çıkarma, bildirim, güvenli render.

Hashtag'e benzer desen (bkz. hashtags.py) ama ayrı bir tabloya ihtiyaç yok —
mention'ın tek işi geçerli bir kullanıcı adına link vermek + o kullanıcıya
bildirim göndermek (keşfet/listeleme sayfası istenmedi, kapsam dışı).
"""
import re
from flask import url_for
from markupsafe import Markup, escape
from .notifications import notify

# Kayıtta username formatı zorlanmıyor (bkz. auth.py) — gerçek kullanıcı adlarında
# nokta görülüyor (ör. "umut.test2025"), bu yüzden sadece \w yetmez, nokta/tire de
# dahil. Python 3'te \w zaten Unicode farkında (ç, ğ, ı, ö, ş, ü dahil).
MENTION_RE = re.compile(r"@([\w.-]+)", re.UNICODE)


def extract_mentions(content: str) -> list[str]:
    """İçerikten benzersiz, küçük harfli kullanıcı adlarını (sırayı koruyarak) çıkarır."""
    if not content:
        return []
    seen: list[str] = []
    for m in MENTION_RE.finditer(content):
        uname = m.group(1).lower()
        if uname not in seen:
            seen.append(uname)
    return seen


def notify_mentions(sb, *, actor_id: str, content: str,
                     post_id: str | None = None, comment_id: str | None = None,
                     conversation_id: str | None = None,
                     allowed_ids: set | None = None) -> None:
    """İçerikte geçen, GERÇEKTEN VAR OLAN kullanıcı adlarına mention bildirimi yollar.

    sql/migration_mentions.sql henüz uygulanmamışsa notifications.type CHECK kısıtı
    'mention' değerini reddeder — post/yorum paylaşımı bundan etkilenmesin diye
    try/except ile sessizce atlanır (like/comment/follow gibi çekirdek bildirimler
    zaten ayrı notify() çağrılarıyla gönderilir, bu satır sadece ek bir bildirim).

    `allowed_ids` verilmişse (mesaj etiketlemesi — bkz. messaging/sending.py)
    SADECE bu kümedeki kullanıcı adları bildirim alır: bir sohbette geçmeyen
    birini @etiketlemek onu mesajı görmediği halde bildirim göndermemeli.
    """
    usernames = extract_mentions(content)
    if not usernames:
        return
    try:
        for uname in usernames:
            prof = sb.table("profiles").select("id").ilike("username", uname).execute().data
            if not prof or prof[0]["id"] == actor_id:
                continue
            if allowed_ids is not None and prof[0]["id"] not in allowed_ids:
                continue
            notify(sb, recipient_id=prof[0]["id"], actor_id=actor_id,
                   type_="mention", post_id=post_id, comment_id=comment_id,
                   conversation_id=conversation_id)
    except Exception:
        pass


def linkify_mentions(content, valid_usernames: dict | None = None):
    """@kullanıcı adlarını profiline link yapar; SADECE valid_usernames'te
    (küçük harfli anahtar -> gerçek kullanıcı adı) bulunan, yani gerçekten var
    olan kullanıcı adları linklenir — aksi halde rastgele "@" içeren metinler
    (ör. bir e-posta parçası) yanlışlıkla linklenmiş olurdu.

    Büyük/küçük harf FARKI GÖZETİLMEZ: yazan "@Art" yazsa da gerçek kullanıcı
    "art" olsa da eşleşir, VE görüntülenen/linklenen metin HER ZAMAN gerçek
    kullanıcı adının kendi harflerine (valid_usernames'teki değere) otomatik
    düzeltilir — kullanıcı raporu: "kullanıcı adını yazarken büyük küçük harf
    farkı olmasın, gönderirken otomatik düzeltsin".

    `content` ham metin de olabilir, `linkify_hashtags` çıktısı (zaten Markup)
    da olabilir — her iki durumda da güvenli: Markup dilimleri zaten güvenli
    kabul edilir (escape() bunlarda no-op'tur), ham metin dilimleri normal
    şekilde escape edilir. Sıralama mantığı linkify_hashtags ile aynıdır.
    """
    if not content:
        return ""
    valid = valid_usernames or {}

    parts = []
    last_end = 0
    for m in MENTION_RE.finditer(content):
        uname = m.group(1)
        real_uname = valid.get(uname.lower())
        if real_uname is None:
            continue
        parts.append(escape(content[last_end:m.start()]))
        url = url_for("routes.profile", username=real_uname)
        parts.append(Markup('<a href="{}" class="mention-link">@{}</a>').format(url, real_uname))
        last_end = m.end()
    parts.append(escape(content[last_end:]))

    return Markup("").join(parts)


def get_valid_usernames(sb) -> dict:
    """Tüm kullanıcı adlarını {küçük harfli: gerçek_kullanıcı_adı} olarak döner
    — sayfa başına TEK sorgu, mention linkify'ın her postta ayrı bir DB
    sorgusu yapmasını önler (N+1). Gerçek casing'i sakladığı için
    linkify_mentions bunu hem eşleştirme (case-insensitive) HEM görüntülenen
    metni doğru harflere otomatik düzeltmek için kullanır. 60 saniye TTL ile
    cache'lenir."""
    from .cache import get_cached
    def _fetch():
        rows = sb.table("profiles").select("username").execute().data
        return {r["username"].lower(): r["username"] for r in rows if r.get("username")}
    return get_cached("valid_usernames", 60, _fetch)
