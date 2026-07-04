// Medya grid lightbox — odak hapsi, Escape ile kapanma, odağın tetikleyiciye
// dönmesi, ok tuşlarıyla önceki/sonraki (WCAG modal dialog deseni).

(function () {
    var overlay = document.getElementById('lightbox');
    if (!overlay) return;

    var img = document.getElementById('lightbox-img');
    var closeBtn = document.getElementById('lightbox-close');
    var prevBtn = document.getElementById('lightbox-prev');
    var nextBtn = document.getElementById('lightbox-next');
    var gotoLink = document.getElementById('lightbox-goto');

    var thumbs = [];
    var currentIndex = -1;
    var lastFocused = null;

    function refreshThumbs() {
        thumbs = Array.prototype.slice.call(document.querySelectorAll('.media-thumb'));
    }

    function render() {
        var thumb = thumbs[currentIndex];
        if (!thumb) return;
        img.src = thumb.dataset.lightboxSrc;
        gotoLink.href = thumb.dataset.postUrl;
    }

    function open(index) {
        refreshThumbs();
        if (!thumbs[index]) return;
        currentIndex = index;
        lastFocused = document.activeElement;
        render();
        overlay.hidden = false;
        document.body.style.overflow = 'hidden';
        closeBtn.focus();
    }

    function close() {
        overlay.hidden = true;
        document.body.style.overflow = '';
        img.src = '';
        if (lastFocused) lastFocused.focus();
    }

    function show(delta) {
        if (!thumbs.length) return;
        currentIndex = (currentIndex + delta + thumbs.length) % thumbs.length;
        render();
    }

    document.addEventListener('click', function (e) {
        var thumb = e.target.closest('.media-thumb');
        if (thumb) {
            refreshThumbs();
            open(thumbs.indexOf(thumb));
        }
    });

    closeBtn.addEventListener('click', close);
    prevBtn.addEventListener('click', function () { show(-1); });
    nextBtn.addEventListener('click', function () { show(1); });

    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close();
    });

    document.addEventListener('keydown', function (e) {
        if (overlay.hidden) return;
        if (e.key === 'Escape') {
            close();
        } else if (e.key === 'ArrowRight') {
            show(1);
        } else if (e.key === 'ArrowLeft') {
            show(-1);
        } else if (e.key === 'Tab') {
            var focusable = overlay.querySelectorAll('button, a[href]');
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
