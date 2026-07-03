// Post paylaşma modalı — erişilebilir (focus trap, ESC, overlay click)
// Çoklu görsel önizleme

(function () {
    var modal = document.getElementById('post-modal');
    var openBtn = document.getElementById('open-post-modal');
    var closeBtn = document.getElementById('close-post-modal');
    var fileInput = document.getElementById('post-image-input');
    var previewGrid = document.getElementById('post-image-preview');
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
})();
