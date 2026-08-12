"""URL linkify filtresi + Open Graph link önizleme (SSRF-korumalı proxy/fetch).

`app/gifs.py`'deki "proxy" deseniyle tutarlı: web tarafı session-cookie auth
(`@login_required`), native tarafı (`app/api_v1/link_preview.py`) Bearer token
auth (`@api_login_required`) kullanır, ikisi de burada tanımlı TEK çekirdek
fonksiyonu (`get_or_fetch_preview`) çağırır — SSRF/parse mantığı tek yerde.

Cache: sql/migration_link_previews.sql ile eklenen public.link_previews
tablosu (normalized_url unique). 7 gün başarılı sonuç, 1 saat başarısız
sonuç TTL'i ile — sürekli aynı kırık URL'yi denemesin ama kalıcı olarak da
kilitlenmesin.
"""
import ipaddress
import re
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse, urljoin

import requests
from flask import Blueprint, request, jsonify, session
from markupsafe import Markup, escape

from .decorators import login_required
from .supabase_client import get_sb
from .rate_limit import is_rate_limited
from .linkify_utils import apply_outside_anchors

bp = Blueprint("link_preview", __name__)

# Sadece http(s) — başka şema (ör. javascript:) ASLA eşleşmez, regex'in
# https?:// ile SIKI başlaması bunu zaten garanti eder (XSS önlemi).
URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_CHARS = ".,;:!?)]}'\""

_ALLOWED_PORTS = {80, 443, 8080, 8443}
_USER_AGENT = "Mozilla/5.0 (compatible; SosyalMedyaBot/1.0; +link-preview)"
_TIMEOUT = (3, 5)  # (connect, read) saniye
_MAX_BYTES = 1_500_000  # ~1.5MB — bellek tüketimi/DoS önlemi
_MAX_TOTAL_READ_SECONDS = 10  # tek bir yanıtın gövdesini okumak için üst sınır (yavaş-sızdırma DoS önlemi)
_MAX_REDIRECTS = 3
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # başarılı sonuç 7 gün taze sayılır
_FAILED_RETRY_SECONDS = 3600  # başarısız sonuç 1 saat sonra tekrar denenir


# ------------------------------------------------------------------
# 1) linkify_urls — Jinja filtresi
# ------------------------------------------------------------------

def _strip_trailing_punct(url: str) -> tuple[str, str]:
    """URL sonundaki yaygın cümle noktalama işaretlerini ayırır (klasik
    linkifier tuzağı: cümle sonu noktası veya kapanan parantez URL'nin
    parçası sayılmamalı). Basit trailing-strip yeterli — dengeli parantez
    kontrolü BİLEREK yok (aşırı mühendislik)."""
    end = len(url)
    while end > 0 and url[end - 1] in _TRAILING_CHARS:
        end -= 1
    return url[:end], url[end:]


def linkify_urls(content):
    """http(s) URL'leri tıklanabilir linke çevirir.

    Zincirde EN BAŞTA çalışması GEREKİR (`content|linkify_urls|linkify_hashtags
    |linkify_mentions(...)`) — URL'ler `#fragment` veya nadiren `@` içerebilir
    (ör. query string'de e-posta); linkify_urls sondan çalışırsa hashtag/mention
    filtreleri URL'nin ortasından bir hashtag/mention linki koparır, URL
    parçalanır. apply_outside_anchors zaten bu filtreden SONRA çalışacak
    filtrelerin mevcut `<a>` bloğunun içini bir daha taramamasını sağlıyor.
    """
    if not content:
        return ""

    def _replace(m):
        raw = m.group(0)
        url, trailing = _strip_trailing_punct(raw)
        if not url:
            return None
        safe_url = escape(url)
        link = Markup(
            '<a href="{}" target="_blank" rel="noopener noreferrer nofollow" '
            'class="content-link">{}</a>'
        ).format(safe_url, safe_url)
        return link + escape(trailing)

    return apply_outside_anchors(content, URL_RE, _replace)


# ------------------------------------------------------------------
# 2) SSRF-korumalı fetch + Open Graph parse
# ------------------------------------------------------------------

def is_valid_url_format(url: str) -> bool:
    """Route seviyesinde 400 kararı için hafif format kontrolü (SSRF/DNS
    çözümlemesi İÇERMEZ — o kontrol _resolve_pinned_target'ta, fetch anında
    yapılır)."""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_unsafe_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # parse edilemeyen bir adres güvenli SAYILMAZ
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_pinned_target(url: str):
    """Hostname'i çözer, dönen HER IP'yi kontrol eder — biri bile iç
    ağ/localhost/link-local/metadata-endpoint (169.254.169.254 dahil)
    aralığındaysa None döner (istek reddedilir). Sadece standart web
    portlarına izin verilir (iç servis portlarına — 22/3306/6379/9200 vb. —
    SSRF'i engeller).

    Başarılıysa (hostname, port, infos) döner — infos, `getaddrinfo`'nun HAM
    dönüş listesi (birden fazla aile/IP içerebilir, ör. hem IPv4 hem IPv6).
    `_pin_dns` bu TAM listeyi olduğu gibi sabitler (tek bir IP'ye
    indirgemez): aksi halde ör. sadece IPv6 kaydı seçilip ortamda IPv6 rotası
    yoksa bağlantı gereksiz yere başarısız olurdu — `socket.create_connection`
    normalde birden çok adresi sırayla dener (happy-eyeballs benzeri
    fallback), pinleme bu davranışı KORUMALI, sadece "hangi adaylar
    denenecek" listesini validate anında donduruyor (bkz. `_fetch_html`
    docstring'i: DNS-rebinding TOCTOU'yu kapatmak için validate ile connect
    ARASINDA hostname bir daha çözülmemeli)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        return None
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None
    if not infos:
        return None
    if any(_is_unsafe_ip(info[4][0]) for info in infos):
        return None
    return hostname, port, infos


# DNS-pinning: socket.getaddrinfo modül-seviyesi bir GLOBAL — thread-local
# DEĞİL. waitress prod'da birden çok thread ile çalışıyor (bkz. serve.py);
# önceki tasarım (`_pin_dns` her istekte `socket.getaddrinfo`'yu geçici
# olarak DEĞİŞTİRİP finally'de GERİ ALIYORDU) thread-safe DEĞİLDİ: Thread
# A context İÇİNDEYKEN Thread B kendi context'ine girip "orijinal" diye
# Thread A'nın wrapper'ını yakalayabilir, çıkışta biri diğerinin patch'ini
# geri yükleyebilir/kaybedebilir — sonuçta `socket.getaddrinfo` KALICI
# olarak yanlış bir wrapper'da SIKIŞABİLİR veya stale bir pin başka bir
# isteğin (Supabase client, gifs.py, messaging.py — AYNI process'teki TÜM
# `socket` kullanan kod) DNS çözümlemesini sessizce etkileyebilir.
#
# Çözüm: patch SADECE MODÜL YÜKLENİRKEN BİR KEZ kurulur, asla geri alınmaz;
# hangi thread'in hangi (host, port) için pin'i olduğu `threading.local()`
# ile İZOLE tutulur — her thread kendi pin'ini görür, başka thread'i ASLA
# etkilemez. Pin yoksa (varsayılan durum, `_dns_pin_local.pin is None`)
# davranış gerçek `socket.getaddrinfo` ile birebir aynıdır.
_dns_pin_local = threading.local()
_real_getaddrinfo = socket.getaddrinfo  # gerçek fonksiyon BİR KEZ yakalanır, bir daha değişmez


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    pin = getattr(_dns_pin_local, "pin", None)
    if pin is not None and pin[0] == host and pin[1] == port:
        return pin[2]
    return _real_getaddrinfo(host, port, *args, **kwargs)


socket.getaddrinfo = _pinned_getaddrinfo  # import anında, BİR KEZ — per-request restore YOK


@contextmanager
def _pin_dns(hostname: str, port: int, infos: list):
    """Bu thread'in TEK bir requests.get() çağrısı süresince `socket.
    getaddrinfo`'yu SADECE (hostname, port) çifti için önceden doğrulanmış
    `infos` listesine sabitler — diğer thread'leri VE diğer hostname'leri
    hiç etkilemez (bkz. modül seviyesindeki `_pinned_getaddrinfo`/
    `threading.local()` açıklaması).

    DNS-rebinding TOCTOU'yu kapatır: `_resolve_pinned_target` ile
    `requests.get()` arasında hostname TEKRAR çözülürse (urllib3 kendi
    getaddrinfo'sunu ayrıca çalıştırır), kötü niyetli/TTL=0 bir DNS sunucusu
    2. sorguya farklı (iç ağ) bir IP döndürüp doğrulamayı bypass edebilirdi —
    bu pencereyi kapatmak için gerçek bağlantı da SADECE doğrulama anında
    görülen adaylar arasından seçim yapar. Host header/TLS SNI hostname
    string'i olarak KALIR (sadece adres çözümlemesi sabitlenir), vhost/TLS
    bozulmaz."""
    _dns_pin_local.pin = (hostname, port, infos)
    try:
        yield
    finally:
        _dns_pin_local.pin = None


def _fetch_html(url: str):
    """Yönlendirmeleri OTOMATİK takip ETMEZ — her hop AYRI AYRI SSRF
    doğrulamasından geçer VE doğrulanan IP'ye pinlenir (en fazla
    _MAX_REDIRECTS hop). Otomatik `allow_redirects=True` kullanmak SSRF
    korumasını bypass edilebilir kılardı: dışarıdan güvenli görünen bir URL,
    sunucu tarafında iç ağa yönlendirebilir (klasik SSRF-via-redirect
    tekniği).

    Başarılıysa (final_url, html_text) döner, aksi halde None."""
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        target = _resolve_pinned_target(current_url)
        if target is None:
            return None
        hostname, port, pinned_infos = target

        try:
            # requests.get() bu blok içinde TCP bağlantısını kurup
            # status+header'ları senkron okur (stream=True olsa da gövde
            # okuma iter_content ile SONRA yapılır) — DNS pinleme SADECE bu
            # ilk bağlantı kurulumu için gerekli, bloktan çıkınca kaldırılır.
            with _pin_dns(hostname, port, pinned_infos):
                resp = requests.get(
                    current_url,
                    headers={"User-Agent": _USER_AGENT},
                    timeout=_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
        except requests.RequestException:
            return None

        try:
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue

            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None

            # Content-Length'e GÜVENME (yalan söyleyebilir) — gerçek okunan
            # byte sayısını say, limiti aşınca oku'maya devam etme. Ayrıca
            # TOPLAM okuma süresi de sınırlanır: _TIMEOUT her tek soket
            # okumasına AYRI uygulanıyor, yavaş/kötü niyetli bir sunucu
            # veriyi _MAX_BYTES'a kadar küçük parçalar halinde (her parça
            # kendi read-timeout'unun altında) göndererek bağlantıyı çok
            # uzun tutabilirdi (yavaş-sızdırma DoS) — monotonic saatle
            # toplam süreye de üst sınır konur.
            chunks = []
            total = 0
            start = time.monotonic()
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > _MAX_BYTES:
                    break
                if time.monotonic() - start > _MAX_TOTAL_READ_SECONDS:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            try:
                html_text = raw.decode(resp.encoding or "utf-8", errors="replace")
            except (LookupError, TypeError):
                html_text = raw.decode("utf-8", errors="replace")
            return current_url, html_text
        finally:
            resp.close()

    return None  # hop limiti aşıldı (olası yönlendirme döngüsü)


class _OGParser(HTMLParser):
    """<meta property="og:*"> + fallback <title>/<meta name="description">
    yakalar. Regex tabanlı parsing YERİNE stdlib HTMLParser kullanılır —
    gerçek dünya HTML'i genelde kapanmamış/bozuk etiketler içerir, regex
    bunlarda kolayca yanılır. Yeni bağımlılık (bs4/lxml) requirements.txt'te
    YOK, bilinçli olarak stdlib ile yetinildi."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og: dict[str, str] = {}
        self.title = None
        self.description = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "meta":
            prop = (attrs_d.get("property") or attrs_d.get("name") or "").lower()
            content = attrs_d.get("content")
            if not prop or content is None:
                return
            if prop.startswith("og:"):
                self.og.setdefault(prop, content)
            elif prop == "description" and self.description is None:
                self.description = content
        elif tag == "title" and self.title is None:
            self._in_title = True

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


def _extract_preview(final_url: str, html_text: str) -> dict:
    parser = _OGParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass  # bozuk HTML — elde ne varsa onunla devam

    title = (parser.og.get("og:title") or parser.title or "").strip()
    description = (parser.og.get("og:description") or parser.description or "").strip()
    image = (parser.og.get("og:image") or "").strip()
    site_name = (parser.og.get("og:site_name") or "").strip()

    if image:
        image = urljoin(final_url, image)  # göreli og:image URL'lerini mutlak hale getir

    hostname = urlparse(final_url).hostname or ""
    domain = hostname[4:] if hostname.startswith("www.") else hostname

    return {
        "url": final_url,
        "domain": domain,
        # Aşırı uzun değerler DB/cache'e taşınmasın diye kırpılır (savunma amaçlı)
        "title": title[:300] or None,
        "description": description[:500] or None,
        "image": image or None,
        "site_name": site_name or None,
    }


# X'in giriş/JS gerektiren tweetler için (korumalı hesap, hassas içerik
# uyarısı, bazı yeni/az bilinen hesaplar) döndürdüğü JS-gerektiren SPA
# kabuğunun varsayılan <title>/og:title değerleri — GERÇEK bir tweet'in
# title'ı HER ZAMAN "Ad Soyad (@kullanici) on X" formatındadır, asla bare
# "X"/"Twitter"/"Post" değildir. Kullanıcı raporu: bu durumda description
# hep boş dönüyor ama title+image (generic X logosu) dolu olduğu için eski
# kod bunu "ok:true" sayıp neredeyse boş bir kart gösteriyordu — Twitterbot
# UA'sıyla doğrulandı: X bu URL'ler için kendi resmi crawler'ına bile 404
# veriyor, yani bu bizim tarafımızdan düzeltilebilecek bir şey DEĞİL, sadece
# "önizleme yok" olarak ele alınmalı (Discord/Slack gibi diğer unfurler'lar
# da aynı URL için zengin kart gösteremez).
_TWITTER_PLACEHOLDER_TITLES = {"x", "twitter", "post"}


def _is_unusable_preview(preview: dict) -> bool:
    if not (preview["title"] or preview["description"] or preview["image"]):
        return True
    domain = (preview.get("domain") or "").lower()
    if domain in ("twitter.com", "x.com", "mobile.twitter.com"):
        title = (preview.get("title") or "").strip().lower()
        if not preview.get("description") and title in _TWITTER_PLACEHOLDER_TITLES:
            return True
    return False


def _normalize_url(url: str) -> str:
    """Cache anahtarı: scheme+host lowercase, sondaki `/` kırpılır — query
    string/fragment olduğu gibi bırakılır (basit tutmak tercih edildi, aşırı
    normalizasyon farklı URL'leri yanlışlıkla aynı cache satırına düşürebilirdi)."""
    parsed = urlparse(url)
    normalized = urlunparse((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
        parsed.params, parsed.query, parsed.fragment,
    ))
    stripped = normalized.rstrip("/")
    return stripped or normalized


def _parse_iso(value):
    if not value:
        return None
    try:
        v = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _row_to_response(row: dict) -> dict:
    url = row.get("url") or ""
    hostname = urlparse(url).hostname or ""
    domain = hostname[4:] if hostname.startswith("www.") else hostname
    return {
        "ok": True,
        "url": url,
        "domain": domain,
        "title": row.get("title"),
        "description": row.get("description"),
        "image": row.get("image_url"),
        "site_name": row.get("site_name"),
    }


def _save_result(sb, normalized_url: str, original_url: str, preview) -> None:
    payload = {
        "normalized_url": normalized_url,
        "url": (preview or {}).get("url") or original_url,
        "title": (preview or {}).get("title"),
        "description": (preview or {}).get("description"),
        "image_url": (preview or {}).get("image"),
        "site_name": (preview or {}).get("site_name"),
        "fetch_failed": preview is None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("link_previews").upsert(payload, on_conflict="normalized_url").execute()
    except Exception:
        pass  # cache yazımı başarısız olsa da fetch sonucu zaten döndürülüyor


def get_or_fetch_preview(url: str) -> dict:
    """Bir URL için Open Graph önizlemesini döner (varsa cache'ten, yoksa
    SSRF-korumalı fetch ile). Fetch/parse'ın HERHANGİ bir noktada patlaması
    (timeout, SSRF reddi, HTML parse hatası, DB hatası, migration henüz
    uygulanmamış olması) bu fonksiyonun dışına İSTİSNA olarak SIZMAZ — her
    zaman `{"ok": bool, ...}` döner. Post/mesaj gösterimi önizleme olmadan da
    ÇALIŞMAYA DEVAM ETMELİ (kritik olmayan bir özellik, app/gifs.py'deki
    graceful-degradation felsefesiyle aynı)."""
    try:
        return _get_or_fetch_preview_inner(url)
    except Exception:
        return {"ok": False}


def _get_or_fetch_preview_inner(url: str) -> dict:
    sb = get_sb()
    normalized = _normalize_url(url)
    now = datetime.now(timezone.utc)

    cached = None
    try:
        rows = sb.table("link_previews").select("*").eq(
            "normalized_url", normalized
        ).execute().data
        cached = rows[0] if rows else None
    except Exception:
        cached = None  # migration henüz uygulanmamış olabilir — cache'siz devam

    if cached:
        fetched_at = _parse_iso(cached.get("fetched_at"))
        if fetched_at:
            age = (now - fetched_at).total_seconds()
            if not cached.get("fetch_failed") and age < _CACHE_TTL_SECONDS:
                return _row_to_response(cached)
            if cached.get("fetch_failed") and age < _FAILED_RETRY_SECONDS:
                return {"ok": False}

    fetched = _fetch_html(url)
    if fetched is None:
        _save_result(sb, normalized, url, None)
        return {"ok": False}

    final_url, html_text = fetched
    preview = _extract_preview(final_url, html_text)
    if _is_unusable_preview(preview):
        _save_result(sb, normalized, url, None)  # kullanılabilir hiçbir alan yok — önizlemesiz say
        return {"ok": False}

    _save_result(sb, normalized, url, preview)
    return {
        "ok": True,
        "url": preview["url"],
        "domain": preview["domain"],
        "title": preview["title"],
        "description": preview["description"],
        "image": preview["image"],
        "site_name": preview["site_name"],
    }


# ------------------------------------------------------------------
# 3) Web route (session-cookie auth) — native karşılığı app/api_v1/link_preview.py
# ------------------------------------------------------------------

@bp.route("/link-preview")
@login_required
def link_preview_route():
    url = request.args.get("url", "").strip()
    if not is_valid_url_format(url):
        return jsonify(ok=False, error="invalid_url"), 400

    me = session["user"]["id"]
    if is_rate_limited(f"link_preview:{me}", 30, 60):
        return jsonify(ok=False, error="rate_limited"), 429

    return jsonify(get_or_fetch_preview(url))
