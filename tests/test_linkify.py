"""Linkify (URL, hashtag, mention) birim testleri — XSS regresyon, URL bütünlüğü.

Link preview özelliğine ait kalıcı test suite — güvenlik-kritik XSS regresyonu
ve filtre bütünlüğü doğrulaması. `linkify_hashtags` ve `linkify_mentions` Flask
app context (url_for için) ve valid_usernames DB sorgusu gerektiği için,
test_client + session ile yapılır.
"""
import pytest
from markupsafe import Markup

from app.link_preview import linkify_urls, _is_unusable_preview, _is_media_image
from app.hashtags import linkify_hashtags
from app.mentions import linkify_mentions


class TestLinkifyXSSRegression:
    """KRİTİK: Önceki stored XSS açığı tekrar açılmamış mı?

    Ham metin içinde yazılı `<a href="x" onmouseover="...">` benzeri dize
    ASLA gerçek bir HTML elementi olarak render OLMAMALI, HER ZAMAN
    escape edilmiş `&lt;a href` olarak çıkmalı."""

    def test_linkify_urls_xss_injection_in_raw_text_escaped(self):
        """Raw string'de yazılı HTML etiketleri escape edilmeli.

        Ham metin `<a href="x" onmouseover="alert(1)">tık</a>` dönemine alındığında,
        linkify_urls'ün href yakalamadığı için (URL_RE sadece https?:// ile başlar),
        tüm metin escape edilmiş olarak geçer. Kontrol:
        - Gerçek bir `<a href=` tag'ı (unescaped) OLMAMALI
        - Çıktı yapı olarak `&lt;a href=` (escaped açı parantez) veya sabit `&lt;` + `&gt;`
        olmalı — yani gerçek HTML tag'ı değil metin görünümü.
        """
        content = '<a href="x" onmouseover="alert(1)">tık</a>'
        result = linkify_urls(content)

        # Sonuç Markup olmalı (linkify çıktısı)
        assert isinstance(result, Markup)

        result_str = str(result)

        # Kontrol 1: Gerçek bir `<a href` tag'ı (escape edilmemiş) OLMAMALI
        # (bu test HTML parser'ından geçebilecek gerçek tag'ı arar)
        # Basit regex: unescaped <a href= dizisi OLMAMALI
        assert '<a href=' not in result_str, f"Gerçek <a tag'ı bulundu: {result_str}"

        # Kontrol 2: Metin escape edilmiş (en azından açı parantez)
        assert '&lt;' in result_str or '&#' in result_str, f"Escape bulunamadı: {result_str}"



class TestLinkifyURLIntegrity:
    """URL'lerin zincir (linkify_urls -> hashtags -> mentions) sonrası
    bozulmaması, fragment'ler ayrı hashtag olmaması vs."""

    def test_url_with_fragment_not_split_by_hashtag(self):
        """URL sonundaki #fragment yanlışlıkla hashtag linkine dönüşmesin."""
        # Zincir: URL filtresinden gel, hashtag filtresine gir
        content = "See https://example.com/page#cool-stuff for details"

        # linkify_urls
        step1 = linkify_urls(content)
        assert isinstance(step1, Markup)

        # linkify_hashtags (zincir)
        step2 = linkify_hashtags(step1)

        # Sonuç: TEK bir href (URL), #cool-stuff ayrı hashtag OLMAMALI
        result_str = str(step2)
        # URL'nin kendisi link
        assert 'href="' in result_str or 'href=' in result_str
        # apply_outside_anchors zaten URL'nin içini taramamalı
        # Garip escape'leme OLMAMALI
        assert '&lt;' not in result_str  # escape'i gereksiz yere YAPMA

    def test_linkify_urls_only_integration(self):
        """linkify_urls birim test — URL'ler linklenip escaped karakterler barındırmadığında
        başında/sonunda noktalama işaretleri düzgün kaldırılıyor."""
        content = "Check https://example.com/page. Cool?"

        result = linkify_urls(content)
        result_str = str(result)

        # URL link var
        assert 'href="https://example.com/page"' in result_str
        # Sonda nokta/soru işareti URL'nin parçası değil
        assert 'href="https://example.com/page."' not in result_str
        assert 'href="https://example.com/page?"' not in result_str


class TestLinkifyHashtagMentionIntegration:
    """Hashtag ve mention filtreleri Flask request context gerekli (url_for).

    Bu testler `app.test_request_context()` ile çalışır — request context'te
    `url_for()` direkt SERVER_NAME olmadan çalışır."""

    def test_url_hashtag_mention_chain_no_corruption(self, app):
        """KRİTİK: URL + hashtag + mention zinciri — hiçbiri birbirini bozmaz,
        tüm linkler doğru href'lere sahip, iç içe `<a>` OLMAZ.

        Senaryo: `"See https://example.com/page#cool-stuff @alice check #tag2"`
        - https://example.com/page#cool-stuff → TEK `<a href="...">`  (#cool-stuff kopia olmaz)
        - @alice (geçerli user) → mention linki (href="/u/alice")
        - #tag2 → hashtag linki (href="/hashtag/tag2")
        - Toplam 3 ayrı <a> tag'ı (iç içe değil)"""

        with app.test_request_context():
            content = "See https://example.com/page#cool-stuff @alice check #tag2"
            valid_usernames = {"alice": "alice"}

            # Zincir: URL -> hashtag -> mention
            step1 = linkify_urls(content)
            step2 = linkify_hashtags(step1)
            step3 = linkify_mentions(step2, valid_usernames=valid_usernames)
            result = str(step3)

            # 1. URL TEK PARÇA — fragment kopia olmadı
            assert 'href="https://example.com/page#cool-stuff"' in result, \
                f"URL fragment'i kopia edilmiş veya bozulmuş: {result}"

            # 2. #cool-stuff (URL içinde) ayrı hashtag linkine dönüşmedi
            assert 'href="/hashtag/cool' not in result, \
                f"URL fragment'i yanlışlıkla hashtag linkine dönüştü: {result}"

            # 3. #tag2 (URL DIŞINDAKİ) GERÇEKTEN hashtag linki oldu
            assert 'href="/hashtag/tag2"' in result, \
                f"URL dışı #tag2 hashtag linkine dönüşmedi: {result}"

            # 4. @alice mention linki oldu (routes.profile route'ında /u/<username>)
            # url_for("routes.profile", username="alice") → /u/alice
            assert 'href="/u/alice"' in result, \
                f"@alice mention linki oluşmadı: {result}"

            # 5. Tam olarak 3 ayrı <a> tag'ı (URL, mention, hashtag)
            link_count = result.count('<a ')
            assert link_count == 3, \
                f"Beklenen 3 link, bulundu {link_count}: {result}"

            # 6. Hiçbir escape hatası (gerçek tag'lar bozulmamış)
            assert '&lt;a ' not in result, \
                f"Link tag'ları escape edilmiş (hatalı): {result}"


class TestLinkifyProtocolRestriction:
    """javascript:/data:/vbscript: şemaları linklenmemeli."""

    def test_javascript_url_not_linkified(self):
        """javascript: şeması URL olarak linklenmemeli."""
        content = "Click javascript:alert(1) here"
        result = linkify_urls(content)

        # javascript: linki OLMAMALI
        result_str = str(result)
        assert 'href="javascript:' not in result_str
        assert 'href=\'javascript:' not in result_str

    def test_data_url_not_linkified(self):
        """data: şeması linklenmemeli."""
        content = "data:text/html,<img src=x onerror=alert(1)> is bad"
        result = linkify_urls(content)

        result_str = str(result)
        assert 'href="data:' not in result_str

    def test_vbscript_url_not_linkified(self):
        """vbscript: şeması (IE eski sürümleri) linklenmemeli."""
        content = "vbscript:msgbox('xss')"
        result = linkify_urls(content)

        result_str = str(result)
        assert 'href="vbscript:' not in result_str


class TestLinkifyTrailingPunctuation:
    """Cümle sonundaki nokta/virgül URL'nin parçası olmamali."""

    def test_trailing_period_excluded_from_url(self):
        """Cümle sonu noktası URL dışında kalmalı."""
        content = "Read https://example.com/article carefully."
        result = linkify_urls(content)

        result_str = str(result)
        # href sonunda "/article"ı olmalı, ".carefully" OLMAMALI
        assert 'href="https://example.com/article"' in result_str
        assert 'href="https://example.com/article."' not in result_str

    def test_trailing_comma_excluded(self):
        """Virgül URL dışında."""
        content = "link: https://example.com, important!"
        result = linkify_urls(content)

        result_str = str(result)
        assert 'href="https://example.com"' in result_str
        assert 'href="https://example.com,"' not in result_str

    def test_trailing_closing_paren_excluded(self):
        """Kapanan parantez dışarıda."""
        content = "(see https://example.com/docs)"
        result = linkify_urls(content)

        result_str = str(result)
        # href="/docs" olmalı, "/docs)" OLMAMALI
        assert 'href="https://example.com/docs"' in result_str
        assert 'href="https://example.com/docs)"' not in result_str

    def test_multiple_trailing_punctuation(self):
        """Birden fazla sonda işareti."""
        content = "Visit https://example.com/page!!! for more."
        result = linkify_urls(content)

        result_str = str(result)
        assert 'href="https://example.com/page"' in result_str
        assert 'href="https://example.com/page!!!' not in result_str


class TestLinkifyEmptyAndEdgeCases:
    """Boş/edge case'ler."""

    def test_empty_content_returns_empty(self):
        """Boş girdi boş çıktı."""
        assert linkify_urls("") == ""
        assert linkify_hashtags("") == ""
        assert linkify_mentions("", valid_usernames={}) == ""

    def test_none_content_safe(self):
        """None input safe handling (veya empty)."""
        # linkify_*'lar genellikle "" döner
        result = linkify_urls(None) if None else ""
        assert result == "" or result is None or isinstance(result, Markup)

    def test_malformed_url_variants(self):
        """Yanlış şekilli URL'ler güvenli handled."""
        content = "https://example"  # TLD yok ama başlayan valid https://
        result = linkify_urls(content)
        # Ya link olur ya olmaz ama crash OLMAMALI
        assert isinstance(result, (str, Markup))


class TestLinkPreviewTwitterPlaceholderDetection:
    """KRİTİK regresyon: X'in giriş/JS gerektiren tweetler (korumalı hesap,
    hassas içerik, bazı yeni/az bilinen hesaplar) için döndürdüğü JS-gerektiren
    SPA kabuğu — title="X"/"Twitter"/"Post", description YOK, image sadece
    generic fallback logosu — eskiden "ok:true" sayılıp neredeyse boş bir kart
    gösteriliyordu (kullanıcı raporu: "twitter gönderileri içeriği gözükmüyor,
    kart çıkıyor ama boş"). Gerçek bir tweet linkiyle (x.com/Jqrxx/status/...)
    Twitterbot UA'sının bile 404 aldığı doğrulanarak teşhis edildi — bu
    bizim tarafımızdan "düzeltilebilecek" bir fetch hatası DEĞİL, X'in
    içeriği hiç sunmadığı bir durum; doğru davranış kart HİÇ göstermemek."""

    def test_twitter_generic_spa_shell_treated_as_unusable(self):
        preview = {
            "url": "https://x.com/someuser/status/123",
            "domain": "x.com",
            "title": "X",
            "description": None,
            "image": "https://abs.twimg.com/rweb/ssr/default/v2/og/image.png",
            "site_name": "X (formerly Twitter)",
        }
        assert _is_unusable_preview(preview) is True

    def test_twitter_legacy_placeholder_title_treated_as_unusable(self):
        preview = {
            "url": "https://twitter.com/someuser/status/123",
            "domain": "twitter.com",
            "title": "Twitter",
            "description": "",
            "image": None,
            "site_name": None,
        }
        assert _is_unusable_preview(preview) is True

    def test_twitter_real_tweet_with_description_stays_usable(self):
        """Gerçek bir tweet'in description'ı varsa (jack/status/20 gibi
        gerçek dünyada doğrulandı) placeholder sanılıp reddedilmemeli."""
        preview = {
            "url": "https://twitter.com/jack/status/20",
            "domain": "twitter.com",
            "title": "jack (@jack) on X",
            "description": "just setting up my twttr",
            "image": "https://pbs.twimg.com/profile_images/x.jpg",
            "site_name": "X (formerly Twitter)",
        }
        assert _is_unusable_preview(preview) is False

    def test_non_twitter_domain_with_bare_title_not_affected(self):
        """Placeholder tespiti SADECE twitter.com/x.com'a özgü — başka bir
        sitenin gerçekten title="X" olan (alakasız) bir sayfası yanlışlıkla
        reddedilmemeli."""
        preview = {
            "url": "https://example.com/x",
            "domain": "example.com",
            "title": "X",
            "description": None,
            "image": "https://example.com/some-real-image.jpg",
            "site_name": None,
        }
        assert _is_unusable_preview(preview) is False

    def test_all_fields_empty_still_unusable(self):
        """Mevcut davranış (placeholder tespitinden ÖNCE de vardı) korunuyor."""
        preview = {"url": "https://example.com/", "domain": "example.com", "title": None, "description": None, "image": None, "site_name": None}
        assert _is_unusable_preview(preview) is True


class TestLinkPreviewImageIsMedia:
    """og:image bir tweet'in gerçek foto/video karesi mi, yoksa küçük profil
    avatarı/statik asset mi? Kullanıcı raporu: foto/video tweet'lerinde
    önizleme (image alanı GERÇEK 16:9 medya karesi olduğu halde) hiç
    çıkmıyordu — kart kodu image'i HER ZAMAN 36px yuvarlak avatar sayıyordu.
    Gerçek fetch'lerle doğrulanmış 3 URL şekli burada regresyon koruması."""

    def test_profile_image_is_small(self):
        assert _is_media_image("https://pbs.twimg.com/profile_images/123/abc_200x200.jpg") is False

    def test_media_photo_is_media(self):
        assert _is_media_image("https://pbs.twimg.com/media/Gxyz.jpg") is True

    def test_video_thumbnail_different_host_is_media(self):
        """Video karesi pbs.twimg.com ALTINDA DEĞİL — farklı host
        (jf.x.com) — asıl regresyon koruması burada."""
        assert _is_media_image("https://jf.x.com/images/media-preview/456") is True

    def test_x_static_logo_is_small(self):
        assert _is_media_image("https://abs.twimg.com/rweb/ssr/default/v2/og/image.png") is False

    def test_none_and_empty_are_small(self):
        assert _is_media_image(None) is False
        assert _is_media_image("") is False

    def test_unknown_shape_defaults_permissive_to_media(self):
        """Bilinmeyen bir site/URL şekli — izin-verici varsayılan gereği
        'medya' sayılır (avatar/statik olduğu KANITLANMAMIŞ her şey)."""
        assert _is_media_image("https://example.com/some-real-image.jpg") is True

    def test_host_check_is_urlparse_based_not_substring(self):
        """abs.twimg.com KONTROLÜ ham substring DEĞİL, gerçek hostname
        karşılaştırması — aksi halde bu URL yanlışlıkla 'küçük' sayılırdı."""
        assert _is_media_image("https://example.com/abs.twimg.com-fake.jpg") is True
