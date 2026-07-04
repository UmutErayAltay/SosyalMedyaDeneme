// Dark mode toggle — FOUC önleyici script base.html <head>'inde çalışır.
// Bu dosya sadece toggle butonu etkileşimini yönetir.

(function () {
    var toggle = document.getElementById('theme-toggle');
    if (!toggle) return;

    var root = document.documentElement;
    var sun = toggle.querySelector('.icon-sun');
    var moon = toggle.querySelector('.icon-moon');

    function syncIcons() {
        var isDark = root.getAttribute('data-theme') === 'dark';
        if (sun) sun.style.display = isDark ? 'inline' : 'none';
        if (moon) moon.style.display = isDark ? 'none' : 'inline';
        toggle.setAttribute('aria-label', isDark ? 'Aydınlık moda geç' : 'Karanlık moda geç');
    }

    syncIcons();

    toggle.addEventListener('click', function () {
        var isDark = root.getAttribute('data-theme') === 'dark';
        if (isDark) {
            root.removeAttribute('data-theme');
            try { localStorage.setItem('theme', 'light'); } catch (e) {}
        } else {
            root.setAttribute('data-theme', 'dark');
            try { localStorage.setItem('theme', 'dark'); } catch (e) {}
        }
        syncIcons();
    });

    // DEĞİŞTİ: localStorage kontrolünü try/catch içine aldık
    // ve mantığı "kullanıcı zaten manuel seçim yaptıysa dokunma" şeklinde netleştirdik.
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
        var userChoice;
        try { userChoice = localStorage.getItem('theme'); } catch (err) { userChoice = null; }

        if (!userChoice) {
            if (e.matches) root.setAttribute('data-theme', 'dark');
            else root.removeAttribute('data-theme');
            syncIcons();
        }
    });
})();
