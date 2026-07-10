// Basit servis çalışanı — SADECE statik varlıkları (CSS/JS/ikon) önbelleğe alır.
// HTML sayfaları ve API/AJAX istekleri kasıtlı olarak hiç önbelleklenmez:
// bu uygulamadaki içerik kullanıcıya özel ve sürekli değişiyor (feed, mesajlar,
// bildirimler) — sayfa önbelleklenirse bayat veri veya (paylaşılan bir cihazda)
// kullanıcılar arası veri sızıntısı riski doğar. Statik varlıklar için
// NETWORK-FIRST: önce ağdan taze halini çek, sadece ağ ERİŞİLEMEZSE (offline)
// önbelleğe düş. Önceden stale-while-revalidate kullanılıyordu (önce önbellek,
// arka planda güncelle) — bu, her CSS/JS deploy'undan sonra kullanıcının
// düzeltmeyi görebilmesi için sayfayı İKİ KEZ yenilemesi gerektiği anlamına
// geliyordu (ilk yenileme sadece arka planda önbelleği günceller, düzeltme
// ancak İKİNCİ yenilemede görünür) — bu yüzden gerçek bir kullanıcı, kodu
// düzeltilmiş olsa bile "değişiklik olmamış" sanabiliyordu (bkz. navbar/
// feed-sidebar senkron kayma bug raporu, tek yenilemeden sonra hâlâ eski
// halin görünmesiyle fark edildi).
// v2: ?v=mtime sürümlemesi ÖNCESİNDEN kalan sürümsüz girdiler atılsın diye
// yükseltildi — activate handler'ı eski adlı cache'i komple siler.
const CACHE_NAME = 'sosyal-static-v2';
const STATIC_ASSETS = ['/static/css/style.css'];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys.filter(function (k) { return k !== CACHE_NAME; })
                    .map(function (k) { return caches.delete(k); })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', function (event) {
    var url = new URL(event.request.url);

    if (event.request.method !== 'GET' || url.pathname.indexOf('/static/') !== 0) {
        return; // sayfa/API istekleri: servis çalışanı hiç araya girmez
    }

    event.respondWith(
        fetch(event.request).then(function (response) {
            if (response.ok) {
                var clone = response.clone();
                caches.open(CACHE_NAME).then(function (cache) {
                    cache.put(event.request, clone);
                });
            }
            return response;
        }).catch(function () {
            return caches.match(event.request);
        })
    );
});

// --- Web Push: sunucudan gelen push event'ini bildirim olarak göster ---
self.addEventListener('push', function (event) {
    var data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { /* boş payload */ }
    var title = data.title || 'Sosyal';
    var options = {
        body: data.body || '',
        icon: '/static/img/icon-192.png',
        badge: '/static/img/icon-192.png',
        data: { url: data.url || '/' },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

// Bildirime tıklanınca: zaten açık bir sekme varsa ona odaklan, yoksa yeni aç
self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            for (var i = 0; i < clientList.length; i++) {
                var c = clientList[i];
                if (c.url.indexOf(url) !== -1 && 'focus' in c) return c.focus();
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
