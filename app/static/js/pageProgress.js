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

    function hide() {
        bar.style.transition = 'opacity 0.2s';
        bar.style.opacity = '0';
        bar.style.width = '0%';
    }

    // Aynı-origin link tıklamalarında başlat (yeni sekme/indirme/sadece-hash hariç).
    // KONTROL setTimeout(0) İÇİNDE: bildirim zili / mesaj listesi / dropdown gibi
    // linkleri BAŞKA bir JS preventDefault ile yakalıyor — bar başlatılıp
    // navigasyon hiç olmayınca %75-80'de takılı kalıyordu (kullanıcı raporu).
    // Dispatch bittikten sonra defaultPrevented kesinleşir.
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
        setTimeout(function () {
            if (!e.defaultPrevented) start();
        }, 0);
    });

    // Normal form submit'lerinde de başlat — aynı sebeple ertelenmiş kontrol
    // (AJAX formlarının preventDefault'u bizden SONRA çalışabiliyor)
    document.addEventListener('submit', function (e) {
        setTimeout(function () {
            if (!e.defaultPrevented) start();
        }, 0);
    });

    // bfcache'ten dönüşte (geri tuşu) bar %80'de asılı kalmasın
    window.addEventListener('pageshow', hide);
})();
