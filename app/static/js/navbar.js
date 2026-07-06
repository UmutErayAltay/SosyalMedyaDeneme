// Mobil menü toggle
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

// GARANTİLİ AKILLI NAVBAR (WHEEL + TOUCH + SCROLL HİBRİT SİSTEMİ)
(function () {
    var navbar = document.querySelector('.navbar');
    var searchInput = document.getElementById('nav-search-input');
    var navLinks = document.getElementById('nav-links');
    if (!navbar) return;

    var HIDE_THRESHOLD = 60; // Sayfa en üstteyken gizlenmeyi engellemek için eşik
    
    // body'ye de aynı class eklenir: .feed-sidebar gibi sabit (sticky) öğeler
    // navbar gizlenince/gelince üstteki boşluğa göre kendi top offset'lerini
    // ayarlayabilsin diye (bkz. style.css .feed-sidebar / body.navbar-hidden).
    function showNavbar() {
        navbar.classList.remove('navbar-hidden');
        document.body.classList.remove('navbar-hidden');
    }

    function hideNavbar() {
        var currentScroll = window.scrollY || document.documentElement.scrollTop;
        if (currentScroll > HIDE_THRESHOLD) {
            navbar.classList.add('navbar-hidden');
            document.body.classList.add('navbar-hidden');
        }
    }

    // 1. FARE TEKERLEĞİ (Wheel) - Overflow alanlarında (örn. mesaj paneli) kaydırmayı da yakalar
    window.addEventListener('wheel', function (e) {
        var searchFocused = document.activeElement === searchInput;
        var menuOpen = navLinks && navLinks.classList.contains('open');
        if (searchFocused || menuOpen) return;

        if (e.deltaY < 0) {
            showNavbar(); // Tekerlek yukarı kaydırıldı
        } else if (e.deltaY > 0) {
            hideNavbar(); // Tekerlek aşağı kaydırıldı
        }
    }, { passive: true });

    // 2. MOBİL DOKUNMA (Touch) - Dokunmatik ekranlardaki ivmeyi yakalar
    var touchStartY = 0;
    window.addEventListener('touchstart', function (e) {
        touchStartY = e.touches[0].clientY;
    }, { passive: true });

    window.addEventListener('touchmove', function (e) {
        var searchFocused = document.activeElement === searchInput;
        if (searchFocused) return;

        var touchEndY = e.touches[0].clientY;
        var diffY = touchStartY - touchEndY;

        if (Math.abs(diffY) > 10) { // 10px tolerans (titremeyi önler)
            if (diffY < 0) {
                showNavbar(); // Sayfa yukarı
            } else {
                hideNavbar(); // Sayfa aşağı
            }
            touchStartY = touchEndY;
        }
    }, { passive: true });

    // 3. STANDART SCROLL & AJAX (Fallback)
    window.addEventListener('scroll', function () {
        var currentScrollY = window.scrollY || document.documentElement.scrollTop;
        if (currentScrollY <= HIDE_THRESHOLD) {
            showNavbar();
        }
    }, { passive: true });

    // Sayfa içi AJAX geçişlerinde (örneğin sol menü tıklamalarında) navbar'ı geri getir
    window.addEventListener('popstate', showNavbar);

    if (searchInput) {
        searchInput.addEventListener('focus', showNavbar);
    }
})();
