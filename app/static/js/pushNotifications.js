// Web Push abonelik akışı — bildirim tercihleri sayfasındaki "Anlık
// Bildirimler" kartını yönetir. Tarayıcı izni + service worker gerektirir;
// desteklenmiyorsa veya sunucuda VAPID anahtarı yoksa buton devre dışı kalır
// (feature-flag deseni, GIF/Klipy ile aynı graceful degradation).

(function () {
    var btn = document.getElementById('push-toggle-btn');
    var statusText = document.getElementById('push-status-text');
    if (!btn) return;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    // VAPID public key (base64url) → Uint8Array — pushManager.subscribe()'ın
    // applicationServerKey'i bu formatı bekler (standart boilerplate).
    function urlBase64ToUint8Array(base64String) {
        var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
        var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        var rawData = window.atob(base64);
        var outputArray = new Uint8Array(rawData.length);
        for (var i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    function setStatus(text) {
        if (statusText) statusText.textContent = text;
    }

    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
        btn.disabled = true;
        btn.textContent = 'Tarayıcın desteklemiyor';
        return;
    }

    var vapidKey = null;

    function updateButton(subscribed) {
        btn.textContent = subscribed ? 'Bildirimleri Kapat' : 'Bildirimleri Aç';
        btn.dataset.subscribed = subscribed ? '1' : '0';
    }

    async function init() {
        try {
            var res = await fetch('/push/vapid-public-key');
            var data = await res.json();
            if (!data.enabled) {
                btn.disabled = true;
                btn.textContent = 'Şu anda kullanılamıyor';
                return;
            }
            vapidKey = data.key;

            var reg = await navigator.serviceWorker.ready;
            var sub = await reg.pushManager.getSubscription();
            updateButton(!!sub);
        } catch (err) {
            btn.disabled = true;
            btn.textContent = 'Yüklenemedi';
        }
    }

    async function subscribe() {
        if (Notification.permission === 'denied') {
            setStatus('Bildirim izni tarayıcı ayarlarından engellenmiş — açmak için site ayarlarını kontrol et.');
            return;
        }
        var permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            setStatus('İzin verilmedi.');
            return;
        }
        try {
            var reg = await navigator.serviceWorker.ready;
            var sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(vapidKey),
            });
            var subJson = sub.toJSON();
            var res = await fetch('/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                body: JSON.stringify(subJson),
            });
            if (!res.ok) throw new Error('Sunucu kaydı başarısız');
            updateButton(true);
            setStatus('Bildirimler açık.');
        } catch (err) {
            setStatus('Abonelik başarısız oldu.');
        }
    }

    async function unsubscribe() {
        try {
            var reg = await navigator.serviceWorker.ready;
            var sub = await reg.pushManager.getSubscription();
            if (sub) {
                var endpoint = sub.endpoint;
                await sub.unsubscribe();
                await fetch('/push/unsubscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                    body: JSON.stringify({ endpoint: endpoint }),
                });
            }
            updateButton(false);
            setStatus('Bildirimler kapatıldı.');
        } catch (err) {
            setStatus('Kapatma başarısız oldu.');
        }
    }

    btn.addEventListener('click', function () {
        if (btn.dataset.subscribed === '1') {
            unsubscribe();
        } else {
            subscribe();
        }
    });

    init();
})();
