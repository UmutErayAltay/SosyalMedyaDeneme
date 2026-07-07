// Kaydedilenler klasörleri — filtreleme (client-side, sunucuya istek yok),
// yeni klasör oluşturma, klasör silme, bir kaydı klasöre taşıma. Hepsi AJAX
// (follow.js/bookmarks.js ile aynı desen: optimistic olmayan ama sayfa
// yenilemesiz güncelleme).

(function () {
    function csrfHeader() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function escapeHtml(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // --- Klasöre göre filtrele: "Tümü" / "Genel" / belirli bir klasör ---
    var collectionBar = document.querySelector('.collection-bar');
    if (collectionBar) {
        collectionBar.addEventListener('click', function (e) {
            var chip = e.target.closest('.collection-chip');
            if (!chip) return;
            collectionBar.querySelectorAll('.collection-chip').forEach(function (c) {
                c.classList.toggle('active', c === chip);
            });
            var filter = chip.dataset.collectionFilter;
            document.querySelectorAll('.bookmark-item').forEach(function (item) {
                if (filter === '') {
                    item.hidden = false;
                } else if (filter === 'none') {
                    item.hidden = !!item.dataset.collectionId;
                } else {
                    item.hidden = item.dataset.collectionId !== filter;
                }
            });
        });
    }

    // --- Yeni klasör oluştur ---
    var newForm = document.getElementById('new-collection-form');
    if (newForm) {
        newForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var input = newForm.querySelector('input[name="name"]');
            var name = input.value.trim();
            if (!name) return;
            try {
                var res = await fetch(newForm.action, {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'fetch',
                        'X-CSRF-Token': csrfHeader(),
                    },
                    body: new URLSearchParams({ name: name }),
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                var data = await res.json();
                input.value = '';

                var wrap = document.createElement('span');
                wrap.className = 'collection-chip-wrap';
                var safeName = escapeHtml(data.name);
                // newForm.action = ".../social/collections/new" — aynı prefix'i
                // koruyarak silme URL'ini türet (JS'te url_for yok).
                var deleteUrl = newForm.action.replace(/\/new$/, '/' + data.id + '/delete');
                wrap.innerHTML =
                    '<button type="button" class="collection-chip" data-collection-filter="' + data.id + '">' + safeName + '</button>' +
                    '<button type="button" class="collection-delete-btn" data-collection-id="' + data.id + '" data-delete-url="' + deleteUrl + '" aria-label="' + safeName + ' klasörünü sil" title="Klasörü sil">×</button>';
                newForm.parentElement.insertBefore(wrap, newForm);
            } catch (err) {
                console.error('Klasör oluşturulamadı:', err);
            }
        });
    }

    // --- Klasör sil (içindeki kayıtlar "Genel"e döner) ---
    document.addEventListener('click', async function (e) {
        var delBtn = e.target.closest('.collection-delete-btn');
        if (!delBtn) return;
        if (!window.confirm('Bu klasörü silmek istiyor musun? İçindeki kayıtlar "Genel"e taşınır.')) return;

        var collectionId = delBtn.dataset.collectionId;
        try {
            var res = await fetch(delBtn.dataset.deleteUrl, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'fetch',
                    'X-CSRF-Token': csrfHeader(),
                },
            });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);

            var wrap = delBtn.closest('.collection-chip-wrap');
            if (wrap) wrap.remove();
            document.querySelectorAll('.bookmark-item[data-collection-id="' + collectionId + '"]').forEach(function (item) {
                item.dataset.collectionId = '';
            });
        } catch (err) {
            console.error('Klasör silinemedi:', err);
        }
    });
})();
