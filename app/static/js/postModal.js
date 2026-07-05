// Post paylaşma modalı — erişilebilir (focus trap, ESC, overlay click)
// Çoklu görsel önizleme

(function () {
    var modal = document.getElementById('post-modal');
    var modalBox = modal ? modal.querySelector('.modal') : null;
    var openBtn = document.getElementById('open-post-modal');
    var closeBtn = document.getElementById('close-post-modal');
    var fileInput = document.getElementById('post-image-input');
    var previewGrid = document.getElementById('post-image-preview');
    var videoInput = document.getElementById('post-video-input');
    var videoPreview = document.getElementById('post-video-preview');
    var pollToggleBtn = document.getElementById('poll-toggle-btn');
    var pollContainer = document.getElementById('poll-options-container');
    var pollAddOptionBtn = document.getElementById('poll-add-option-btn');
    var pollCancelBtn = document.getElementById('poll-cancel-btn');
    if (!modal || !openBtn) return;

    var lastFocused = null;

    function open() {
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        // Focus'u modal içine taşı
        setTimeout(function () {
            var ta = modal.querySelector('textarea');
            if (ta) ta.focus();
        }, 50);
    }

    function close() {
        modal.hidden = true;
        document.body.style.overflow = '';
        if (lastFocused) lastFocused.focus();
        if (pollContainer && !pollContainer.hidden) resetPollUI();
    }

    openBtn.addEventListener('click', open);
    if (closeBtn) closeBtn.addEventListener('click', close);

    // Overlay'e tıklayınca kapat
    modal.addEventListener('click', function (e) {
        if (e.target === modal) close();
    });

    // ESC ile kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) close();
    });

    // Focus trap — modal açıkken Tab modal içinde kalır
    modal.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        var focusable = modal.querySelectorAll('button, textarea, input, a[href]');
        if (focusable.length === 0) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });

    // --- Çoklu görsel önizleme ---
    if (fileInput && previewGrid) {
        fileInput.addEventListener('change', function (e) {
            previewGrid.innerHTML = '';
            var files = e.target.files;
            var count = Math.min(files.length, 4);
            for (var i = 0; i < count; i++) {
                (function (file) {
                    var reader = new FileReader();
                    reader.onload = function (ev) {
                        var wrap = document.createElement('div');
                        wrap.className = 'image-preview-item';
                        wrap.innerHTML = '<img src="' + ev.target.result + '" alt="Önizleme">';
                        previewGrid.appendChild(wrap);
                    };
                    reader.readAsDataURL(file);
                })(files[i]);
            }
            if (files.length > 4) {
                var note = document.createElement('p');
                note.className = 'muted';
                note.textContent = 'İlk 4 görsel yüklenecek.';
                previewGrid.appendChild(note);
            }
        });
    }

    // --- Video ekle: görsel ile video AYNI POSTTA birlikte desteklenmiyor,
    // biri seçilince diğeri temizlenir (backend de aynı kuralı uygular). ---
    if (videoInput && videoPreview) {
        videoInput.addEventListener('change', function (e) {
            var file = e.target.files[0];
            if (!file) {
                videoPreview.style.display = 'none';
                videoPreview.removeAttribute('src');
                return;
            }
            if (fileInput) fileInput.value = '';
            if (previewGrid) previewGrid.innerHTML = '';
            videoPreview.src = URL.createObjectURL(file);
            videoPreview.style.display = 'block';
        });
    }
    if (fileInput && videoInput) {
        fileInput.addEventListener('change', function () {
            if (fileInput.files.length && videoInput.files.length) {
                videoInput.value = '';
                videoPreview.style.display = 'none';
                videoPreview.removeAttribute('src');
            }
        });
    }

    // --- Anket ekle: görsel/video ile AYNI POSTTA birlikte desteklenmiyor
    // (backend de aynı kuralı uygular, bkz. routes.create_post()) ---
    function resetPollUI() {
        if (!pollContainer) return;
        pollContainer.hidden = true;
        if (pollToggleBtn) pollToggleBtn.hidden = false;
        pollContainer.querySelectorAll('input').forEach(function (inp, i) {
            inp.value = '';
            if (i >= 2) inp.hidden = true;
        });
        if (pollAddOptionBtn) pollAddOptionBtn.hidden = false;
    }

    if (pollToggleBtn && pollContainer) {
        pollToggleBtn.addEventListener('click', function () {
            pollContainer.hidden = false;
            pollToggleBtn.hidden = true;
            // Anket seçilince görsel/video temizlenir
            if (fileInput) fileInput.value = '';
            if (previewGrid) previewGrid.innerHTML = '';
            if (videoInput) videoInput.value = '';
            if (videoPreview) { videoPreview.style.display = 'none'; videoPreview.removeAttribute('src'); }
            var firstInput = pollContainer.querySelector('input');
            if (firstInput) firstInput.focus();
        });
    }

    if (pollAddOptionBtn && pollContainer) {
        pollAddOptionBtn.addEventListener('click', function () {
            var hiddenInputs = pollContainer.querySelectorAll('input[hidden]');
            if (hiddenInputs.length === 0) return;
            hiddenInputs[0].hidden = false;
            hiddenInputs[0].focus();
            if (hiddenInputs.length === 1) pollAddOptionBtn.hidden = true; // 4 seçenek doldu
        });
    }

    if (pollCancelBtn) {
        pollCancelBtn.addEventListener('click', resetPollUI);
    }

    // Görsel/video seçilince anket temizlenir (ters yön)
    [fileInput, videoInput].forEach(function (inp) {
        if (!inp) return;
        inp.addEventListener('change', function () {
            if (inp.files.length && pollContainer && !pollContainer.hidden) resetPollUI();
        });
    });

    // --- Sürükle-bırak görsel yükleme (tıklanabilir "Görsel Ekle" her zaman
    // alternatif olarak duruyor — WCAG 2.5.7 tek-imleçli alternatif) ---
    if (fileInput && modalBox) {
        ['dragover', 'dragenter'].forEach(function (evt) {
            modalBox.addEventListener(evt, function (e) {
                e.preventDefault();
                modalBox.classList.add('drag-over');
            });
        });
        ['dragleave', 'dragend'].forEach(function (evt) {
            modalBox.addEventListener(evt, function () {
                modalBox.classList.remove('drag-over');
            });
        });
        modalBox.addEventListener('drop', function (e) {
            e.preventDefault();
            modalBox.classList.remove('drag-over');
            var dropped = e.dataTransfer && e.dataTransfer.files;
            if (!dropped || !dropped.length) return;

            var dt = new DataTransfer();
            var count = 0;
            for (var i = 0; i < dropped.length && count < 4; i++) {
                if (dropped[i].type.startsWith('image/')) {
                    dt.items.add(dropped[i]);
                    count++;
                }
            }
            if (count === 0) return;
            fileInput.files = dt.files;
            fileInput.dispatchEvent(new Event('change'));
        });
    }
})();
