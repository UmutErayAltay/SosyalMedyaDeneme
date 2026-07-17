// Reels (dikey kısa video akışı) — otomatik oynatma/durdurma, ses toggle

(function () {
    // IntersectionObserver: video görüş alanına girince oynat, çıkınca durdur
    var observer = new IntersectionObserver(
        function (entries) {
            entries.forEach(function (entry) {
                var video = entry.target;
                if (entry.isIntersecting) {
                    // Video %50'den fazla görünürse oynat (threshold: 0.5)
                    video.play().catch(function (err) {
                        // Tarayıcı politikası (autoplay gerekir muted + user gesture)
                        console.warn('Video oynatılamadı:', err);
                    });
                } else {
                    // Video görüş alanından çıkınca durdur
                    video.pause();
                }
            });
        },
        { threshold: 0.5 }
    );

    // Tüm reel videolarını observer'a kaydet
    document.querySelectorAll('.reel-video').forEach(function (video) {
        observer.observe(video);
    });

    // Video'ya tıklama: ses aç/kapat toggle
    document.addEventListener('click', function (e) {
        var video = e.target.closest('.reel-video');
        if (!video) return;
        e.preventDefault();
        video.muted = !video.muted;
    });

    // Yeni video (dinamik ekleme sırasında) gözleme ekle
    // Reels sayfasında bu yok, ama future-proof için:
    if ('MutationObserver' in window) {
        var mutationObserver = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node.nodeType === 1) { // Element node
                        var video = node.querySelector && node.querySelector('.reel-video');
                        if (video && !video._observed) {
                            video._observed = true;
                            observer.observe(video);
                        }
                    }
                });
            });
        });
        mutationObserver.observe(document.getElementById('reels-feed') || document.body, {
            childList: true,
            subtree: true
        });
    }
})();
