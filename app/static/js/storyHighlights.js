// Hikaye öne çıkanları (highlights, #41): feed'deki hikaye görüntüleyicide
// "⭐ Öne çıkar" ile eklenen kalıcı koleksiyonlar, profilde gösterilir.
// İki bağımsız parça: (1) ekleme picker'ı — stories.js'in fırlattığı
// 'open-highlight-picker' custom event'ini dinler, stories.js bu dosyayı
// TANIMAZ (gevşek bağlama); (2) profildeki highlight çubuğu + görüntüleyici.
// Aynı dosya hem feed.html hem profile.html'de yüklenir, her parça kendi
// DOM elemanlarının varlığını kontrol eder (diğer sayfada elemanlar yoktur).

(function () {
    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    // ============================================================
    // --- Öne çıkanlara ekleme picker'ı (sadece feed.html'de var) ---
    // ============================================================
    var pickerModal = document.getElementById('highlight-picker-modal');
    var pickerList = document.getElementById('highlight-picker-list');
    var pickerClose = document.getElementById('highlight-picker-close');
    var newTitleInput = document.getElementById('highlight-new-title-input');
    var createBtn = document.getElementById('highlight-create-btn');
    var storiesBar = document.getElementById('stories-bar');

    var pendingStoryId = null;
    var pickerLastFocused = null;

    function renderPickerList(highlights) {
        pickerList.innerHTML = '';
        if (!highlights.length) {
            pickerList.innerHTML = '<p class="muted">Henüz öne çıkanın yok, aşağıdan yeni oluştur.</p>';
            return;
        }
        highlights.forEach(function (h) {
            var row = document.createElement('button');
            row.type = 'button';
            row.className = 'highlight-picker-row';
            row.innerHTML = (h.cover_url
                ? '<img src="' + h.cover_url + '" class="highlight-cover small" alt="">'
                : '<div class="highlight-cover small avatar-placeholder" aria-hidden="true"></div>') +
                '<span>' + h.title + ' <span class="muted">(' + h.item_count + ')</span></span>';
            row.addEventListener('click', function () { saveToHighlight({ highlight_id: h.id }); });
            pickerList.appendChild(row);
        });
    }

    async function loadHighlightList() {
        if (!pickerList) return;
        var myUserId = storiesBar ? storiesBar.dataset.myUserId : '';
        if (!myUserId) { pickerList.innerHTML = ''; return; }
        pickerList.innerHTML = '<p class="muted">Yükleniyor...</p>';
        try {
            var res = await fetch('/stories/highlights/' + myUserId, { headers: { 'X-Requested-With': 'fetch' } });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var data = await res.json();
            renderPickerList(data.highlights || []);
        } catch (err) {
            console.error('Öne çıkanlar yüklenemedi:', err);
            pickerList.innerHTML = '<p class="muted">Öne çıkanlar yüklenemedi.</p>';
        }
    }

    function openPicker(storyId) {
        if (!pickerModal) return;
        pendingStoryId = storyId;
        pickerLastFocused = document.activeElement;
        if (newTitleInput) newTitleInput.value = '';
        pickerModal.hidden = false;
        document.body.style.overflow = 'hidden';
        loadHighlightList();
    }

    function closePicker() {
        if (!pickerModal) return;
        pickerModal.hidden = true;
        document.body.style.overflow = '';
        pendingStoryId = null;
        if (pickerLastFocused) pickerLastFocused.focus();
    }

    async function saveToHighlight(body) {
        if (!pendingStoryId) return;
        try {
            var res = await fetch('/stories/' + pendingStoryId + '/save-highlight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                body: JSON.stringify(body)
            });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok || !data.ok) throw new Error(data.error || 'İstek başarısız: ' + res.status);
            closePicker();
            // Basit ayarlar akışı, aşırı mühendislik gerekmez — groupChat.js'teki
            // alert(err.message) hata deseniyle tutarlı bir başarı göstergesi.
            alert('Öne çıkanlara eklendi.');
        } catch (err) {
            alert(err.message);
        }
    }

    // stories.js bu event'i fırlatır (openViewer içindeki "⭐ Öne çıkar" butonu).
    document.addEventListener('open-highlight-picker', function (e) {
        openPicker(e.detail.storyId);
    });

    if (pickerClose) pickerClose.addEventListener('click', closePicker);
    if (pickerModal) {
        pickerModal.addEventListener('click', function (e) {
            if (e.target === pickerModal) closePicker();
        });
    }
    if (createBtn) {
        createBtn.addEventListener('click', function () {
            var title = newTitleInput ? newTitleInput.value.trim() : '';
            if (!title) { alert('Bir başlık gir.'); return; }
            saveToHighlight({ new_title: title });
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && pickerModal && !pickerModal.hidden) closePicker();
    });

    // ============================================================
    // --- Profildeki highlight çubuğu + görüntüleyici (sadece profile.html'de var) ---
    // ============================================================
    var highlightBar = document.querySelector('.highlight-bar');
    var hlViewerModal = document.getElementById('highlight-viewer-modal');
    var hlViewerTitle = document.getElementById('highlight-viewer-title');
    var hlViewerClose = document.getElementById('highlight-viewer-close');
    var hlViewerImage = document.getElementById('highlight-viewer-image');
    var hlViewerVideo = document.getElementById('highlight-viewer-video');
    var hlViewerCaption = document.getElementById('highlight-viewer-caption');
    var hlDeleteBtn = document.getElementById('highlight-delete-btn');
    var hlNavPrev = document.getElementById('highlight-nav-prev');
    var hlNavNext = document.getElementById('highlight-nav-next');

    var currentItems = [];
    var currentIndex = 0;
    var currentHighlightId = null;
    var hlLastFocused = null;

    function showItem(index) {
        if (index < 0 || index >= currentItems.length) return;
        currentIndex = index;
        var it = currentItems[index];

        hlViewerVideo.pause();
        hlViewerVideo.hidden = true;
        hlViewerVideo.removeAttribute('src');
        hlViewerImage.hidden = true;
        hlViewerImage.removeAttribute('src');
        hlViewerCaption.hidden = !it.caption;
        hlViewerCaption.textContent = it.caption || '';

        if (it.video_url) {
            hlViewerVideo.src = it.video_url;
            hlViewerVideo.hidden = false;
        } else {
            hlViewerImage.src = it.image_url || '';
            hlViewerImage.hidden = false;
        }
    }

    async function openHighlightViewer(highlightId) {
        try {
            var res = await fetch('/stories/highlights/' + highlightId + '/view', { headers: { 'X-Requested-With': 'fetch' } });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var data = await res.json();
            if (!data.items || !data.items.length) return;

            hlLastFocused = document.activeElement;
            currentHighlightId = highlightId;
            currentItems = data.items;
            hlViewerTitle.textContent = data.title || '';
            hlDeleteBtn.hidden = !data.is_mine;

            hlViewerModal.hidden = false;
            document.body.style.overflow = 'hidden';
            hlViewerClose.focus();
            showItem(0);
        } catch (err) {
            console.error('Öne çıkan yüklenemedi:', err);
        }
    }

    function closeHighlightViewer() {
        hlViewerVideo.pause();
        hlViewerModal.hidden = true;
        document.body.style.overflow = '';
        currentItems = [];
        currentHighlightId = null;
        if (hlLastFocused) hlLastFocused.focus();
    }

    if (highlightBar) {
        highlightBar.querySelectorAll('.highlight-item[data-highlight-id]').forEach(function (btn) {
            btn.addEventListener('click', function () { openHighlightViewer(btn.dataset.highlightId); });
        });
    }

    if (hlNavPrev) hlNavPrev.addEventListener('click', function () { showItem(currentIndex - 1); });
    if (hlNavNext) hlNavNext.addEventListener('click', function () { showItem(currentIndex + 1); });
    if (hlViewerClose) hlViewerClose.addEventListener('click', closeHighlightViewer);
    if (hlViewerModal) {
        hlViewerModal.addEventListener('click', function (e) {
            if (e.target === hlViewerModal) closeHighlightViewer();
        });
    }
    document.addEventListener('keydown', function (e) {
        if (!hlViewerModal || hlViewerModal.hidden) return;
        if (e.key === 'Escape') { closeHighlightViewer(); return; }
        if (e.key === 'ArrowRight') showItem(currentIndex + 1);
        else if (e.key === 'ArrowLeft') showItem(currentIndex - 1);
    });

    if (hlDeleteBtn) {
        hlDeleteBtn.addEventListener('click', async function () {
            if (!currentHighlightId) return;
            if (!await window.appConfirm('Bu öne çıkanı silmek istiyor musun?')) return;
            try {
                var res = await fetch('/stories/highlights/' + currentHighlightId + '/delete', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfToken() },
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                closeHighlightViewer();
                // groupChat.js/stories.js'teki "karmaşık optimistic UI gerekmez"
                // tercihiyle tutarlı: en basit ve güvenilir yol sayfa yenileme.
                window.location.reload();
            } catch (err) {
                console.error('Öne çıkan silinemedi:', err);
                alert('Silinemedi, tekrar dene.');
            }
        });
    }
})();
