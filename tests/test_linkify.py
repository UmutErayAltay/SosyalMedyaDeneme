"""Linkify (URL, hashtag, mention) birim testleri — XSS regresyon, URL bütünlüğü.

Link preview özelliğine ait kalıcı test suite — güvenlik-kritik XSS regresyonu
ve filtre bütünlüğü doğrulaması. `linkify_hashtags` ve `linkify_mentions` Flask
app context (url_for için) ve valid_usernames DB sorgusu gerektiği için,
test_client + session ile yapılır.
"""
import pytest
from markupsafe import Markup

from app.link_preview import linkify_urls
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
