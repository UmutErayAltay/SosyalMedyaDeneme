// Mesajlaşma: sol listeden bir konuşmaya tıklayınca tam sayfa yenilemeden
// AJAX ile sağ paneli değiştirir. URL history.pushState ile güncellenir
// (geri/ileri tuşları ve deep-link çalışmaya devam eder).

(function () {
    var layout = document.getElementById('messages-layout');
    if (!layout) return;

    // --- Ön-yükleme (prefetch): istek, tıklama TAMAMLANMADAN başlar ---
    // Fareyle üzerine gelince / parmak basar basmaz (pointerdown) panel HTML'i
    // çekilmeye başlanır; click geldiğinde çoğu zaman yanıt yoldadır ya da
    // gelmiştir — sohbet "tıkladığım gibi açılsın" isteği (kullanıcı).
    // 10sn TTL: bayat panelin (bu arada mesaj gelmiş olabilir) gösterilmesini
    // sınırlar; realtime aboneliği zaten swap sonrası taze kurulur.
    var prefetchCache = {}; // url -> { promise, time }

    function prefetchPanel(url) {
        var now = Date.now();
        var hit = prefetchCache[url];
        if (hit && now - hit.time < 10000) return hit.promise;
        // X-Prefetch: sunucu okundu/aktif YAZMALARINI atlar — sadece hover
        // eden (hiç açmayan) kullanıcı karşı tarafa ✓✓ göndermesin. Gerçek
        // açılışta okundu işaretleme aşağıda (swap sonrası POST) yapılır.
        var p = fetch(url, { headers: { 'X-Requested-With': 'fetch', 'X-Prefetch': '1' } })
            .then(function (res) {
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                return res.text();
            });
        prefetchCache[url] = { promise: p, time: now };
        p.catch(function () { delete prefetchCache[url]; });
        return p;
    }

    // --- İskelet panel: tıklama ANINDA görsel tepki (algılanan hız) ---
    // Gerçek panel gelene kadar başlıkta tıklanan sohbetin adı/avatarı +
    // parıldayan balon yer tutucuları gösterilir.
    function showSkeleton(link) {
        var old = document.getElementById('conversation-panel');
        if (!old) return;
        var skel = document.createElement('div');
        skel.className = 'messages-panel';
        skel.id = 'conversation-panel';

        var header = document.createElement('header');
        header.className = 'conv-header';
        var back = document.createElement('a');
        back.className = 'conv-back-link';
        back.href = '/messages/';
        back.textContent = '← Geri';
        header.appendChild(back);
        var av = link.querySelector('img.avatar');
        if (av) {
            var avClone = av.cloneNode(false);
            avClone.className = 'avatar';
            header.appendChild(avClone);
        }
        var h2 = document.createElement('h2');
        var nameEl = link.querySelector('strong');
        h2.textContent = nameEl ? nameEl.textContent : '';
        header.appendChild(h2);
        skel.appendChild(header);

        var stream = document.createElement('div');
        stream.className = 'message-stream msg-skeleton-stream';
        stream.setAttribute('aria-label', 'Mesajlar yükleniyor');
        for (var i = 0; i < 7; i++) {
            var b = document.createElement('div');
            b.className = 'msg-skel' + (i % 2 ? ' mine' : '');
            stream.appendChild(b);
        }
        skel.appendChild(stream);

        old.replaceWith(skel);
        layout.classList.add('showing-panel');
    }

    // Hızlı art arda iki sohbete tıklanırsa YAVAŞ gelen eski yanıt hızlı
    // geleni ezmesin — yalnızca en son tıklamanın sonucu uygulanır
    var loadSeq = 0;

    async function loadConversation(url, push) {
        var seq = ++loadSeq;
        try {
            var html = await prefetchPanel(url);
            if (seq !== loadSeq) return; // bu sonuç bayat, daha yeni tıklama var

            var temp = document.createElement('div');
            temp.innerHTML = html;
            var newPanel = temp.querySelector('#conversation-panel');
            var oldPanel = document.getElementById('conversation-panel');
            if (!newPanel || !oldPanel) throw new Error('Panel bulunamadı');
            oldPanel.replaceWith(newPanel);

            // Grup yönetim modalı #conversation-panel'in KARDEŞİ olarak
            // render edilir (bkz. _conversation_panel.html), bu yüzden yukarıdaki
            // replaceWith onu kapsamaz — atlanırsa AJAX ile açılan grup
            // sohbetlerinde modal hiç DOM'a girmez ve "Yönet" butonu hiçbir şey
            // yapmaz (eski/yanlış conversation_id'li kalıntı da aynı şekilde
            // sorun olurdu). Burada eskisi silinip taze markup'la değiştirilir.
            var oldModal = document.getElementById('group-manage-modal');
            if (oldModal) oldModal.remove();
            var newModal = temp.querySelector('#group-manage-modal');
            if (newModal) newPanel.insertAdjacentElement('afterend', newModal);
            // Tüketilen ön-yükleme tekrar kullanılmasın — aynı sohbete ikinci
            // tıklama TAZE panel çeker (aradaki mesajlar kaçmasın)
            delete prefetchCache[url];

            // Panel X-Prefetch ile (yan-etkisiz) çekildi — GERÇEK açılışta
            // okundu + bildirim işaretlemesi burada tetiklenir (sunucudaki
            // mark-read ucu ikisini de yapar); /active ping'ini chat.js
            // initConversation zaten atıyor.
            var convId = newPanel.dataset.conversationId;
            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            if (convId && csrfMeta) {
                fetch('/messages/' + convId + '/mark-read', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfMeta.content }
                }).then(function () {
                    // Okundu yazıldı — navbar rozeti 25sn poll'u beklemeden insin
                    if (window.refreshMessagesBadge) window.refreshMessagesBadge();
                }).catch(function () { /* rozet en geç polling'de düzelir */ });
            }

            layout.classList.add('showing-panel');

            document.querySelectorAll('.inbox-item').forEach(function (item) {
                var isActive = item.getAttribute('href') === url;
                item.classList.toggle('active', isActive);
                // Sohbet açıldı = okundu: nokta ve vurgu anında kalksın
                // (sunucu tarafı zaten yukarıdaki mark-read POST'uyla okunur)
                if (isActive) {
                    item.classList.remove('unread');
                    var dot = item.querySelector('.unread-dot');
                    if (dot) dot.remove();
                }
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

    // Fare üzerine gelince / basar basmaz ön-yükle (click'ten ~100-300ms önce).
    // pointerover KULLANILIR (pointerenter kabarcıklanmaz, document-level
    // delegation'la çalışmaz); alt öğeler arasında gezinirken tekrar tekrar
    // tetiklenmesi sorun değil — prefetchPanel TTL cache'li, tek istek atar.
    document.addEventListener('pointerover', function (e) {
        var link = e.target.closest ? e.target.closest('.inbox-item') : null;
        if (link) prefetchPanel(link.getAttribute('href'));
    });
    document.addEventListener('pointerdown', function (e) {
        var link = e.target.closest ? e.target.closest('.inbox-item') : null;
        if (link) prefetchPanel(link.getAttribute('href'));
    });

    document.addEventListener('click', function (e) {
        var link = e.target.closest('.inbox-item');
        if (link) {
            e.preventDefault();
            showSkeleton(link); // anında görsel tepki — içerik gelince değişir
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
