// Yakın arkadaşlar listesi yönetimi: arama + ekleme
// .share-target-item class'ı groupAdmin.js'teki selection UI'ıyla eşleşir,
// ama bu sayfada seçim yok — direkt "+ Ekle" tetikler (tek bir fetch POST).

(function () {
    var searchTimeout = null;
    var selectedUserId = null;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    async function fetchSearchResults(query) {
        var resultsContainer = document.getElementById('close-friend-search-results');
        if (!resultsContainer) return;

        if (!query.trim()) {
            resultsContainer.innerHTML = '';
            return;
        }

        resultsContainer.innerHTML = '<p class="muted center">Yükleniyor...</p>';
        try {
            var res = await fetch('/messages/share-targets?q=' + encodeURIComponent(query));
            var users = await res.json();
            renderSearchResults(users);
        } catch (err) {
            resultsContainer.innerHTML = '<p class="muted center">Hata oluştu.</p>';
        }
    }

    function renderSearchResults(users) {
        var resultsContainer = document.getElementById('close-friend-search-results');
        if (!resultsContainer) return;

        if (users.length === 0) {
            resultsContainer.innerHTML = '<p class="muted center">Kullanıcı bulunamadı.</p>';
            return;
        }

        resultsContainer.innerHTML = '';
        users.forEach(function (u) {
            var item = document.createElement('div');
            item.className = 'share-target-item';
            item.dataset.userId = u.id;

            var avatarEl;
            if (u.avatar_url) {
                avatarEl = document.createElement('img');
                avatarEl.src = u.avatar_url;
                avatarEl.className = 'avatar';
                avatarEl.alt = '';
            } else {
                avatarEl = document.createElement('div');
                avatarEl.className = 'avatar avatar-placeholder';
            }

            var nameEl = document.createElement('span');
            nameEl.className = 'username';
            nameEl.textContent = u.username;

            var btnEl = document.createElement('button');
            btnEl.type = 'button';
            btnEl.className = 'btn btn-primary small';
            btnEl.textContent = '+ Ekle';
            btnEl.dataset.userId = u.id;

            item.append(avatarEl, nameEl, btnEl);
            resultsContainer.appendChild(item);
        });
    }

    async function addCloseFriend(userId) {
        try {
            var res = await fetch('/close-friends/add', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken()
                },
                body: JSON.stringify({ user_id: userId })
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Eklenemedi.');
            // Sayfayı yenile — yakın arkadaşlar listesi güncellensin.
            window.location.reload();
        } catch (err) {
            alert(err.message || 'Hata oluştu.');
        }
    }

    // Arama input'u listener'ı
    document.addEventListener('input', function (e) {
        if (e.target.id !== 'close-friend-search-input') return;
        clearTimeout(searchTimeout);
        var q = e.target.value.trim();
        searchTimeout = setTimeout(function () { fetchSearchResults(q); }, 300);
    });

    // "+ Ekle" buton listener'ı (document delegation, çünkü sonuç dinamiktir)
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('button[data-user-id]');
        if (!btn || btn.closest('#close-friend-search-results') === null) return;
        addCloseFriend(btn.dataset.userId);
    });
})();
