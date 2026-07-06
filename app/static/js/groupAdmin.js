// Grup sohbeti yönetim modalı (rename, üye ekle/çıkar, admin ata, ayrıl).
// Modal `_conversation_panel.html` içinde yaşıyor ve panel her konuşma
// değişiminde AJAX ile innerHTML olarak yeniden oluşuyor (messagesPanel.js) —
// bu yüzden TÜM dinleyiciler document-level delegation kullanır, modal/buton
// elementlerine doğrudan referans alıp cache'lemek burada stale kalır
// (groupChat.js'teki desen burada ÇALIŞMAZ çünkü o modal sabit sidebar'da).

(function () {
    var addSelectedUsers = new Set();
    var addSearchTimeout = null;
    var lastFocused = null;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function getModal() {
        return document.getElementById('group-manage-modal');
    }

    function getConversationId() {
        var modal = getModal();
        return modal ? modal.dataset.conversationId : null;
    }

    function openModal() {
        var modal = getModal();
        if (!modal) return;
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        addSelectedUsers.clear();
        updateAddSubmitButton();
        var searchInput = document.getElementById('group-add-search-input');
        if (searchInput) {
            searchInput.value = '';
            fetchAddTargets('');
        }
    }

    function closeModal() {
        var modal = getModal();
        if (!modal) return;
        modal.hidden = true;
        document.body.style.overflow = '';
        addSelectedUsers.clear();
        var list = document.getElementById('group-add-user-list');
        if (list) list.innerHTML = '';
        if (lastFocused) lastFocused.focus();
    }

    function updateAddSubmitButton() {
        var btn = document.getElementById('group-add-submit-btn');
        if (!btn) return;
        btn.disabled = addSelectedUsers.size === 0;
        btn.textContent = addSelectedUsers.size > 0 ? 'Ekle (' + addSelectedUsers.size + ')' : 'Ekle';
    }

    async function fetchAddTargets(query) {
        var list = document.getElementById('group-add-user-list');
        if (!list) return;
        list.innerHTML = '<p class="muted center">Yükleniyor...</p>';
        try {
            var res = await fetch('/messages/share-targets?q=' + encodeURIComponent(query || ''));
            var users = await res.json();
            renderAddUserList(users);
        } catch (err) {
            list.innerHTML = '<p class="muted center">Hata oluştu.</p>';
        }
    }

    function renderAddUserList(users) {
        var list = document.getElementById('group-add-user-list');
        if (!list) return;
        if (users.length === 0) {
            list.innerHTML = '<p class="muted center">Kullanıcı bulunamadı.</p>';
            return;
        }
        list.innerHTML = '';
        users.forEach(function (u) {
            var item = document.createElement('div');
            item.className = 'share-target-item' + (addSelectedUsers.has(u.id) ? ' selected' : '');
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
            var checkEl = document.createElement('div');
            checkEl.className = 'checkbox';
            item.append(avatarEl, nameEl, checkEl);
            list.appendChild(item);
        });
    }

    async function renameGroup() {
        var input = document.getElementById('group-rename-input');
        var convId = getConversationId();
        if (!input || !convId) return;
        var name = input.value.trim();
        if (!name) return;
        try {
            var res = await fetch('/messages/group/' + convId + '/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                body: JSON.stringify({ name: name }),
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Grup adı güncellenemedi.');
            // Basit yaklaşım: küçük bir arkadaş grubu uygulaması, optimistic
            // UI yerine sayfayı yeniden yükleyip sunucudan taze veri al.
            window.location.reload();
        } catch (err) {
            alert(err.message || 'Grup adı güncellenemedi.');
        }
    }

    async function toggleAdmin(userId) {
        var convId = getConversationId();
        if (!convId) return;
        try {
            var res = await fetch('/messages/group/' + convId + '/members/' + userId + '/toggle-admin', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken() },
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'İşlem başarısız.');
            window.location.reload();
        } catch (err) {
            alert(err.message || 'İşlem başarısız.');
        }
    }

    async function removeMember(userId) {
        if (!confirm('Bu kişiyi gruptan çıkarmak istiyor musun?')) return;
        var convId = getConversationId();
        if (!convId) return;
        try {
            var res = await fetch('/messages/group/' + convId + '/members/' + userId + '/remove', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken() },
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'İşlem başarısız.');
            window.location.reload();
        } catch (err) {
            alert(err.message || 'İşlem başarısız.');
        }
    }

    async function addMembers() {
        var convId = getConversationId();
        if (!convId || addSelectedUsers.size === 0) return;
        var btn = document.getElementById('group-add-submit-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Ekleniyor...'; }
        try {
            var res = await fetch('/messages/group/' + convId + '/members/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                body: JSON.stringify({ user_ids: Array.from(addSelectedUsers) }),
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Üye eklenemedi.');
            window.location.reload();
        } catch (err) {
            alert(err.message || 'Üye eklenemedi.');
            updateAddSubmitButton();
        }
    }

    async function leaveGroup() {
        if (!confirm('Bu gruptan ayrılmak istediğine emin misin?')) return;
        var convId = getConversationId();
        if (!convId) return;
        try {
            var res = await fetch('/messages/group/' + convId + '/leave', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken() },
            });
            var data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Gruptan ayrılınamadı.');
            window.location.href = data.redirect || '/messages';
        } catch (err) {
            alert(err.message || 'Gruptan ayrılınamadı.');
        }
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest('#open-group-manage-modal')) {
            openModal();
            return;
        }
        var modal = getModal();
        if (!modal || modal.hidden) return;

        if (e.target.closest('#close-group-manage-modal')) {
            closeModal();
            return;
        }
        if (e.target === modal) {
            closeModal();
            return;
        }
        if (e.target.closest('#group-rename-save-btn')) {
            renameGroup();
            return;
        }
        var toggleBtn = e.target.closest('.group-toggle-admin-btn');
        if (toggleBtn) {
            toggleAdmin(toggleBtn.dataset.userId);
            return;
        }
        var removeBtn = e.target.closest('.group-remove-member-btn');
        if (removeBtn) {
            removeMember(removeBtn.dataset.userId);
            return;
        }
        if (e.target.closest('#group-add-submit-btn')) {
            addMembers();
            return;
        }
        if (e.target.closest('#group-leave-btn')) {
            leaveGroup();
            return;
        }
        var item = e.target.closest('.share-target-item');
        if (item && modal.contains(item)) {
            var userId = item.dataset.userId;
            if (addSelectedUsers.has(userId)) {
                addSelectedUsers.delete(userId);
                item.classList.remove('selected');
            } else {
                addSelectedUsers.add(userId);
                item.classList.add('selected');
            }
            updateAddSubmitButton();
        }
    });

    document.addEventListener('keydown', function (e) {
        var modal = getModal();
        if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
    });

    // Üye ekleme arama kutusu — panel her yenilendiğinde input yeniden
    // oluştuğu için bu da document-level delegation.
    document.addEventListener('input', function (e) {
        if (e.target.id !== 'group-add-search-input') return;
        clearTimeout(addSearchTimeout);
        var q = e.target.value.trim();
        addSearchTimeout = setTimeout(function () { fetchAddTargets(q); }, 300);
    });
})();
