"""linkify_urls / linkify_hashtags / linkify_mentions filtrelerinin ORTAK
zincirleme yardımcısı.

Bu üç filtre template'lerde zincirlenir (ör.
`content|linkify_urls|linkify_hashtags|linkify_mentions(valid_usernames)`).
Her filtre kendi regex'ini önceki filtrenin ÇIKTISI üzerinde çalıştırır —
ama önceki filtrenin ürettiği `<a ...>...</a>` bloğunun İÇİNİ bir daha
TARAMAMALI: aksi halde ör. bir URL'nin href'inde veya görünen metninde geçen
`#fragment`/`@isim` benzeri bir alt dizi, sonraki filtre tarafından yanlışlıkla
ikinci bir `<a>` ile sarılır, HTML/href bozulur (iç içe `<a>` üretilir).

`apply_outside_anchors` bunu tek bir yerde çözer: content'i mevcut
`<a>...</a>` bloklarına göre dilimlere ayırır, pattern'i SADECE bu bloklar
DIŞINDAKİ dilimlerde çalıştırır, blokların kendisini olduğu gibi (tekrar
taramadan) geçirir.
"""
import re
from markupsafe import Markup, escape

_ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.IGNORECASE | re.DOTALL)


def apply_outside_anchors(content, pattern: re.Pattern, replace_fn) -> Markup:
    """content ham metin (DB'den gelen escape edilmemiş kullanıcı girdisi)
    VEYA zincirdeki önceki bir linkify filtresinden gelen GÜVENİLİR Markup
    olabilir.

    GÜVENLİK-KRİTİK ayrım: content SADECE zaten `markupsafe.Markup` İSE
    (yani zincirde önceki bir linkify filtresinden geçtiyse) "mevcut <a>
    bloklarını atla" mantığı çalışır. Ham `str` girdide "önceki filtreden
    gelen güvenli bir anchor" hiç var olamaz — bu durumda kullanıcı içeriğinde
    yazılı DÜZ METİN `<a href="x" onmouseover="...">` gibi bir dizi, gerçek
    bir anchor sanılıp escape'ten muaf tutulursa stored XSS oluşur. Bu yüzden
    ham str girdide anchor ayrımı YAPILMAZ, TÜMÜ normal şekilde escape edilir
    (isinstance kontrolü `Markup` bir `str` alt sınıfı olduğu için mümkün).

    pattern'in content içinde eşleştiği her yer için replace_fn(match) çağrılır:
    - Markup dönerse o eşleşme bununla DEĞİŞTİRİLİR (link üretildi).
    - None dönerse eşleşme DEĞİŞTİRİLMEZ, düz (escape edilmiş) metin olarak
      kalır (ör. linkify_mentions'ta geçersiz/var olmayan bir kullanıcı adı).
    """
    if not content:
        return Markup("")
    if not isinstance(content, Markup):
        # Ham girdi: zincirin İLK halkası (ör. p.content|linkify_urls) —
        # önceki bir filtreden gelen gerçek <a> bloğu YOK, anchor arama
        # sahte pozitif üretir (kullanıcı metninde literal "<a ...>" yazması).
        return _linkify_segment(content, pattern, replace_fn)

    out = []
    pos = 0
    for anchor_m in _ANCHOR_RE.finditer(content):
        out.append(_linkify_segment(content[pos:anchor_m.start()], pattern, replace_fn))
        out.append(Markup(anchor_m.group(0)))
        pos = anchor_m.end()
    out.append(_linkify_segment(content[pos:], pattern, replace_fn))
    return Markup("").join(out)


def _linkify_segment(segment, pattern: re.Pattern, replace_fn) -> Markup:
    """Tek bir dilim (mevcut anchor'ların DIŞINDaki metin) üzerinde çalışır —
    eşleşmeyen aralar escape edilir, eşleşenler replace_fn ile değiştirilir."""
    if not segment:
        return Markup("")
    parts = []
    last_end = 0
    for m in pattern.finditer(segment):
        replacement = replace_fn(m)
        if replacement is None:
            continue  # bu eşleşme geçersiz sayıldı — last_end İLERLEMEZ, düz metin olarak kalır
        parts.append(escape(segment[last_end:m.start()]))
        parts.append(replacement)
        last_end = m.end()
    parts.append(escape(segment[last_end:]))
    return Markup("").join(parts)
