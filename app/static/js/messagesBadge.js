// Navbar "Mesajlar" rozeti — notifications.js'teki zil rozeti deseninin
// aynısı (25sn polling, Realtime yerine — bu ölçekte yeterli ve daha az yük).

(function () {
    var badge = document.getElementById('messages-badge');
    var link = document.getElementById('messages-nav-link');
    if (!badge) return;

    function updateBadge(count) {
        if (count > 0) {
            badge.textContent = count;
            badge.classList.remove('hidden');
        } else {
            badge.textContent = '';
            badge.classList.add('hidden');
        }
    }

    async function poll() {
        try {
            var res = await fetch('/messages/unread-count', {
                headers: { 'X-Requested-With': 'fetch' },
            });
            if (!res.ok) return;
            var data = await res.json();
            updateBadge(data.count);
        } catch (err) {
            // Sessizce yut — bir sonraki poll'da tekrar denenir
        }
    }

    setInterval(poll, 25000);
    // Gerçek-zamanlı tetikleme kancası (bkz. liveBadges.js) — polling
    // artık sadece güvenlik ağı
    window.refreshMessagesBadge = poll;

    // Linke tıklayınca rozet hemen sıfırlanır (sunucu isteğinin bitmesini
    // beklemeden) — tıklamak zaten "gördüm" niyetini gösterir, tam sayfa
    // yüklemesi zaten inbox/konuşma sayfasında gerçek okunma durumunu
    // kalıcı hale getirir (bkz. notifications.js openPanel() aynı gerekçe).
    if (link) {
        link.addEventListener('click', function () { updateBadge(0); });
    }
})();
