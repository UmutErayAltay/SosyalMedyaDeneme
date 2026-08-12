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
    // url -> aynı URL için sonucu bekleyen <a> listesi. Set DEĞİL Map<url,
    // link[]>: aynı URL'ye ait bir fetch DEVAM EDERKEN (chat.js'in
    // formatSharedPosts()'u yüzünden — bkz. processedBlocks yorumu) o bloğun
    // linki DEĞİŞİRSE, YENİ link de AYNI bekleyen listeye eklenir — fetch
    // sonuçlandığında listedeki HER link için insertCard denenir (her biri
    // kendi isConnected durumunu bağımsız kontrol eder, eskisi no-op olur,
    // güncel/bağlı olan kartı alır). Eskiden ikinci link'in isteği sessizce
    // ATLANIYORDU (pendingFetches.has(url) → return), fetch #1 SADECE artık
    // disconnected olan ilk linke sonuç dönmeye çalışıp hep no-op oluyordu.
    var pendingFetches = new Map();
    // block (<p>) -> o blokta İZLENEN <a> — WeakSet DEĞİL WeakMap: chat.js'in
    // formatSharedPosts() gibi bazı sayfa-yükleme-sonrası geçişleri (mesaj
    // balonundaki \n karakterlerini <br>'ye çevirmek için) p.innerHTML'i
    // TAMAMEN sıfırlıyor — İÇERİK aynı kalsa bile bu, DOM'da BAMBAŞKA bir
    // <a class="content-link"> elemanı yaratıyor, ESKİ eleman disconnected
    // oluyor. Sadece "bu blok işlendi mi" (WeakSet) kontrolü, aynı <p>'de
    // YENİ (geçerli) linki "zaten işlendi" sanıp atlıyordu — kullanıcı
    // raporu: "mesajlarda linklerin önizlemesi yok" (akışta ÇALIŞIYORDU,
    // çünkü post kartları bu şekilde yeniden render edilmiyor). Çözüm: bloğu
    // "işlenmiş" saymaya devam et AMA daha önce izlenen link artık DOM'dan
    // kopmuşsa (isConnected === false) YENİDEN gözlemlemeye izin ver.
    var processedBlocks = new WeakMap();
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

    // --- YouTube tespiti -------------------------------------------------
    // youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID (www./m.
    // opsiyonel, watch?v= yanında başka query paramları olabilir). Backend
    // zaten yönlendirmeleri (ör. youtu.be -> youtube.com/watch) takip edip
    // `data.url`'yi final URL'e çeviriyor, ama garanti olmadığı için hem
    // fetch sonucu hem de linkin ORİJİNAL href'i denenir.
    var YT_ID_RE = /^[\w-]{11}$/;
    function extractYouTubeId(url) {
        if (!url) return null;
        var parsed;
        try {
            parsed = new URL(url);
        } catch (e) {
            return null;
        }
        var host = parsed.hostname.toLowerCase().replace(/^(www|m)\./, '');
        if (host === 'youtu.be') {
            var short = parsed.pathname.replace(/^\//, '').split('/')[0];
            return YT_ID_RE.test(short) ? short : null;
        }
        if (host === 'youtube.com') {
            if (parsed.pathname === '/watch') {
                var v = parsed.searchParams.get('v');
                return v && YT_ID_RE.test(v) ? v : null;
            }
            var shortsMatch = parsed.pathname.match(/^\/shorts\/([\w-]{11})/);
            if (shortsMatch) return shortsMatch[1];
        }
        return null;
    }

    // --- Twitter/X tespiti -------------------------------------------------
    var TWEET_DOMAINS = ['twitter.com', 'x.com', 'mobile.twitter.com'];
    function isTweetPreview(data) {
        var domain = (data.domain || '').toLowerCase();
        if (TWEET_DOMAINS.indexOf(domain) !== -1) return true;
        var siteName = (data.site_name || '').toLowerCase();
        return siteName.indexOf('twitter') !== -1 || siteName.indexOf('x (formerly twitter)') !== -1;
    }

    function stripTweetTitleSuffix(title) {
        // Backend'den "Ad Soyad (@kullaniciadi) on X" formatında geliyor —
        // sondaki " on X"/" on Twitter" gösterimde gereksiz, kırpılır.
        return title.replace(/\s+on\s+(x|twitter)\s*$/i, '').trim();
    }

    function buildGenericCard(data) {
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

    function buildYouTubeCard(data, videoId) {
        var a = document.createElement('a');
        a.className = 'link-preview-card link-preview-card--youtube';
        a.href = data.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer nofollow';

        var media = document.createElement('div');
        media.className = 'link-preview-yt-media';
        media.setAttribute('data-yt-id', videoId);

        var img = document.createElement('img');
        img.className = 'link-preview-yt-thumb';
        img.loading = 'lazy';
        img.alt = '';
        // YouTube'un herkese açık, kimlik doğrulama gerektirmeyen thumbnail
        // CDN'i — ekstra fetch/CORS derdi yok.
        img.src = 'https://img.youtube.com/vi/' + videoId + '/hqdefault.jpg';
        media.appendChild(img);

        var playBtn = document.createElement('button');
        playBtn.type = 'button';
        playBtn.className = 'link-preview-yt-play';
        playBtn.setAttribute('aria-label', 'Videoyu oynat');
        var playIcon = document.createElement('span');
        playIcon.className = 'link-preview-yt-play-icon';
        playIcon.setAttribute('aria-hidden', 'true');
        playIcon.textContent = '▶'; // ▶ — statik karakter, SVG/font-icon gerekmiyor
        playBtn.appendChild(playIcon);
        media.appendChild(playBtn);

        a.appendChild(media);

        if (data.title) {
            var body = document.createElement('div');
            body.className = 'link-preview-card-body';
            var title = document.createElement('div');
            title.className = 'link-preview-card-title';
            title.textContent = data.title;
            body.appendChild(title);
            var domain = document.createElement('div');
            domain.className = 'link-preview-card-domain';
            domain.textContent = 'YouTube';
            body.appendChild(domain);
            a.appendChild(body);
        }
        return a;
    }

    function buildTweetCard(data) {
        var a = document.createElement('a');
        a.className = 'link-preview-card link-preview-card--tweet';
        a.href = data.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer nofollow';

        // Backend (app/link_preview.py::_is_media_image) karar verir: `image`
        // gerçek bir foto/video karesi mi, yoksa küçük profil avatarı mı.
        // Sınıflandırma native (LinkPreviewCard.kt) ile ORTAK — burada
        // TEKRARLANMAZ. X, tweet videosunu oynatılabilir sunmadığı için
        // (og:video/public embed API YOK) inline oynatma YOK: büyük önizleme
        // karesi + karta tıklayınca tweet'i açma (mevcut target=_blank).
        var hasMedia = !!(data.image && data.image_is_media);

        if (hasMedia) {
            var media = document.createElement('img');
            media.className = 'link-preview-tweet-media';
            media.loading = 'lazy';
            media.alt = '';
            media.src = data.image; // property ataması — attribute injection riski yok
            a.appendChild(media);
        }

        var body = document.createElement('div');
        body.className = 'link-preview-card-body';

        var header = document.createElement('div');
        header.className = 'link-preview-tweet-header';
        if (data.image && !hasMedia) {
            var avatar = document.createElement('img');
            avatar.className = 'link-preview-tweet-avatar';
            avatar.loading = 'lazy';
            avatar.alt = '';
            avatar.src = data.image;
            header.appendChild(avatar);
        }
        if (data.title) {
            var name = document.createElement('div');
            name.className = 'link-preview-tweet-name';
            name.textContent = stripTweetTitleSuffix(data.title);
            header.appendChild(name);
        }
        body.appendChild(header);

        if (data.description) {
            var text = document.createElement('div');
            text.className = 'link-preview-tweet-text';
            text.textContent = data.description;
            body.appendChild(text);
        }

        var tag = document.createElement('div');
        tag.className = 'link-preview-card-domain';
        tag.textContent = (data.domain || '').toLowerCase() === 'x.com' ? 'X' : 'Twitter';
        body.appendChild(tag);

        a.appendChild(body);
        return a;
    }

    function buildCard(data, originalHref) {
        var ytId = extractYouTubeId(data.url) || extractYouTubeId(originalHref);
        if (ytId) return buildYouTubeCard(data, ytId);
        if (isTweetPreview(data)) return buildTweetCard(data);
        return buildGenericCard(data);
    }

    function insertCard(link, data) {
        if (!link.isConnected) return; // link bu arada DOM'dan kaldırılmış olabilir (mesaj silindi/düzenlendi)
        var block = ownerBlockOf(link);
        if (!block || !block.parentElement) return;
        if (block.nextElementSibling && block.nextElementSibling.classList.contains('link-preview-card')) return; // zaten eklenmiş
        block.parentElement.insertBefore(buildCard(data, link.getAttribute('href')), block.nextSibling);
    }

    function fetchAndRenderPreview(link) {
        var url = link.getAttribute('href');
        if (!url) return;

        if (previewCache.has(url)) {
            var cached = previewCache.get(url);
            if (cached && cached.ok) insertCard(link, cached);
            return;
        }
        if (pendingFetches.has(url)) {
            pendingFetches.get(url).push(link); // aynı URL için fetch zaten sürüyor — bu link de sonucu bekleyen listeye eklenir
            return;
        }
        pendingFetches.set(url, [link]);

        fetch('/link-preview?url=' + encodeURIComponent(url))
            .then(function (res) { return res.ok ? res.json() : { ok: false }; })
            .then(function (data) {
                previewCache.set(url, data);
                if (data && data.ok) {
                    (pendingFetches.get(url) || []).forEach(function (waitingLink) {
                        insertCard(waitingLink, data);
                    });
                }
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
            if (!block) return;
            var trackedLink = processedBlocks.get(block);
            // Blok daha önce işlendi VE o zamanki link hâlâ DOM'da bağlıysa
            // atla (blok başına sadece İLK link) — trackedLink artık
            // disconnected'sa (bkz. yukarıdaki yorum) bu YENİ linki işlemeye
            // devam et.
            if (trackedLink && trackedLink.isConnected) return;
            processedBlocks.set(block, link);
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

    // YouTube kartı "tıkla-oynat": thumbnail sonradan (infiniteScroll ile)
    // eklenen kartlarda da çalışsın diye document-level delegation — kartlar
    // dinamik oluşturulduğu için her birine ayrı ayrı listener eklemek yerine
    // tek yerden. preventDefault, sarmalayan <a target="_blank">'in
    // navigasyonunu da iptal eder (aynı event'in default action'ı).
    document.addEventListener('click', function (e) {
        var playBtn = e.target.closest('.link-preview-yt-play');
        if (!playBtn) return;
        e.preventDefault();
        e.stopPropagation();
        var media = playBtn.closest('.link-preview-yt-media');
        if (!media) return;
        var videoId = media.getAttribute('data-yt-id');
        if (!videoId) return;
        var iframe = document.createElement('iframe');
        iframe.className = 'link-preview-yt-iframe';
        // youtube-nocookie.com: privacy-enhanced mod, tıklamadan önce hiçbir
        // YouTube isteği/çerezi gitmez.
        iframe.src = 'https://www.youtube-nocookie.com/embed/' + videoId + '?autoplay=1';
        iframe.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture');
        iframe.allowFullscreen = true;
        iframe.setAttribute('frameborder', '0');
        media.textContent = ''; // thumbnail+play butonunu kaldır
        media.appendChild(iframe);
    });
})();
