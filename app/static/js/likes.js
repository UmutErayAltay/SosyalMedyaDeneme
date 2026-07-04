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

    async function sendReaction(btn, reaction) {
        if (btn.dataset.busy === '1') return;

        var countEl = btn.querySelector('.like-count');
        var iconEl = btn.querySelector('.reaction-icon');
        var wasLiked = btn.dataset.liked === '1';
        var prevReaction = btn.dataset.reaction || '';
        var prevCount = parseInt(countEl.textContent, 10) || 0;

        // --- Optimistic update ---
        var willRemove = wasLiked && prevReaction === reaction;
        var nextLiked = !willRemove;
        btn.dataset.liked = nextLiked ? '1' : '0';
        btn.classList.toggle('liked', nextLiked);
        iconEl.textContent = nextLiked ? (REACTIONS[reaction] || '👍') : '👍';
        btn.dataset.reaction = nextLiked ? reaction : '';
        countEl.textContent = prevCount + (willRemove ? -1 : (wasLiked ? 0 : 1));
        btn.dataset.busy = '1';

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

            btn.dataset.liked = data.liked ? '1' : '0';
            btn.classList.toggle('liked', data.liked);
            btn.dataset.reaction = data.liked ? (data.reaction || reaction) : '';
            iconEl.textContent = data.liked ? (REACTIONS[data.reaction] || REACTIONS[reaction] || '👍') : '👍';
            countEl.textContent = data.count;
        } catch (err) {
            btn.dataset.liked = wasLiked ? '1' : '0';
            btn.classList.toggle('liked', wasLiked);
            btn.dataset.reaction = prevReaction;
            iconEl.textContent = wasLiked ? (REACTIONS[prevReaction] || '👍') : '👍';
            countEl.textContent = prevCount;
            console.error('Reaksiyon güncellenemedi:', err);
        } finally {
            btn.dataset.busy = '0';
        }
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

    // --- Basılı tutma tespiti (fare + dokunmatik) ---
    function startPress(btn) {
        longPressFired = false;
        clearTimeout(pressTimer);
        pressTimer = setTimeout(function () {
            longPressFired = true;
            openPicker(btn);
        }, LONG_PRESS_MS);
    }
    function cancelPress() {
        clearTimeout(pressTimer);
    }

    document.addEventListener('mousedown', function (e) {
        var btn = e.target.closest('.like-btn');
        if (btn) startPress(btn);
    });
    document.addEventListener('touchstart', function (e) {
        var btn = e.target.closest('.like-btn');
        if (btn) startPress(btn);
    }, { passive: true });
    ['mouseup', 'mouseleave', 'touchend', 'touchcancel'].forEach(function (evt) {
        document.addEventListener(evt, cancelPress);
    });

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
