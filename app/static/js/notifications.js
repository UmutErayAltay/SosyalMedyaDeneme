// Navbar zil rozeti — 25 saniyede bir okunmamış bildirim sayısını kontrol eder.
// Realtime yerine polling: bu ölçekte (arkadaş grubu) yeterli ve daha az yük getiriyor.
// Zil tıklanınca tam sayfa yönlendirme yerine sağdan kayan panel açılır (JS yoksa
// <a href> normal /notifications sayfasına gider — progressive enhancement).

(function () {
    var badge = document.getElementById('notif-badge');
    var live = document.getElementById('notif-live');
    var bell = document.getElementById('notif-bell');
    var panel = document.getElementById('notif-panel');
    var backdrop = document.getElementById('notif-panel-backdrop');
    var closeBtn = document.getElementById('notif-panel-close');
    var list = document.getElementById('notif-panel-list');
    if (!badge) return;

    var lastCount = parseInt(badge.textContent, 10) || 0;
    var lastFocused = null;

    function updateBadge(count) {
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
            updateBadge(data.count);
        } catch (err) {
            // Sessizce yut — bir sonraki poll'da tekrar denenir
        }
    }

    setInterval(poll, 25000);

    // --- Slide-in panel ---
    if (!bell || !panel) return;

    function timeAgo(iso) {
        var diffMs = Date.now() - new Date(iso + 'Z').getTime();
        var mins = Math.floor(diffMs / 60000);
        if (mins < 1) return 'şimdi';
        if (mins < 60) return mins + 'dk';
        var hours = Math.floor(mins / 60);
        if (hours < 24) return hours + 'sa';
        return Math.floor(hours / 24) + 'g';
    }

    function renderItem(n) {
        var a = document.createElement('a');
        a.href = n.target_url;
        a.className = 'notif-item' + (n.is_read ? '' : ' unread');

        var avatar;
        if (n.avatar_url) {
            avatar = document.createElement('img');
            avatar.src = n.avatar_url;
            avatar.className = 'avatar avatar-sm';
            avatar.alt = '';
            avatar.loading = 'lazy';
        } else {
            avatar = document.createElement('div');
            avatar.className = 'avatar avatar-sm avatar-placeholder';
        }

        var textSpan = document.createElement('span');
        textSpan.className = 'notif-text';
        var strong = document.createElement('strong');
        strong.textContent = n.username;
        textSpan.appendChild(strong);
        textSpan.appendChild(document.createTextNode(' ' + n.text));

        var time = document.createElement('span');
        time.className = 'time';
        time.textContent = timeAgo(n.created_at);

        a.append(avatar, textSpan, time);
        return a;
    }

    async function loadPanel() {
        list.innerHTML = '<p class="muted center">Yükleniyor...</p>';
        try {
            var res = await fetch('/notifications/panel', {
                headers: { 'X-Requested-With': 'fetch' },
            });
            if (!res.ok) throw new Error('İstek başarısız');
            var data = await res.json();
            list.innerHTML = '';
            if (!data.notifications.length) {
                list.innerHTML = '<p class="muted center">Henüz bildirimin yok.</p>';
                return;
            }
            data.notifications.forEach(function (n) {
                list.appendChild(renderItem(n));
            });
            // Panel açıldığında sunucu bunları okundu işaretledi — rozeti sıfırla
            updateBadge(0);
        } catch (err) {
            list.innerHTML = '<p class="muted center">Bildirimler yüklenemedi.</p>';
        }
    }

    function openPanel(e) {
        if (e) e.preventDefault();
        lastFocused = document.activeElement;
        panel.hidden = false;
        backdrop.hidden = false;
        document.body.style.overflow = 'hidden';
        closeBtn.focus();
        loadPanel();
    }

    function closePanel() {
        panel.hidden = true;
        backdrop.hidden = true;
        document.body.style.overflow = '';
        if (lastFocused) lastFocused.focus();
    }

    bell.addEventListener('click', openPanel);
    closeBtn.addEventListener('click', closePanel);
    backdrop.addEventListener('click', closePanel);

    document.addEventListener('keydown', function (e) {
        if (panel.hidden) return;
        if (e.key === 'Escape') {
            closePanel();
            return;
        }
        if (e.key === 'Tab') {
            var focusable = panel.querySelectorAll('button, a[href]');
            if (!focusable.length) return;
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });
})();
