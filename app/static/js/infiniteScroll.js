// Akışta sonsuz kaydırma — "Daha eski →" butonunun yerini alır (buton JS
// yoksa/IntersectionObserver desteklenmiyorsa fallback olarak kalır).
//
// Kasma (jank) olmaması için iki önlem:
// 1) Sentinel, viewport'a girmeden ÇOK ÖNCE (rootMargin 1200px) tetiklenir —
//    kullanıcı liste sonuna varmadan sonraki sayfa çoktan yüklenmiş olur,
//    "bekleme + zıplama" hissi oluşmaz.
// 2) Sunucu AJAX isteğinde sadece post kartlarını render eder (kenar çubuğu/
//    hikaye/öneri sorguları atlanır, bkz. routes/posts.py feed()) — yanıt
//    küçük ve hızlıdır; görseller zaten loading="lazy".
//
// Etkileşimler (beğeni/kaydet/anket/lightbox/kart tıklama) yeni eklenen
// kartlarda otomatik çalışır — ilgili tüm JS'ler document-level event
// delegation kullanıyor, yeniden init gerekmez.

(function () {
    var list = document.getElementById('post-list');
    var sentinel = document.getElementById('feed-sentinel');
    var loading = document.getElementById('feed-loading');
    var pagination = document.getElementById('feed-pagination');
    if (!list || !sentinel || !('IntersectionObserver' in window)) return;

    var hasNext = list.dataset.hasNext === '1';
    var nextPage = parseInt(list.dataset.nextPage, 10) || 2;
    var feedUrl = list.dataset.feedUrl || '/';
    var busy = false;
    var failCount = 0;

    if (!hasNext) return;
    // JS aktif: buton yerine kaydırdıkça yükleme devrede
    if (pagination) pagination.hidden = true;

    async function loadNext() {
        if (busy || !hasNext) return;
        busy = true;
        if (loading) loading.hidden = false;
        try {
            var res = await fetch(feedUrl + '?page=' + nextPage, {
                headers: { 'X-Requested-With': 'fetch' },
            });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var html = await res.text();
            hasNext = res.headers.get('X-Has-Next') === '1';
            nextPage += 1;
            failCount = 0;
            if (html.trim()) list.insertAdjacentHTML('beforeend', html);
            if (!hasNext) observer.disconnect();
        } catch (err) {
            console.error('Sonraki postlar yüklenemedi:', err);
            // 3 kez üst üste başarısız olursa vazgeç, fallback butonu geri göster
            failCount += 1;
            if (failCount >= 3) {
                observer.disconnect();
                if (pagination) pagination.hidden = false;
            }
        } finally {
            busy = false;
            if (loading) loading.hidden = true;
        }
    }

    var observer = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) loadNext();
    }, { rootMargin: '1200px 0px' });

    observer.observe(sentinel);
})();
