// Mobil menü toggle — erişilebilir (aria-expanded)

(function () {
    var toggle = document.querySelector('.nav-toggle');
    var links = document.getElementById('nav-links');
    if (!toggle || !links) return;

    toggle.addEventListener('click', function () {
        var isOpen = links.classList.toggle('open');
        toggle.classList.toggle('active', isOpen);
        toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    // Menü dışına tıklayınca kapat (mobil)
    document.addEventListener('click', function (e) {
        if (!links.classList.contains('open')) return;
        if (!toggle.contains(e.target) && !links.contains(e.target)) {
            links.classList.remove('open');
            toggle.classList.remove('active');
            toggle.setAttribute('aria-expanded', 'false');
        }
    });

    // Esc ile kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && links.classList.contains('open')) {
            links.classList.remove('open');
            toggle.classList.remove('active');
            toggle.setAttribute('aria-expanded', 'false');
            toggle.focus();
        }
    });
})();

// Scroll-hide navbar: aşağı kaydırınca gizlen, yukarı kaydırınca geri gel.
// Arama input'u odaktayken veya sayfanın en üstündeyken HİÇBİR ZAMAN gizlenmez
// (erişilebilirlik: odaktaki öğenin görsel bağlamı kaybolmamalı).
(function () {
    var navbar = document.querySelector('.navbar');
    var searchInput = document.getElementById('nav-search-input');
    var navLinks = document.getElementById('nav-links');
    if (!navbar) return;

    var HIDE_THRESHOLD = 80;
    var lastY = window.scrollY;
    var ticking = false;

    function update() {
        var y = window.scrollY;
        var searchFocused = document.activeElement === searchInput;
        var menuOpen = navLinks && navLinks.classList.contains('open');

        if (searchFocused || menuOpen || y < HIDE_THRESHOLD) {
            navbar.classList.remove('navbar-hidden');
        } else if (y > lastY) {
            navbar.classList.add('navbar-hidden');
        } else {
            navbar.classList.remove('navbar-hidden');
        }
        lastY = y;
        ticking = false;
    }

    window.addEventListener('scroll', function () {
        if (!ticking) {
            requestAnimationFrame(update);
            ticking = true;
        }
    }, { passive: true });

    if (searchInput) {
        searchInput.addEventListener('focus', function () {
            navbar.classList.remove('navbar-hidden');
        });
    }
})();
