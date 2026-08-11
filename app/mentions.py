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
        # 1) Önce bildirim ALACAK adayları topla (profil çözümleme hâlâ
        # kullanıcı-başına ayrı sorgu — pre-existing, bu fonksiyonun kapsamı
        # farklı bir N+1 olduğu için burada dokunulmuyor).
        recipient_ids: list[str] = []
        for uname in usernames:
            prof = sb.table("profiles").select("id").ilike("username", uname).execute().data
            if not prof or prof[0]["id"] == actor_id:
                continue
            if allowed_ids is not None and prof[0]["id"] not in allowed_ids:
                continue
            recipient_ids.append(prof[0]["id"])
        if not recipient_ids:
            return

        # 2) post_id verilmişse, notify() zaten HER çağrıda tek tek attığı
        # muted_posts sorgusunu burada TOPLU (tek IN sorgusu) çözüp mute
        # etmiş olanları eleyelim — 5 mention'lı bir postta notify()'ın
        # kendi içinde 5 ayrı muted_post_ids() sorgusu atmasını önler.
        muted_ids: set = set()
        if post_id:
            try:
                muted_ids = {
                    r["user_id"] for r in sb.table("muted_posts").select("user_id")
                    .eq("post_id", post_id).in_("user_id", recipient_ids).execute().data
                }
            except Exception:
                pass  # migration henüz uygulanmamışsa boş küme (fail-open)

        for recipient_id in recipient_ids:
            if recipient_id in muted_ids:
                continue  # mute etmiş — notify() zaten bildirim üretmeyecekti, sorguyu hiç yapma
            notify(sb, recipient_id=recipient_id, actor_id=actor_id,
                   type_="mention", post_id=post_id, comment_id=comment_id,
                   conversation_id=conversation_id)
    except Exception:
        pass


def notify_story_mention(sb, *, actor_id: str, recipient_id: str) -> None:
    """Hikaye sticker'ında (@mention) etiketlenen ZATEN ÇÖZÜLMÜŞ tek bir
    kullanıcıya bildirim yollar — notify_mentions()'ın regex tabanlı
    content-parsing yolunu (extract_mentions) KULLANMAZ, çünkü çağıran taraf
    (api_v1/stories.py) kullanıcı adını zaten `profiles`'a karşı doğrulamış
    olarak gelir; tekrar regex/DB araması gereksiz.

    Ayrı bir "story_mention" tipi kullanılır ('mention' DEĞİL): notifications
    tablosundaki mevcut 'mention' tipinin hedef URL'i (bkz.
    app/notifications.py _TARGET_BUILDERS) post_id/conversation_id'ye bağlı,
    hikaye sticker'ında ikisi de yok — story_reaction ile AYNI desen
    benimsendi (hedef her zaman etiketleyenin profili).

    sql/migration_story_caption_style_and_stickers.sql notifications.type
    CHECK kısıtına 'story_mention' ekler; migration henüz uygulanmamışsa
    insert hata fırlatır — ÇAĞIRAN TARAF bunu try/except ile sarmalı, hikaye
    oluşturmayı ASLA bloklamamalı (poll_error'un aksine burada dönecek bir
    hata alanı da yok, tamamen sessiz best-effort)."""
    notify(sb, recipient_id=recipient_id, actor_id=actor_id, type_="story_mention")


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
