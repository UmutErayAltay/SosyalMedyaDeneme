// Global mesaj araması — inbox'taki tüm sohbetlerde ara.
// Konuşma listesi başlığında arama ikonu + debounce'lı API çağrısı.

(function () {
    var searchPanel = document.getElementById('global-msg-search');
    var searchInput = document.getElementById('global-msg-search-input');
    var searchResults = document.getElementById('global-msg-search-results');
    var closeBtn = document.getElementById('global-msg-search-close-btn');

    if (!searchPanel || !searchInput || !searchResults || !closeBtn) {
        return;
    }

    var debounceTimer;
    var lastQuery = '';

    // Konuşma listesi başlığına arama butonu ekle (varsa)
    var sidebarHeader = document.querySelector('.messages-sidebar-header');
    if (sidebarHeader && !document.getElementById('inbox-search-toggle-btn')) {
        var toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.id = 'inbox-search-toggle-btn';
        toggleBtn.className = 'btn btn-ghost small';
        toggleBtn.setAttribute('aria-label', 'Mesajlarda ara');
        toggleBtn.setAttribute('aria-expanded', 'false');
        toggleBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
        sidebarHeader.appendChild(toggleBtn);

        toggleBtn.addEventListener('click', function (e) {
            e.preventDefault();
            var willOpen = searchPanel.hidden;
            searchPanel.hidden = !willOpen;
            toggleBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            if (willOpen) {
                searchInput.focus();
            } else {
                searchInput.value = '';
                searchResults.innerHTML = '';
                lastQuery = '';
            }
        });
    }

    // Arama kutusu kapatma butonu
    closeBtn.addEventListener('click', function (e) {
        e.preventDefault();
        searchPanel.hidden = true;
        searchInput.value = '';
        searchResults.innerHTML = '';
        lastQuery = '';
        var toggleBtn = document.getElementById('inbox-search-toggle-btn');
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', 'false');
        }
    });

    // Debounce'lı arama
    searchInput.addEventListener('input', function () {
        var query = this.value.trim();

        clearTimeout(debounceTimer);
        searchResults.innerHTML = '';

        if (!query) {
            return;
        }

        lastQuery = query;

        debounceTimer = setTimeout(function () {
            if (lastQuery !== query) return; // Başka input gerçekleşti

            fetch('/messages/search?q=' + encodeURIComponent(query) + '&offset=0')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data || lastQuery !== query) return;

                    searchResults.innerHTML = '';

                    if (!data.results || data.results.length === 0) {
                        searchResults.innerHTML = '<p style="padding: 10px 12px; color: var(--muted); font-size: 13px;">Sonuç bulunamadı</p>';
                        return;
                    }

                    data.results.forEach(function (msg) {
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'global-msg-search-result';
                        btn.innerHTML = '<span class="msg-search-result-sender">' + escapeHtml(msg.sender) + '</span>' +
                            '<span class="msg-search-result-content">' + escapeHtml(msg.content.substring(0, 60)) + (msg.content.length > 60 ? '…' : '') + '</span>' +
                            '<span class="msg-search-result-time">' + msg.created_at + '</span>';
                        btn.addEventListener('click', function () {
                            // Gerçek jump-to-message şeması (chat.js:909 #msg-<id>
                            // hash'ini okuyup vurguluyor, ?all=1 tam geçmişi
                            // getiriyor — mesaj henüz DOM'da yüklenmemiş olabilir).
                            window.location.href = '/messages/' + msg.conversation_id + '?all=1#msg-' + msg.id;
                        });
                        searchResults.appendChild(btn);
                    });
                })
                .catch(function (err) {
                    console.error('Mesaj araması başarısız:', err);
                });
        }, 200);
    });

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
})();
