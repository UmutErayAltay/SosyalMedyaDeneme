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
        // Push API yalnızca HTTPS veya localhost'ta çalışır. Güvensiz origin'de
        // (ör. LAN IP'siyle erişim, http://192.168.x.x:5000) tarayıcı
        // pushManager.subscribe()'ı kriptik "Registration failed - push service
        // error" ile reddeder — kullanıcı bunu anahtar/kod hatası sanıyordu,
        // gerçek sebep ortam kısıtı. Denemeden önce net uyarı ver (Sprint 65).
        if (!window.isSecureContext) {
            setStatus('Bildirimler yalnızca HTTPS veya localhost üzerinden çalışır. Şu an güvenli olmayan bir adrestesin (ör. LAN IP) — sunucu HTTPS ile yayına alınınca çalışacak.');
            return;
        }
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
            var sub;
            try {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(vapidKey),
                });
            } catch (subErr) {
                // "InvalidStateError: ... different applicationServerKey" — tarayıcıda
                // ESKİ bir VAPID key'le kurulmuş bayat bir abonelik varsa (örn. .env'deki
                // anahtar değişti) subscribe() bu hatayla reddeder. Eski aboneliği
                // kaldırıp AYNI anahtarla yeniden denemek genelde çözer — kullanıcıya
                // "başarısız" demeden önce bu kurtarma yolu denenir.
                if (subErr.name === 'InvalidStateError') {
                    var stale = await reg.pushManager.getSubscription();
                    if (stale) await stale.unsubscribe();
                    sub = await reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(vapidKey),
                    });
                } else {
                    throw subErr;
                }
            }
            var subJson = sub.toJSON();
            var res = await fetch('/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                body: JSON.stringify(subJson),
            });
            if (!res.ok) throw new Error('Sunucu kaydı başarısız (HTTP ' + res.status + ')');
            updateButton(true);
            setStatus('Bildirimler açık.');
        } catch (err) {
            // Gerçek tarayıcı hatasını göster — önceden jenerik "Abonelik başarısız
            // oldu" yazıyordu, hangi adımda/neden başarısız olduğu hiç görünmüyordu
            // (kullanıcı raporu, teşhis için kritik).
            console.error('Push abonelik hatası:', err);
            setStatus('Abonelik başarısız: ' + (err.message || err.name || 'bilinmeyen hata'));
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
            console.error('Push abonelikten çıkma hatası:', err);
            setStatus('Kapatma başarısız: ' + (err.message || err.name || 'bilinmeyen hata'));
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
