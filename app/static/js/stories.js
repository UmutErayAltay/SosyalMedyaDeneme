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
    var storyPollToggleBtn = document.getElementById('story-poll-toggle-btn');
    var storyPollAddOptionBtn = document.getElementById('story-poll-add-option-btn');
    var storyPollCancelBtn = document.getElementById('story-poll-cancel-btn');
    var storyPollContainer = document.getElementById('story-poll-options-container');

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
        if (storyPollContainer) storyPollContainer.hidden = true;
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

    // Hikaye altyazısı: içeriğe göre otomatik büyür/küçülür — comments.js/chat.js
    // ile AYNI desen (manuel resize tutamacı yerine, kullanıcı isteği).
    document.addEventListener('input', function (e) {
        if (e.target.id !== 'story-caption-input') return;
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
    });

    // Anket oluşturma modalında widget'ı sürüklenebilir ve boyutlandırılabilir yap
    var storyPollPreviewWidget = document.getElementById('story-poll-preview-widget');
    var storyMediaPreviewWrap = storyPollPreviewWidget ? storyPollPreviewWidget.closest('.story-media-preview-wrap') : null;
    var storyPollScaleSlider = document.getElementById('story-poll-scale-slider');
    var storyPollScaleDisplay = document.getElementById('story-poll-scale-display');
    var storyPollScaleControl = document.getElementById('story-poll-scale-control');
    var storyPollDragState = { dragging: false, startX: 0, startY: 0, offsetX: 0, offsetY: 0 };

    function initStoryPollWidget() {
        if (!storyPollPreviewWidget) return;
        // Widget başlangıç: konteyner center'ında
        storyPollPreviewWidget.style.left = '50%';
        storyPollPreviewWidget.style.top = '50%';
        storyPollPreviewWidget.style.transform = 'translate(-50%, -50%) scale(1)';
        storyPollPreviewWidget.dataset.positionX = '0.5';
        storyPollPreviewWidget.dataset.positionY = '0.5';
        storyPollPreviewWidget.dataset.scale = '1';
        if (storyPollScaleSlider) storyPollScaleSlider.value = '1';
        if (storyPollScaleDisplay) storyPollScaleDisplay.textContent = '100%';
    }

    // Anket boyutu slider'ı
    if (storyPollScaleSlider) {
        storyPollScaleSlider.addEventListener('input', function (e) {
            var scale = parseFloat(e.target.value);
            if (storyPollPreviewWidget) {
                storyPollPreviewWidget.style.transform = 'translate(-50%, -50%) scale(' + scale + ')';
                storyPollPreviewWidget.dataset.scale = scale.toFixed(2);
                var pollScaleInput = storyPollContainer ? storyPollContainer.querySelector('input[name="poll_scale"]') : null;
                if (pollScaleInput) pollScaleInput.value = scale.toFixed(2);
            }
            if (storyPollScaleDisplay) {
                storyPollScaleDisplay.textContent = Math.round(scale * 100) + '%';
            }
        });
    }

    function updateStoryPollPreview(options) {
        if (!storyPollPreviewWidget) return;
        var previewHtml = '<div style="background:rgba(0,0,0,0.7);color:#fff;padding:8px 12px;border-radius:8px;font-size:12px;max-width:200px;">';
        previewHtml += '<strong>Anket:</strong><br>';
        (options || []).slice(0, 2).forEach(function (text) {
            if (text.trim()) previewHtml += '• ' + text + '<br>';
        });
        previewHtml += '</div>';
        storyPollPreviewWidget.innerHTML = previewHtml;
    }

    // Poll widget sürükleme: Pointer Events (mouse + touch)
    if (storyPollPreviewWidget) {
        storyPollPreviewWidget.addEventListener('pointerdown', function (e) {
            if (!storyMediaPreviewWrap) return;
            e.preventDefault();
            storyPollDragState.dragging = true;
            storyPollDragState.startX = e.clientX;
            storyPollDragState.startY = e.clientY;
            var rect = storyPollPreviewWidget.getBoundingClientRect();
            storyPollDragState.offsetX = rect.left - storyMediaPreviewWrap.getBoundingClientRect().left;
            storyPollDragState.offsetY = rect.top - storyMediaPreviewWrap.getBoundingClientRect().top;
        });
    }

    document.addEventListener('pointermove', function (e) {
        if (!storyPollDragState.dragging || !storyMediaPreviewWrap || !storyPollPreviewWidget) return;
        var deltaX = e.clientX - storyPollDragState.startX;
        var deltaY = e.clientY - storyPollDragState.startY;
        var newX = storyPollDragState.offsetX + deltaX;
        var newY = storyPollDragState.offsetY + deltaY;

        // Sınır kontrolü: konteyner içinde kal
        var containerRect = storyMediaPreviewWrap.getBoundingClientRect();
        var widgetRect = storyPollPreviewWidget.getBoundingClientRect();
        var maxX = containerRect.width - (widgetRect.width || 150);
        var maxY = containerRect.height - (widgetRect.height || 60);
        newX = Math.max(0, Math.min(newX, maxX));
        newY = Math.max(0, Math.min(newY, maxY));

        storyPollPreviewWidget.style.left = newX + 'px';
        storyPollPreviewWidget.style.top = newY + 'px';
        storyPollPreviewWidget.style.transform = 'none';
    });

    document.addEventListener('pointerup', function (e) {
        if (!storyPollDragState.dragging || !storyMediaPreviewWrap) return;
        storyPollDragState.dragging = false;

        // Konumu 0-1 arası oran'a çevir ve gizli input'lara yaz
        var containerRect = storyMediaPreviewWrap.getBoundingClientRect();
        var widgetRect = storyPollPreviewWidget.getBoundingClientRect();
        var centerX = widgetRect.left - containerRect.left + (widgetRect.width || 150) / 2;
        var centerY = widgetRect.top - containerRect.top + (widgetRect.height || 60) / 2;
        var posX = Math.max(0, Math.min(1, centerX / containerRect.width));
        var posY = Math.max(0, Math.min(1, centerY / containerRect.height));

        storyPollPreviewWidget.dataset.positionX = posX.toFixed(2);
        storyPollPreviewWidget.dataset.positionY = posY.toFixed(2);

        // Gizli input'lara yaz
        var pollPosXInput = storyPollContainer ? storyPollContainer.querySelector('input[name="poll_position_x"]') : null;
        var pollPosYInput = storyPollContainer ? storyPollContainer.querySelector('input[name="poll_position_y"]') : null;
        if (pollPosXInput) pollPosXInput.value = posX.toFixed(2);
        if (pollPosYInput) pollPosYInput.value = posY.toFixed(2);
    });

    // Hikaye formu: anket toggle ve seçenek ekleme
    if (storyPollToggleBtn) {
        storyPollToggleBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (storyPollContainer) {
                var wasHidden = storyPollContainer.hidden;
                storyPollContainer.hidden = !wasHidden;
                var nowOpen = wasHidden; // wasHidden=true -> az önce açıldı (görünür oldu)
                if (nowOpen) {
                    // Görsel/video ile anket ARTIK birlikte var olabilir (kullanıcı
                    // isteği) — önceden burada image/video input'u temizleniyordu,
                    // "görsel eklendikten sonra anket ekleyince görsel kayboluyor"
                    // hatasına yol açıyordu. Poll widget'ı göster + init et
                    if (storyPollPreviewWidget) {
                        storyPollPreviewWidget.hidden = false;
                        initStoryPollWidget();
                        updateStoryPollPreview(['Seçenek 1', 'Seçenek 2']);
                    }
                    if (storyPollScaleControl) storyPollScaleControl.hidden = false;
                } else {
                    // Poll'u kapat: widget'ı gizle
                    if (storyPollPreviewWidget) {
                        storyPollPreviewWidget.hidden = true;
                    }
                    if (storyPollScaleControl) storyPollScaleControl.hidden = true;
                }
            }
        });
    }

    if (storyPollAddOptionBtn) {
        storyPollAddOptionBtn.addEventListener('click', function (e) {
            e.preventDefault();
            var hidden3 = storyPollContainer.querySelector('input[name="poll_option_3"]');
            var hidden4 = storyPollContainer.querySelector('input[name="poll_option_4"]');
            if (hidden3 && hidden3.hidden) {
                hidden3.hidden = false;
                return;
            }
            if (hidden4 && hidden4.hidden) {
                hidden4.hidden = false;
            }
        });
    }

    // Anket seçenekleri değiştiğinde widget'ı güncelle
    if (storyPollContainer) {
        var pollInputs = storyPollContainer.querySelectorAll('input[type="text"][name^="poll_option_"]');
        pollInputs.forEach(function (inp) {
            inp.addEventListener('input', function () {
                var options = [];
                pollInputs.forEach(function (i) {
                    if (i.value.trim()) options.push(i.value.trim());
                });
                if (options.length >= 2) updateStoryPollPreview(options);
            });
        });
    }

    if (storyPollCancelBtn) {
        storyPollCancelBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (storyPollContainer) {
                storyPollContainer.hidden = true;
                if (storyPollContainer.querySelector('input[name="poll_option_3"]')) {
                    storyPollContainer.querySelector('input[name="poll_option_3"]').hidden = true;
                }
                if (storyPollContainer.querySelector('input[name="poll_option_4"]')) {
                    storyPollContainer.querySelector('input[name="poll_option_4"]').hidden = true;
                }
                var inputs = storyPollContainer.querySelectorAll('input[type="text"]');
                inputs.forEach(function (inp) { inp.value = ''; });
            }
        });
    }

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
    var replyForm = document.getElementById('story-reply-form');
    var replyInput = document.getElementById('story-reply-input');
    var replySendBtn = replyForm ? replyForm.querySelector('.story-reply-send-btn') : null;
    var replyAutoPaused = false;
    var reactionsBar = document.getElementById('story-reactions-bar');
    var pollWidget = document.getElementById('story-poll-widget');
    var pollContainer = document.getElementById('story-poll-container');

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

    function renderStoryPoll(poll) {
        if (!pollContainer) return;
        pollContainer.innerHTML = '';
        var widget = document.createElement('div');
        widget.className = 'poll-widget';

        // DOM API ile inşa edilir (innerHTML string birleştirme DEĞİL) — opt.text
        // kullanıcı girdisi (anket seçeneği), innerHTML'e ham gömmek XSS riski taşır.
        poll.options.forEach(function (opt) {
            var optBtn = document.createElement('button');
            optBtn.className = 'poll-option' + (poll.my_vote === opt.id ? ' voted' : '');
            optBtn.dataset.optionId = opt.id;
            optBtn.dataset.pollId = poll.id;
            optBtn.setAttribute('aria-label', opt.text + ', yüzde ' + opt.pct + ', ' + opt.votes + ' oy');

            var textSpan = document.createElement('span');
            textSpan.className = 'poll-option-text';
            textSpan.textContent = opt.text;

            var bar = document.createElement('div');
            bar.className = 'poll-option-bar';
            bar.style.width = opt.pct + '%';

            var pctSpan = document.createElement('span');
            pctSpan.className = 'poll-option-pct';
            pctSpan.textContent = opt.pct + '%';

            optBtn.appendChild(textSpan);
            optBtn.appendChild(bar);
            optBtn.appendChild(pctSpan);
            widget.appendChild(optBtn);
        });

        var totalDiv = document.createElement('div');
        totalDiv.className = 'poll-total';
        totalDiv.textContent = poll.total_votes + ' oy';
        widget.appendChild(totalDiv);

        pollContainer.appendChild(widget);

        // Anket widget'ını konumlandır (poll.position_x, position_y, scale)
        if (pollWidget) {
            var posX = (poll.position_x !== undefined) ? poll.position_x : 0.5;
            var posY = (poll.position_y !== undefined) ? poll.position_y : 0.5;
            var scale = (poll.scale !== undefined) ? poll.scale : 1;

            pollWidget.style.left = (posX * 100) + '%';
            pollWidget.style.top = (posY * 100) + '%';
            pollWidget.style.transform = 'translate(-50%, -50%) scale(' + scale + ')';
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

        // Anket render'ı (varsa)
        if (pollWidget && pollContainer) {
            if (s.poll) {
                renderStoryPoll(s.poll);
                pollWidget.hidden = false;
            } else {
                pollContainer.innerHTML = '';
                pollWidget.hidden = true;
            }
        }

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
            // Kendi hikayene yanıt/tepki verilemez (Instagram deseni)
            if (replyForm) {
                replyForm.hidden = data.is_mine;
                if (replyInput) replyInput.value = '';
            }
            if (reactionsBar) {
                reactionsBar.hidden = data.is_mine;
            }

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
        if (pollContainer) pollContainer.innerHTML = '';
        if (pollWidget) pollWidget.hidden = true;
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

    // Emoji tepkisi butonları — tıklanınca /react POST et + yanıt input'unda geri bildirim
    if (reactionsBar) {
        // KRİTİK: mousedown'da preventDefault() — aksi halde tarayıcı odağı
        // butona kaydırır, bu da #story-reply-input'un blur handler'ını
        // SENKRON tetikler (reactionsBar'dan .visible kalkar, pointer-events:
        // none ANINDA uygulanır — transition'ı beklemez). Sonuç: mousedown
        // buton üzerinde başlasa da mouseup, artık tıklama-geçirmez hale gelen
        // şeridin ALTINDAKİ elemana (hikaye önceki/sonraki nav-zone'u) düşüyor,
        // click hiç ateşlenmiyor, tepki gönderilmiyordu. preventDefault ile
        // buton hiç fokus almıyor, input fokusu/şerit görünürlüğü korunuyor.
        reactionsBar.addEventListener('mousedown', function (e) {
            if (e.target.closest('.story-reaction-btn')) e.preventDefault();
        });
        reactionsBar.addEventListener('click', async function (e) {
            var btn = e.target.closest('.story-reaction-btn');
            if (!btn) return;
            e.preventDefault();
            if (btn.dataset.busy === '1') return;
            btn.dataset.busy = '1';

            var emoji = btn.dataset.emoji;
            var storyId = currentStories[currentIndex] ? currentStories[currentIndex].id : null;
            if (!storyId) {
                btn.dataset.busy = '0';
                return;
            }

            try {
                var formData = new FormData();
                formData.append('emoji', emoji);
                formData.append('csrf_token', csrfHeader());

                var res = await fetch('/stories/' + storyId + '/react', {
                    method: 'POST', body: formData,
                    headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfHeader() },
                });
                if (!res.ok) throw new Error('İstek başarısız');

                // Görsel onay: buton patlama + input placeholder feedback + şeridi kapat
                btn.style.transform = 'scale(1.3)';
                setTimeout(function () { btn.style.transform = 'scale(1)'; }, 200);

                // Yanıt input'unda "Gönderildi ✓" onayı
                if (replyInput) {
                    var originalPlaceholder = replyInput.placeholder;
                    replyInput.placeholder = 'Gönderildi ✓';
                    replyInput.blur(); // Emoji şeridini kapat
                    setTimeout(function () { replyInput.placeholder = originalPlaceholder; }, 2000);
                }
            } catch (err) {
                console.error('Tepki gönderilemedi:', err);
            } finally {
                btn.dataset.busy = '0';
            }
        });
    }

    // Anket oy verme — document-level delegation (AJAX'ta yenilenen anket için)
    document.addEventListener('click', async function (e) {
        var btn = e.target.closest('.poll-option[data-poll-id][data-option-id]');
        if (!btn || !reactionsBar || reactionsBar.hidden) return;  // Sadece hikaye görüntüleyicide
        e.preventDefault();
        if (btn.dataset.busy === '1') return;
        btn.dataset.busy = '1';

        var pollId = btn.dataset.pollId;
        var optionId = btn.dataset.optionId;
        var voteUrl = '/poll/' + pollId + '/vote';

        try {
            var formData = new FormData();
            formData.append('option_id', optionId);
            formData.append('csrf_token', csrfHeader());

            var res = await fetch(voteUrl, {
                method: 'POST', body: formData,
                headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfHeader() },
            });
            if (!res.ok) throw new Error('İstek başarısız');
            var data = await res.json();

            var widget = pollContainer.querySelector('.poll-widget');
            if (widget) {
                var totalEl = widget.querySelector('.poll-total');
                if (totalEl) totalEl.textContent = data.total_votes + ' oy';

                data.options.forEach(function (opt) {
                    var optBtn = widget.querySelector('[data-option-id="' + opt.id + '"]');
                    if (!optBtn) return;
                    optBtn.classList.toggle('voted', data.my_vote === opt.id);
                    var bar = optBtn.querySelector('.poll-option-bar');
                    if (bar) bar.style.width = opt.pct + '%';
                    var pct = optBtn.querySelector('.poll-option-pct');
                    if (pct) pct.textContent = opt.pct + '%';
                    optBtn.setAttribute('aria-label', opt.text + ', yüzde ' + opt.pct + ', ' + opt.votes + ' oy');
                });
            }
        } catch (err) {
            console.error('Oy kullanılamadı:', err);
        } finally {
            btn.dataset.busy = '0';
        }
    });

    document.addEventListener('keydown', function (e) {
        if (!viewerModal || viewerModal.hidden) return;
        if (e.key === 'Escape') { closeViewer(); return; }
        // Yanıt kutusuna yazarken ok tuşları imleç hareketi için kullanılır —
        // hikaye gezinmesini TETİKLEMESİN (kullanıcı metin içinde gezinemezdi)
        if (document.activeElement === replyInput) return;
        if (e.key === 'ArrowRight') showStory(currentIndex + 1);
        else if (e.key === 'ArrowLeft') showStory(currentIndex - 1);
    });

    // Yanıt kutusuna odaklanınca: hikaye duraklat + emoji şeridini göster
    if (replyInput) {
        replyInput.addEventListener('focus', function () {
            if (!isPaused) { replyAutoPaused = true; setPaused(true); }
            if (reactionsBar) reactionsBar.classList.add('visible');
        });
        replyInput.addEventListener('blur', function () {
            if (replyAutoPaused) { replyAutoPaused = false; setPaused(false); }
            if (reactionsBar) reactionsBar.classList.remove('visible');
        });
    }

    if (replyForm) {
        replyForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var text = (replyInput.value || '').trim();
            if (!text || !currentStories.length) return;
            var storyId = currentStories[currentIndex].id;
            replySendBtn.disabled = true;
            try {
                var res = await fetch('/stories/' + storyId + '/reply', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfHeader(),
                        'X-Requested-With': 'fetch',
                    },
                    body: JSON.stringify({ text: text }),
                });
                var data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Yanıt gönderilemedi.');
                replyInput.value = '';
                var originalPlaceholder = replyInput.placeholder;
                replyInput.placeholder = 'Gönderildi ✓';
                setTimeout(function () { replyInput.placeholder = originalPlaceholder; }, 2000);
            } catch (err) {
                window.appAlert(err.message || 'Yanıt gönderilemedi.');
            } finally {
                replySendBtn.disabled = false;
            }
        });
    }

    if (viewerModal) {
        viewerModal.addEventListener('click', function (e) {
            if (e.target === viewerModal) closeViewer();
        });
    }
})();
