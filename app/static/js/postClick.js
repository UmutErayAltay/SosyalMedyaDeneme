// Post kartına tıklayınca post detayına gider.
// Görsellere, butonlara, linklere, formlara tıklanınca KART navigasyonu tetiklenmez.
//
// ÇAKIŞMA ÖNLEME: Görsellere tıklanınca lightbox açılır (lightbox.js), post
// detayına GİTMEZ. Bu yüzden:
//   - post-content / post-header gibi metin alanlarına tıklanınca → detaya git
//   - .post-image / .post-images / img / .media-thumb'a tıklanınca → lightbox

(function () {
    document.addEventListener('click', function (e) {
        // Repost gömülü kartı: İÇ kart tıklaması ORİJİNAL postun detayına
        // gider (dış karta bubbling yapıp repost'un detayına gitmesin) —
        // selector .card.post olmadığı için aşağıdaki karta yakalanmaz,
        // burada ayrıca ele alınır. Link/buton istisnası aynı.
        var embed = e.target.closest('.repost-embed[data-post-url]');
        if (embed) {
            if (e.target.closest('a, button, form, input, textarea, label')) return;
            var embedUrl = embed.dataset.postUrl;
            if (e.metaKey || e.ctrlKey || e.button === 1) {
                window.open(embedUrl, '_blank');
            } else {
                window.location.href = embedUrl;
            }
            return;
        }

        var card = e.target.closest('.card.post[data-post-url]');
        if (!card) return;

        // Kendi davranışı olan etkileşimli öğeler: kart navigasyonu tetiklenmez
        if (e.target.closest('a, button, form, input, textarea, label')) return;

        // Görsel alanları: lightbox çalışsın, kart navigasyonu olmasın
        if (e.target.closest('img, .post-image, .post-images, .post-image-tile, .media-thumb, .media-grid')) return;

        // Video: native play/pause/scrub kontrolleri kart navigasyonuyla çakışmasın
        if (e.target.closest('video')) return;

        var url = card.dataset.postUrl;
        if (e.metaKey || e.ctrlKey || e.button === 1) {
            window.open(url, '_blank');
        } else {
            window.location.href = url;
        }
    });
})();
