// Link önizleme kartı: linkify_urls/linkifyUrlsClient tarafından üretilen
// .content-link <a> elemanlarının (post/mesaj/yorum içindeki tıklanabilir
// http(s) linkler) altına, backend'in GET /link-preview endpoint'inden
// (Open Graph) çektiği görsel/başlık/açıklama/domain kartını ekler.
//
// Tasarım kararları:
// - Her "içerik bloğu" (post-content/mesaj/yorum <p>'si) için SADECE İLK
//   linki önizlenir (Discord/Twitter davranışı) — birden fazla link varsa
//   geri kalanı düz link olarak kalır.
// - IntersectionObserver: sadece görünür alana giren bloklar fetch edilir
//   (feed'de onlarca post olabilir, hepsini aynı anda çekmek performans
//   sorunu olurdu).
// - MutationObserver: chat.js/comments.js/infiniteScroll.js gibi script'ler
//   sayfaya AJAX/realtime ile yeni içerik ekliyor — document-level bir
//   gözlemci kullanmak, bu script'lerin her birine ayrı ayrı "yeni içerik
//   eklendi" bildirimi eklemek yerine TEK yerden, sayfadan bağımsız çalışır.
// - Fetch başarısız veya ok:false → kart HİÇ eklenmez, sessizce geç (GIF/
//   TURN-credentials proxy'lerindeki graceful-degradation felsefesiyle aynı,
//   kritik olmayan bir özellik).
// - GÜVENLİK: title/description/domain/site_name dış siteden gelen
//   güvenilmeyen veridir — DOM'a SADECE textContent ile yazılır, innerHTML
//   asla kullanılmaz. image alanı da SADECE dolu ise <img> elemanı
//   oluşturulup .src property'sine atanır (boş/None → <img> hiç render
//   edilmez, bazı tarayıcılarda boş src sayfayı yeniden yükler).
(function () {
    if (!('IntersectionObserver' in window)) return; // eski tarayıcı — önizleme olmadan devam, kritik değil

    var previewCache = new Map();   // url -> {ok, ...} (aynı sayfa oturumunda tekrar fetch etme)
    var pendingFetches = new Set(); // url -> eş zamanlı ikinci fetch'i önlemek için
    var processedBlocks = new WeakSet(); // her içerik bloğunda sadece İLK link işlenir
    var observedLinks = new WeakSet();   // aynı <a> iki kez gözlemlenmesin

    var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            io.unobserve(entry.target);
            fetchAndRenderPreview(entry.target);
        });
    }, { rootMargin: '200px 0px' });

    function ownerBlockOf(link) {
        // content-link'ler her zaman bir <p> (post-content/reel-caption/
        // comment/mesaj) içinde render edilir — kart bu <p>'nin ALTINA
        // (bir sonraki kardeş olarak) eklenir.
        return link.closest('p') || link.parentElement;
    }

    function buildCard(data) {
        var a = document.createElement('a');
        a.className = 'link-preview-card';
        a.href = data.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer nofollow';

        if (data.image) {
            var img = document.createElement('img');
            img.className = 'link-preview-card-img';
            img.loading = 'lazy';
            img.alt = '';
            img.src = data.image; // property ataması — HTML parse etmez, attribute injection riski yok
            a.appendChild(img);
        }

        var body = document.createElement('div');
        body.className = 'link-preview-card-body';

        if (data.title) {
            var title = document.createElement('div');
            title.className = 'link-preview-card-title';
            title.textContent = data.title;
            body.appendChild(title);
        }
        if (data.description) {
            var desc = document.createElement('div');
            desc.className = 'link-preview-card-desc';
            desc.textContent = data.description;
            body.appendChild(desc);
        }
        var domainText = data.site_name || data.domain;
        if (domainText) {
            var domain = document.createElement('div');
            domain.className = 'link-preview-card-domain';
            domain.textContent = domainText;
            body.appendChild(domain);
        }
        a.appendChild(body);
        return a;
    }

    function insertCard(link, data) {
        if (!link.isConnected) return; // link bu arada DOM'dan kaldırılmış olabilir (mesaj silindi/düzenlendi)
        var block = ownerBlockOf(link);
        if (!block || !block.parentElement) return;
        if (block.nextElementSibling && block.nextElementSibling.classList.contains('link-preview-card')) return; // zaten eklenmiş
        block.parentElement.insertBefore(buildCard(data), block.nextSibling);
    }

    function fetchAndRenderPreview(link) {
        var url = link.getAttribute('href');
        if (!url) return;

        if (previewCache.has(url)) {
            var cached = previewCache.get(url);
            if (cached && cached.ok) insertCard(link, cached);
            return;
        }
        if (pendingFetches.has(url)) return;
        pendingFetches.add(url);

        fetch('/link-preview?url=' + encodeURIComponent(url))
            .then(function (res) { return res.ok ? res.json() : { ok: false }; })
            .then(function (data) {
                previewCache.set(url, data);
                if (data && data.ok) insertCard(link, data);
            })
            .catch(function () {
                previewCache.set(url, { ok: false });
            })
            .then(function () {
                pendingFetches.delete(url);
            });
    }

    function scan(root) {
        if (!root || !root.querySelectorAll) return;
        var links = root.classList && root.classList.contains('content-link')
            ? [root]
            : root.querySelectorAll('.content-link');
        links.forEach(function (link) {
            if (observedLinks.has(link)) return;
            var block = ownerBlockOf(link);
            if (!block || processedBlocks.has(block)) return; // blok başına sadece İLK link
            processedBlocks.add(block);
            observedLinks.add(link);
            io.observe(link);
        });
    }

    scan(document);

    // AJAX/realtime ile eklenen yeni post/mesaj/yorum bloklarını yakalar
    // (chat.js optimistic mesaj, comments.js yeni yorum/yanıt, infiniteScroll.js
    // yeni sayfa vb.) — bu script'lerin hiçbirine dokunmadan, tek yerden.
    var bodyObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            mutation.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                scan(node);
            });
        });
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });
})();
