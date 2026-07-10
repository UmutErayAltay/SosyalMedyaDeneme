// Mesajlaşma: sol listeden bir konuşmaya tıklayınca tam sayfa yenilemeden
// AJAX ile sağ paneli değiştirir. URL history.pushState ile güncellenir
// (geri/ileri tuşları ve deep-link çalışmaya devam eder).

(function () {
    var layout = document.getElementById('messages-layout');
    if (!layout) return;

    async function loadConversation(url, push) {
        try {
            var res = await fetch(url, { headers: { 'X-Requested-With': 'fetch' } });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var html = await res.text();

            var temp = document.createElement('div');
            temp.innerHTML = html;
            var newPanel = temp.querySelector('#conversation-panel');
            var oldPanel = document.getElementById('conversation-panel');
            if (!newPanel || !oldPanel) throw new Error('Panel bulunamadı');
            oldPanel.replaceWith(newPanel);

            layout.classList.add('showing-panel');

            document.querySelectorAll('.inbox-item').forEach(function (item) {
                item.classList.toggle('active', item.getAttribute('href') === url);
            });

            if (push) history.pushState({}, '', url);
            if (window.initConversation) window.initConversation();

            var input = document.getElementById('msg-input');
            if (input) input.focus();
        } catch (err) {
            console.error('Konuşma yüklenemedi, tam sayfa yenileniyor:', err);
            window.location.href = url;
        }
    }

    document.addEventListener('click', function (e) {
        var link = e.target.closest('.inbox-item');
        if (link) {
            e.preventDefault();
            loadConversation(link.getAttribute('href'), true);
            return;
        }

        // "Daha eski mesajları yükle" — paneli ?all=1 ile yeniden getirir
        // (açılışta yalnızca son N mesaj gelir, bkz. views.py MESSAGE_PAGE)
        var loadAll = e.target.closest('.msg-load-all');
        if (loadAll) {
            e.preventDefault();
            loadConversation(loadAll.getAttribute('href'), false);
            return;
        }

        // Mobilde "Geri" — listeye dönmek için yeniden fetch gerekmiyor,
        // sidebar zaten DOM'da; sadece görünürlük class'ı değişiyor.
        var back = e.target.closest('.conv-back-link');
        if (back && window.matchMedia('(max-width: 720px)').matches) {
            e.preventDefault();
            layout.classList.remove('showing-panel');
            history.pushState({}, '', back.getAttribute('href'));
        }
    });

    window.addEventListener('popstate', function () {
        var path = location.pathname;
        var match = path.match(/^\/messages\/([0-9a-f-]{36})$/i);
        if (match) {
            loadConversation(path, false);
        } else if (/^\/messages\/?$/.test(path)) {
            layout.classList.remove('showing-panel');
        } else {
            window.location.reload();
        }
    });
})();
