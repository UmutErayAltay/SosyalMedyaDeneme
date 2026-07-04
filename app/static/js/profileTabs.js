// Profil sekmeleri — WAI-ARIA APG "tabs" deseni (otomatik etkinleştirme):
// ok tuşlarıyla gezinme aynı anda sekmeyi de etkinleştirir, Home/End ilk/son sekmeye atlar.

(function () {
    var tablist = document.querySelector('.tablist');
    if (!tablist) return;
    var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));

    function activate(tab) {
        tabs.forEach(function (t) {
            var selected = t === tab;
            t.setAttribute('aria-selected', selected ? 'true' : 'false');
            t.tabIndex = selected ? 0 : -1;
            var panel = document.getElementById(t.getAttribute('aria-controls'));
            if (panel) panel.hidden = !selected;
        });
    }

    tabs.forEach(function (tab, i) {
        tab.addEventListener('click', function () { activate(tab); });
        tab.addEventListener('keydown', function (e) {
            var next;
            if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
            else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
            else if (e.key === 'Home') next = tabs[0];
            else if (e.key === 'End') next = tabs[tabs.length - 1];
            if (next) {
                e.preventDefault();
                activate(next);
                next.focus();
            }
        });
    });
})();
