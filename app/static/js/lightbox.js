// Görsel lightbox — feed, post detay ve profil (Medya sekmesi) görsellerinde
// ortak kullanılır. Bir görsele tıklandığında, en yakın galeri kapsayıcısı
// (.media-grid veya .post-images) içindeki tüm görseller "galeri" olur; tekil
// görsellerde (kapsayıcı yoksa) sadece o görsel gösterilir. Odak hapsi, Escape
// ile kapanma, odağın tetikleyiciye dönmesi, ok tuşlarıyla önceki/sonraki.

(function () {
    var overlay = document.getElementById('lightbox');
    if (!overlay) return;

    var img = document.getElementById('lightbox-img');
    var closeBtn = document.getElementById('lightbox-close');
    var prevBtn = document.getElementById('lightbox-prev');
    var nextBtn = document.getElementById('lightbox-next');
    var gotoLink = document.getElementById('lightbox-goto');

    var TRIGGER_SELECTOR = '.media-thumb, .post-image, .post-image-tile';
    var GALLERY_SELECTOR = '.media-grid, .post-images';

    var gallery = [];
    var currentIndex = -1;
    var lastFocused = null;

    function srcOf(el) {
        return el.dataset.lightboxSrc || el.src || '';
    }

    function postUrlOf(el) {
        return el.dataset.postUrl || '#';
    }

    function buildGallery(trigger) {
        var container = trigger.closest(GALLERY_SELECTOR);
        if (container) {
            return Array.prototype.slice.call(container.querySelectorAll(TRIGGER_SELECTOR));
        }
        return [trigger];
    }

    function render() {
        var el = gallery[currentIndex];
        if (!el) return;
        img.src = srcOf(el);
        gotoLink.href = postUrlOf(el);
        var multi = gallery.length > 1;
        prevBtn.hidden = !multi;
        nextBtn.hidden = !multi;
    }

    function open(trigger) {
        gallery = buildGallery(trigger);
        currentIndex = gallery.indexOf(trigger);
        if (currentIndex === -1) currentIndex = 0;
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
        if (!gallery.length) return;
        currentIndex = (currentIndex + delta + gallery.length) % gallery.length;
        render();
    }

    document.addEventListener('click', function (e) {
        var trigger = e.target.closest(TRIGGER_SELECTOR);
        if (trigger) {
            e.preventDefault();
            open(trigger);
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
            var focusable = Array.prototype.slice.call(
                overlay.querySelectorAll('button, a[href]')
            ).filter(function (el) { return !el.hidden; });
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
