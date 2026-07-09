// Hikaye: ekleme modalı (görsel/video önizleme, mutual-exclusive — post'un
// aksine hikaye TEK medyalı, bkz. app/stories.py create_story()) + görüntüleyici
// (Instagram tarzı: ilerleme çubukları, sol/sağ tıklama ile önceki/sonraki,
// görseller 5sn sonra otomatik ilerler, videolar bitince ilerler).

(function () {
    var IMAGE_DURATION_MS = 5000;

    function csrfHeader() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    // ============================================================
    // --- Hikaye ekleme modalı ---
    // ============================================================
    var storyModal = document.getElementById('story-modal');
    var addStoryBtn = document.getElementById('add-story-btn');
    var closeStoryModalBtn = document.getElementById('close-story-modal');
    var storyImageInput = document.getElementById('story-image-input');
    var storyImagePreview = document.getElementById('story-image-preview');
    var storyVideoInput = document.getElementById('story-video-input');
    var storyVideoPreview = document.getElementById('story-video-preview');

    function openStoryModal() {
        if (!storyModal) return;
        storyModal.hidden = false;
        document.body.style.overflow = 'hidden';
        var ta = storyModal.querySelector('textarea');
        if (ta) setTimeout(function () { ta.focus(); }, 50);
    }

    function closeStoryModal() {
        if (!storyModal) return;
        storyModal.hidden = true;
        document.body.style.overflow = '';
        var form = storyModal.querySelector('form');
        if (form) form.reset();
        if (storyImagePreview) storyImagePreview.innerHTML = '';
        if (storyVideoPreview) { storyVideoPreview.style.display = 'none'; storyVideoPreview.removeAttribute('src'); }
    }

    if (addStoryBtn) addStoryBtn.addEventListener('click', openStoryModal);
    if (closeStoryModalBtn) closeStoryModalBtn.addEventListener('click', closeStoryModal);
    if (storyModal) {
        storyModal.addEventListener('click', function (e) {
            if (e.target === storyModal) closeStoryModal();
        });
    }

    // Görsel/video mutual-exclusive: hikaye tek medyalı, biri seçilince diğeri temizlenir.
    if (storyImageInput && storyImagePreview) {
        storyImageInput.addEventListener('change', function (e) {
            storyImagePreview.innerHTML = '';
            var file = e.target.files[0];
            if (!file) return;
            if (storyVideoInput) storyVideoInput.value = '';
            if (storyVideoPreview) { storyVideoPreview.style.display = 'none'; storyVideoPreview.removeAttribute('src'); }
            var reader = new FileReader();
            reader.onload = function (ev) {
                var wrap = document.createElement('div');
                wrap.className = 'image-preview-item';
                wrap.innerHTML = '<img src="' + ev.target.result + '" alt="Önizleme">';
                storyImagePreview.appendChild(wrap);
            };
            reader.readAsDataURL(file);
        });
    }
    if (storyVideoInput && storyVideoPreview) {
        storyVideoInput.addEventListener('change', function (e) {
            var file = e.target.files[0];
            if (!file) { storyVideoPreview.style.display = 'none'; storyVideoPreview.removeAttribute('src'); return; }
            if (storyImageInput) storyImageInput.value = '';
            if (storyImagePreview) storyImagePreview.innerHTML = '';
            storyVideoPreview.src = URL.createObjectURL(file);
            storyVideoPreview.style.display = 'block';
        });
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && storyModal && !storyModal.hidden) closeStoryModal();
    });

    // ============================================================
    // --- Hikaye görüntüleyici ---
    // ============================================================
    var viewerModal = document.getElementById('story-viewer-modal');
    var progressRow = document.getElementById('story-progress-row');
    var viewerAvatar = document.getElementById('story-viewer-avatar');
    var viewerUsername = document.getElementById('story-viewer-username');
    var viewerTime = document.getElementById('story-viewer-time');
    var highlightBtn = document.getElementById('story-highlight-btn');
    var deleteBtn = document.getElementById('story-delete-btn');
    var viewerCloseBtn = document.getElementById('story-viewer-close');
    var viewerImage = document.getElementById('story-viewer-image');
    var viewerVideo = document.getElementById('story-viewer-video');
    var viewerCaption = document.getElementById('story-viewer-caption');
    var navPrev = document.getElementById('story-nav-prev');
    var navNext = document.getElementById('story-nav-next');

    var currentStories = [];
    var currentIndex = 0;
    var progressTimer = null;
    var lastFocused = null;
    var isPaused = false;
    var storyStartTime = null;   // aktif oynatma diliminin başlangıcı
    var elapsedPlayed = 0;       // bu hikayede toplam OYNATILMIŞ süre (duraklamalar hariç)

    function timeAgo(iso) {
        var diffMs = Date.now() - new Date(iso).getTime();
        var mins = Math.floor(diffMs / 60000);
        if (mins < 1) return 'şimdi';
        if (mins < 60) return mins + 'dk';
        var hours = Math.floor(mins / 60);
        if (hours < 24) return hours + 'sa';
        return Math.floor(hours / 24) + 'g';
    }

    function buildProgressBars() {
        progressRow.innerHTML = '';
        currentStories.forEach(function () {
            var seg = document.createElement('div');
            seg.className = 'story-progress-seg';
            seg.innerHTML = '<div class="story-progress-fill"></div>';
            progressRow.appendChild(seg);
        });
    }

    function clearTimer() {
        if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
    }

    // Aktif hikayenin progress fill'i (document.querySelector hep İLK segmenti bulur)
    function currentFill() {
        var segs = progressRow.querySelectorAll('.story-progress-seg');
        return segs[currentIndex] ? segs[currentIndex].querySelector('.story-progress-fill') : null;
    }

    function setPaused(paused) {
        isPaused = paused;
        var indicator = document.getElementById('story-paused-indicator');
        var fill = currentFill();
        var isVideo = !!(currentStories[currentIndex] && currentStories[currentIndex].video_url);
        if (paused) {
            clearTimer();
            if (!isVideo) {
                if (storyStartTime) elapsedPlayed += Date.now() - storyStartTime;
                // Bar CSS transition ile akıyor; class'la DONDURULAMAZ
                // (animation-play-state sadece animation'a işler) — mevcut
                // genişliği hesaplayıp transition'ı kapatarak sabitle
                if (fill) {
                    var pct = Math.min(100, (elapsedPlayed / IMAGE_DURATION_MS) * 100);
                    fill.style.transition = 'none';
                    fill.style.width = pct + '%';
                }
            }
            if (viewerVideo && !viewerVideo.hidden) viewerVideo.pause();
            if (indicator) indicator.hidden = false;
        } else {
            if (indicator) indicator.hidden = true;
            if (isVideo) {
                // Video kendi kaldığı yerden oynar, onended ile ilerler
                if (viewerVideo && !viewerVideo.hidden) viewerVideo.play().catch(function () { });
                return;
            }
            storyStartTime = Date.now();
            var remaining = Math.max(0, IMAGE_DURATION_MS - elapsedPlayed);
            if (fill) {
                requestAnimationFrame(function () {
                    fill.style.transition = 'width ' + remaining + 'ms linear';
                    fill.style.width = '100%';
                });
            }
            progressTimer = setTimeout(function () { showStory(currentIndex + 1); }, remaining);
        }
    }

    function showStory(index) {
        clearTimer();
        isPaused = false;
        elapsedPlayed = 0;
        var oldIndicator = document.getElementById('story-paused-indicator');
        if (oldIndicator) oldIndicator.hidden = true;
        if (index < 0) return; // ilk hikayede geri gidilemez
        if (index >= currentStories.length) { closeViewer(); return; }
        currentIndex = index;
        var s = currentStories[index];
        storyStartTime = Date.now();

        // Buton görünürlüğü: ilk hikayede prev gizli, son hikayede next gizli
        var storyPrevBtn = document.getElementById('story-prev-btn');
        var storyNextBtn = document.getElementById('story-next-btn');
        if (storyPrevBtn) storyPrevBtn.hidden = (index === 0);
        if (storyNextBtn) storyNextBtn.hidden = (index === currentStories.length - 1);

        var segs = progressRow.querySelectorAll('.story-progress-seg');
        segs.forEach(function (seg, i) {
            var fill = seg.querySelector('.story-progress-fill');
            fill.style.transition = 'none';
            fill.style.width = i < index ? '100%' : '0%';
        });

        viewerTime.textContent = timeAgo(s.created_at);
        viewerCaption.hidden = !s.caption;
        viewerCaption.textContent = s.caption || '';

        viewerVideo.pause();
        viewerVideo.hidden = true;
        viewerVideo.removeAttribute('src');
        viewerImage.hidden = true;
        viewerImage.removeAttribute('src');

        if (s.video_url) {
            viewerVideo.src = s.video_url;
            viewerVideo.hidden = false;
            viewerVideo.currentTime = 0;
            viewerVideo.play().catch(function () { /* autoplay engellenmiş olabilir, tıklayınca oynar */ });
            viewerVideo.onended = function () { showStory(currentIndex + 1); };
        } else {
            viewerImage.src = s.image_url || '';
            viewerImage.hidden = false;
            var fill = segs[index].querySelector('.story-progress-fill');
            requestAnimationFrame(function () {
                fill.style.transition = 'width ' + IMAGE_DURATION_MS + 'ms linear';
                fill.style.width = '100%';
            });
            progressTimer = setTimeout(function () { showStory(currentIndex + 1); }, IMAGE_DURATION_MS);
        }
    }

    async function openViewer(userId) {
        try {
            var res = await fetch('/stories/user/' + userId, { headers: { 'X-Requested-With': 'fetch' } });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var data = await res.json();
            if (!data.stories.length) return;

            lastFocused = document.activeElement;
            currentStories = data.stories;
            viewerUsername.textContent = data.is_mine ? 'Sen' : data.username;
            if (data.avatar_url) { viewerAvatar.src = data.avatar_url; viewerAvatar.hidden = false; }
            else { viewerAvatar.hidden = true; }
            deleteBtn.hidden = !data.is_mine;
            highlightBtn.hidden = !data.is_mine;

            buildProgressBars();
            viewerModal.hidden = false;
            document.body.style.overflow = 'hidden';
            viewerCloseBtn.focus();
            showStory(0);

            // Halka rengi: bu kullanıcının hikayesi artık görüldü sayılır (sunucu
            // zaten /stories/user/<id> çağrısında story_views'e işledi) — client'ta
            // da anında güncelle, sonraki feed yenilemesini beklemeden.
            var btn = document.querySelector('.story-avatar-btn[data-user-id="' + userId + '"]');
            if (btn) { btn.classList.remove('story-unseen'); btn.classList.add('story-seen'); }
        } catch (err) {
            console.error('Hikaye yüklenemedi:', err);
        }
    }

    function closeViewer() {
        clearTimer();
        isPaused = false;
        elapsedPlayed = 0;
        storyStartTime = null;
        viewerVideo.pause();
        viewerModal.hidden = true;
        document.body.style.overflow = '';
        currentStories = [];
        var indicator = document.getElementById('story-paused-indicator');
        if (indicator) indicator.hidden = true;
        if (lastFocused) lastFocused.focus();
    }

    document.querySelectorAll('.story-avatar-btn[data-user-id]').forEach(function (btn) {
        btn.addEventListener('click', function () { openViewer(btn.dataset.userId); });
    });

    if (viewerCloseBtn) viewerCloseBtn.addEventListener('click', closeViewer);
    if (navPrev) navPrev.addEventListener('click', function () { showStory(currentIndex - 1); });
    if (navNext) navNext.addEventListener('click', function () { showStory(currentIndex + 1); });

    // Buton event'leri (görünür yön butonları)
    var storyPrevBtn = document.getElementById('story-prev-btn');
    var storyNextBtn = document.getElementById('story-next-btn');
    if (storyPrevBtn) storyPrevBtn.addEventListener('click', function () { showStory(currentIndex - 1); });
    if (storyNextBtn) storyNextBtn.addEventListener('click', function () { showStory(currentIndex + 1); });

    // Medya alanı tıklama — duraklat/devam
    if (viewerModal) {
        viewerModal.addEventListener('click', function (e) {
            if (e.target === viewerModal) { closeViewer(); return; }
            // Medya alanı veya onun alt öğelerine tıklandı mı kontrol et
            var mediaArea = document.getElementById('story-media-area');
            if (mediaArea && (e.target === mediaArea || mediaArea.contains(e.target))) {
                // Buton tıklandıysa atla
                if (e.target.classList && (e.target.classList.contains('story-nav-btn') ||
                    e.target.classList.contains('story-nav-zone'))) return;
                // Duraklat/devam
                setPaused(!isPaused);
            }
        });
    }

    // storyHighlights.js bu event'i dinler — stories.js picker'ın kendisini
    // BİLMEMELİ (gevşek bağlama), sadece hangi hikayenin öne çıkarılacağını fırlatır.
    if (highlightBtn) {
        highlightBtn.addEventListener('click', function () {
            if (!currentStories.length) return;
            document.dispatchEvent(new CustomEvent('open-highlight-picker', {
                detail: { storyId: currentStories[currentIndex].id }
            }));
        });
    }

    if (deleteBtn) {
        deleteBtn.addEventListener('click', async function () {
            if (!currentStories.length) return;
            if (!await window.appConfirm('Bu hikayeyi silmek istiyor musun?')) return;
            var storyId = currentStories[currentIndex].id;
            try {
                var res = await fetch('/stories/' + storyId + '/delete', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfHeader() },
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                currentStories.splice(currentIndex, 1);
                var btn = document.querySelector('.story-avatar-btn[data-user-id]');
                if (!currentStories.length) {
                    closeViewer();
                    // Kendi hikayen kalmadıysa çubuktan tamamen kaldırılması için
                    // en basit ve güvenilir yol: sayfayı yenile.
                    window.location.reload();
                    return;
                }
                buildProgressBars();
                showStory(Math.min(currentIndex, currentStories.length - 1));
            } catch (err) {
                console.error('Hikaye silinemedi:', err);
            }
        });
    }

    document.addEventListener('keydown', function (e) {
        if (!viewerModal || viewerModal.hidden) return;
        if (e.key === 'Escape') { closeViewer(); return; }
        if (e.key === 'ArrowRight') showStory(currentIndex + 1);
        else if (e.key === 'ArrowLeft') showStory(currentIndex - 1);
    });

    if (viewerModal) {
        viewerModal.addEventListener('click', function (e) {
            if (e.target === viewerModal) closeViewer();
        });
    }
})();
