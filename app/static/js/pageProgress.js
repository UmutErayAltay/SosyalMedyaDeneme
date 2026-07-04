// Sayfa geçişlerinde ince ilerleme çubuğu — gerçek yükleme hızını değiştirmez ama
// "sayfa kasıyor" algısını kırar (algısal performans, NotebookLM araştırmasında önerildi).

(function () {
    var bar = document.getElementById('page-progress-bar');
    if (!bar) return;

    function start() {
        bar.style.transition = 'none';
        bar.style.width = '0%';
        bar.style.opacity = '1';
        // Bir sonraki frame'de transition'ı geri aç (0'dan başlaması için gerekli)
        requestAnimationFrame(function () {
            bar.style.transition = 'width 4s cubic-bezier(0.1, 0.5, 0.1, 1)';
            bar.style.width = '80%';
        });
    }

    // Sekme kapanıyor/sayfa değişiyor — yeni sayfa gelene kadar çubuk görünür kalır
    window.addEventListener('beforeunload', start);

    // Aynı-origin link tıklamalarında hemen başlat (yeni sekme/indirme/sadece-hash hariç)
    document.addEventListener('click', function (e) {
        var a = e.target.closest('a[href]');
        if (!a) return;
        if (a.target === '_blank' || a.hasAttribute('download')) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

        var url;
        try {
            url = new URL(a.href, window.location.href);
        } catch (err) {
            return;
        }
        if (url.origin !== window.location.origin) return;
        // Sadece sayfa içi çapa (#) linkleriyse atla
        if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) {
            return;
        }
        start();
    });

    // Normal form submit'lerinde de başlat (AJAX formları zaten preventDefault yapar,
    // bu durumda submit event'i defaultPrevented olur ve buraya girmez)
    document.addEventListener('submit', function (e) {
        if (e.defaultPrevented) return;
        start();
    });
})();
