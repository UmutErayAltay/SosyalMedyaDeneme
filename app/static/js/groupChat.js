// Yeni grup sohbeti oluşturma modalı — shareModal.js ile aynı desen
// (kullanıcı arama/çoklu seçim), farkı: post değil grup oluşturuyor.

(function () {
    var modal = document.getElementById('new-group-modal');
    var openBtn = document.getElementById('open-new-group-modal');
    var closeBtn = document.getElementById('close-new-group-modal');
    var nameInput = document.getElementById('group-name-input');
    var searchInput = document.getElementById('group-search-input');
    var userListDiv = document.getElementById('group-user-list');
    var submitBtn = document.getElementById('group-submit-btn');
    if (!modal || !openBtn) return;

    var selectedUsers = new Set();
    var searchTimeout = null;
    var lastFocused = null;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function openModal() {
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        selectedUsers.clear();
        nameInput.value = '';
        searchInput.value = '';
        updateSubmitButton();
        fetchTargets();
        setTimeout(function () { nameInput.focus(); }, 50);
    }

    function closeModal() {
        modal.hidden = true;
        document.body.style.overflow = '';
        selectedUsers.clear();
        userListDiv.innerHTML = '';
        if (lastFocused) lastFocused.focus();
    }

    openBtn.addEventListener('click', openModal);
    closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) closeModal();
    });

    // Focus trap
    modal.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        var focusable = modal.querySelectorAll('button:not([disabled]), input, [tabindex]:not([tabindex="-1"])');
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

    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        var q = this.value.trim();
        searchTimeout = setTimeout(function () { fetchTargets(q); }, 300);
    });

    async function fetchTargets(query) {
        userListDiv.innerHTML = '<p class="muted center">Yükleniyor...</p>';
        try {
            var res = await fetch('/messages/share-targets?q=' + encodeURIComponent(query || ''));
            var users = await res.json();
            renderUserList(users);
        } catch (err) {
            userListDiv.innerHTML = '<p class="muted center">Hata oluştu.</p>';
        }
    }

    function renderUserList(users) {
        if (users.length === 0) {
            userListDiv.innerHTML = '<p class="muted center">Kullanıcı bulunamadı.</p>';
            return;
        }
        userListDiv.innerHTML = '';
        users.forEach(function (u) {
            var item = document.createElement('div');
            item.className = 'share-target-item' + (selectedUsers.has(u.id) ? ' selected' : '');

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
            var checkEl = document.createElement('div');
            checkEl.className = 'checkbox';
            item.append(avatarEl, nameEl, checkEl);

            item.addEventListener('click', function () {
                if (selectedUsers.has(u.id)) {
                    selectedUsers.delete(u.id);
                    item.classList.remove('selected');
                } else {
                    selectedUsers.add(u.id);
                    item.classList.add('selected');
                }
                updateSubmitButton();
            });
            userListDiv.appendChild(item);
        });
    }

    function updateSubmitButton() {
        var ready = selectedUsers.size >= 2 && nameInput.value.trim().length > 0;
        submitBtn.disabled = !ready;
        submitBtn.textContent = selectedUsers.size > 0
            ? 'Grup Oluştur (' + (selectedUsers.size + 1) + ' kişi)'
            : 'Grup Oluştur';
    }
    nameInput.addEventListener('input', updateSubmitButton);

    submitBtn.addEventListener('click', async function () {
        var name = nameInput.value.trim();
        if (!name || selectedUsers.size < 2) return;

        submitBtn.disabled = true;
        submitBtn.textContent = 'Oluşturuluyor...';
        try {
            var res = await fetch('/messages/group/new', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken(),
                },
                body: JSON.stringify({ name: name, user_ids: Array.from(selectedUsers) }),
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Grup oluşturulamadı.');
            window.location.href = '/messages/' + data.conversation_id;
        } catch (err) {
            alert(err.message || 'Grup oluşturulamadı.');
            updateSubmitButton();
        }
    });
})();
