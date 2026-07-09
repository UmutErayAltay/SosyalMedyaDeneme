// Beğeni / emoji reaksiyon butonu:
// - Kısa tıklama: hızlı "👍 like" toggle (eskisi gibi anında geri bildirim)
// - Basılı tutma (500ms, fare veya dokunmatik): 5 emoji seçici popup açılır
// Optimistic UI: arayüz anında güncellenir, istek başarısız olursa geri alınır.

(function () {
    var REACTIONS = { like: '👍', love: '❤️', haha: '😂', wow: '😮', sad: '😢' };
    var LONG_PRESS_MS = 500;

    var pressTimer = null;
    var longPressFired = false;
    var activeBtn = null;

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    function sendReaction(btn, reaction) {
        // Ard arda tıklamalar DÜŞÜRÜLMEZ (önceden busy bayrağı ikinci tıklamayı
        // yutuyordu — kullanıcı raporu: "bekletiyor/kasıyor"). UI her tıklamada
        // ANINDA güncellenir; ağ istekleri sıraya girer (sunucu toggle sırası
        // korunur) ve UI'ya yalnızca EN SON isteğin yanıtı uygulanır.
        var countEl = btn.querySelector('.like-count');
        var iconEl = btn.querySelector('.reaction-icon');
        var wasLiked = btn.dataset.liked === '1';
        var prevReaction = btn.dataset.reaction || '';
        var prevCount = parseInt(countEl.textContent, 10) || 0;

        // --- Optimistic update (anında) ---
        var willRemove = wasLiked && prevReaction === reaction;
        var nextLiked = !willRemove;
        btn.dataset.liked = nextLiked ? '1' : '0';
        btn.classList.toggle('liked', nextLiked);
        iconEl.textContent = nextLiked ? (REACTIONS[reaction] || '👍') : '👍';
        btn.dataset.reaction = nextLiked ? reaction : '';
        countEl.textContent = prevCount + (willRemove ? -1 : (wasLiked ? 0 : 1));

        btn._seq = (btn._seq || 0) + 1;
        var mySeq = btn._seq;
        btn._chain = (btn._chain || Promise.resolve()).then(async function () {
            try {
                var res = await fetch(btn.dataset.likeUrl, {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'fetch',
                        'X-CSRF-Token': csrfToken(),
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: 'reaction=' + encodeURIComponent(reaction),
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                var data = await res.json();
                if (btn._seq !== mySeq) return; // daha yeni tıklama var — onun yanıtı kazansın

                btn.dataset.liked = data.liked ? '1' : '0';
                btn.classList.toggle('liked', data.liked);
                btn.dataset.reaction = data.liked ? (data.reaction || reaction) : '';
                iconEl.textContent = data.liked ? (REACTIONS[data.reaction] || REACTIONS[reaction] || '👍') : '👍';
                countEl.textContent = data.count;
            } catch (err) {
                console.error('Reaksiyon güncellenemedi:', err);
                if (btn._seq !== mySeq) return;
                btn.dataset.liked = wasLiked ? '1' : '0';
                btn.classList.toggle('liked', wasLiked);
                btn.dataset.reaction = prevReaction;
                iconEl.textContent = wasLiked ? (REACTIONS[prevReaction] || '👍') : '👍';
                countEl.textContent = prevCount;
            }
        });
    }

    // --- Reaksiyon seçici popup: tek bir DOM elemanı, ihtiyaç oldukça yeniden konumlanır ---
    var picker = null;
    function ensurePicker() {
        if (picker) return picker;
        picker = document.createElement('div');
        picker.className = 'reaction-picker';
        picker.setAttribute('role', 'menu');
        picker.setAttribute('aria-label', 'Reaksiyon seç');
        picker.hidden = true;
        Object.keys(REACTIONS).forEach(function (key) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'reaction-option';
            b.dataset.reaction = key;
            b.textContent = REACTIONS[key];
            b.setAttribute('role', 'menuitem');
            b.setAttribute('aria-label', key);
            picker.appendChild(b);
        });
        document.body.appendChild(picker);
        return picker;
    }

    function openPicker(btn) {
        var p = ensurePicker();
        activeBtn = btn;
        var rect = btn.getBoundingClientRect();
        p.hidden = false;
        var pRect = p.getBoundingClientRect();
        var left = rect.left + window.scrollX + rect.width / 2 - pRect.width / 2;
        left = Math.max(8, Math.min(left, window.scrollX + document.documentElement.clientWidth - pRect.width - 8));
        p.style.left = left + 'px';
        p.style.top = (rect.top + window.scrollY - pRect.height - 8) + 'px';
    }

    function closePicker() {
        if (picker) picker.hidden = true;
        activeBtn = null;
    }

    document.addEventListener('click', function (e) {
        if (picker && !picker.hidden) {
            var option = e.target.closest('.reaction-option');
            if (option && activeBtn) {
                sendReaction(activeBtn, option.dataset.reaction);
                closePicker();
                return;
            }
            if (!picker.contains(e.target)) {
                closePicker();
            }
        }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && picker && !picker.hidden) closePicker();
    });

    // --- Basılı tutma tespiti (fare + dokunmatik) — press+hold+sürükle+bırak deseni ---
    function startPress(btn) {
        longPressFired = false;
        clearTimeout(pressTimer);
        pressTimer = setTimeout(function () {
            longPressFired = true;
            openPicker(btn);
        }, LONG_PRESS_MS);
    }

    function pointFromEvent(e) {
        if (e.changedTouches && e.changedTouches.length) {
            return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
        }
        return { x: e.clientX, y: e.clientY };
    }

    // Basılı tutup emoji üzerine sürükleyip bırakma: mousedown/mouseup farklı
    // elemanlarda gerçekleştiği için tarayıcı native "click" event'i ÜRETMEZ —
    // bu yüzden bırakma noktasındaki elemanı elle buluyoruz (elementFromPoint).
    function endPress(e) {
        clearTimeout(pressTimer);
        if (!longPressFired || !activeBtn) return;

        var point = pointFromEvent(e);
        var el = document.elementFromPoint(point.x, point.y);
        var option = el && el.closest && el.closest('.reaction-option');
        if (option) {
            sendReaction(activeBtn, option.dataset.reaction);
            closePicker();
            longPressFired = false;
        }
        // Emoji üzerinde bırakılmadıysa picker açık kalır — kullanıcı ayrıca
        // tıklayarak seçebilir (yukarıdaki picker click handler'ı devam eder).
    }

    document.addEventListener('mousedown', function (e) {
        var btn = e.target.closest('.like-btn');
        if (btn) startPress(btn);
    });
    document.addEventListener('touchstart', function (e) {
        var btn = e.target.closest('.like-btn');
        if (btn) startPress(btn);
    }, { passive: true });
    document.addEventListener('mouseup', endPress);
    document.addEventListener('touchend', endPress);
    document.addEventListener('touchcancel', function () { clearTimeout(pressTimer); });

    // Basılı tutma sırasında parmak kaydırınca sayfa scroll etmesin (seçim gesture'ı)
    document.addEventListener('touchmove', function (e) {
        if (longPressFired) e.preventDefault();
    }, { passive: false });

    // --- Kısa tıklama: hızlı toggle (varsayılan 'like' reaksiyonu) ---
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.like-btn');
        if (!btn) return;
        e.preventDefault();
        if (longPressFired) {
            // Basılı tutma zaten picker'ı açtı — bu click'i normal toggle olarak işleme
            longPressFired = false;
            return;
        }
        sendReaction(btn, 'like');
    });
})();
