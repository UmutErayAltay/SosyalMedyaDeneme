// Navbar zil rozeti — 25 saniyede bir okunmamış bildirim sayısını kontrol eder.
// Realtime yerine polling: bu ölçekte (arkadaş grubu) yeterli ve daha az yük getiriyor.

(function () {
    var badge = document.getElementById('notif-badge');
    var live = document.getElementById('notif-live');
    if (!badge) return;

    var lastCount = parseInt(badge.textContent, 10) || 0;

    function update(count) {
        if (count > 0) {
            badge.textContent = count;
            badge.classList.remove('hidden');
        } else {
            badge.textContent = '';
            badge.classList.add('hidden');
        }
        // Sadece sayı arttığında ekran okuyucuya duyur (her poll'da değil)
        if (count > lastCount && live) {
            live.textContent = count + ' yeni bildirim';
        }
        lastCount = count;
    }

    async function poll() {
        try {
            var res = await fetch('/notifications/unread-count', {
                headers: { 'X-Requested-With': 'fetch' },
            });
            if (!res.ok) return;
            var data = await res.json();
            update(data.count);
        } catch (err) {
            // Sessizce yut — bir sonraki poll'da tekrar denenir
        }
    }

    setInterval(poll, 25000);
})();
