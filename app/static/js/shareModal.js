// app/static/js/shareModal.js

(function () {
    const modal = document.getElementById('share-modal');
    const closeBtn = document.getElementById('close-share-modal');
    const searchInput = document.getElementById('share-search-input');
    const userListDiv = document.getElementById('share-user-list');
    const noteInput = document.getElementById('share-note-input');
    const submitBtn = document.getElementById('share-submit-btn');

    if (!modal) return;

    let currentPostId = null;
    let selectedUsers = new Set();
    let searchTimeout = null;

    // Paylaş butonlarını dinle
    document.body.addEventListener('click', function (e) {
        const btn = e.target.closest('.share-post-btn');
        if (btn) {
            currentPostId = btn.getAttribute('data-post-id');
            openModal();
        }
    });

    function openModal() {
        modal.hidden = false;
        document.body.style.overflow = 'hidden';

        selectedUsers.clear();
        searchInput.value = '';
        noteInput.value = '';

        updateSubmitButton();
        fetchTargets();

        setTimeout(() => searchInput.focus(), 50);
    }

    function closeModal() {
        modal.hidden = true;
        document.body.style.overflow = '';

        currentPostId = null;
        selectedUsers.clear();
        searchInput.value = '';
        noteInput.value = '';

        userListDiv.innerHTML = '';
        updateSubmitButton();
    }

    closeBtn.addEventListener('click', closeModal);

    modal.addEventListener('click', function (e) {
        if (e.target === modal) {
            closeModal();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) {
            closeModal();
        }
    });

    // Arama
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);

        const q = this.value.trim();

        searchTimeout = setTimeout(function () {
            fetchTargets(q);
        }, 300);
    });

    async function fetchTargets(query = "") {
        userListDiv.innerHTML = '<p class="muted center">Yükleniyor...</p>';

        try {
            const res = await fetch(`/messages/share-targets?q=${encodeURIComponent(query)}`);
            const users = await res.json();

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

            const item = document.createElement('div');

            item.className =
                'share-target-item' +
                (selectedUsers.has(u.id) ? ' selected' : '');

            const avatarHtml = u.avatar_url
                ? `<img src="${u.avatar_url}" class="avatar" alt="">`
                : `<div class="avatar avatar-placeholder"></div>`;

            item.innerHTML = `
                ${avatarHtml}
                <span class="username">${u.username}</span>
                <div class="checkbox"></div>
            `;

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

        submitBtn.disabled = selectedUsers.size === 0;

        submitBtn.textContent =
            selectedUsers.size > 0
                ? `Gönder (${selectedUsers.size})`
                : 'Gönder';
    }

    // PAYLAŞ
    submitBtn.addEventListener('click', async function () {

        if (!currentPostId || selectedUsers.size === 0) {
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = 'Gönderiliyor...';

        try {

            const res = await fetch(`/messages/share/${currentPostId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    user_ids: Array.from(selectedUsers),
                    note: noteInput.value
                })
            });

            if (!res.ok) {
                throw new Error("Paylaşım başarısız.");
            }

            // BAŞARILI → MODALI KAPAT
            closeModal();

        } catch (err) {

            console.error(err);
            alert("Paylaşım sırasında hata oluştu.");

        } finally {

            updateSubmitButton();

        }

    });

})();