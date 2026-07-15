// Post kartındaki "⋯ Daha fazla" menüsü (sabitle/arşivle/düzenle/sil) —
// AJAX ile yeniden yüklenen listelerde (feed sonsuz kaydırma) doğrudan
// addEventListener stale kalırdı, document-level delegation kullanılır.

(function () {
    function closeAllMenus(except) {
        document.querySelectorAll('.post-more-menu').forEach(function (menu) {
            if (menu === except) return;
            menu.hidden = true;
            var btn = menu.previousElementSibling;
            if (btn) btn.setAttribute('aria-expanded', 'false');
        });
    }

    document.addEventListener('click', function (e) {
        var toggleBtn = e.target.closest('.post-more-btn');
        if (toggleBtn) {
            e.preventDefault();
            var menu = toggleBtn.nextElementSibling;
            if (!menu) return;
            var willOpen = menu.hidden;
            closeAllMenus();
            menu.hidden = !willOpen;
            toggleBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            return;
        }

        // Menü içinde bir öğeye (link/submit) tıklanınca menü kendiliğinden
        // kapanır (sayfa/form navigasyonu zaten devam eder); dışarı
        // tıklanınca da kapat.
        if (!e.target.closest('.post-more-wrap')) {
            closeAllMenus();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAllMenus();
    });
})();
