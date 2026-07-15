// Kaydet butonu — optimistic UI ile AJAX toggle. Kaydediliyse doğrudan sil,
// kaydedilmemişse klasör seçici popover aç (collection_id ile POST body'de gönder).

(function () {
    var collectionsCache = null;
    var currentPopover = null;

    function csrfHeader() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    async function fetchCollections() {
        if (collectionsCache) return collectionsCache;
        try {
            var res = await fetch('/social/collections', {
                method: 'GET',
                headers: { 'X-Requested-With': 'fetch' },
            });
            if (!res.ok) throw new Error('Koleksiyonlar alınamadı');
            collectionsCache = await res.json();
            return collectionsCache;
        } catch (err) {
            console.error('Koleksiyonlar yüklenemedi:', err);
            return { collections: [] };
        }
    }

    function closePopover() {
        if (currentPopover) {
            currentPopover.remove();
            currentPopover = null;
        }
    }

    function escapeHtml(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    document.addEventListener('click', async function (e) {
        var btn = e.target.closest('.bookmark-btn');
        if (!btn) return;

        var isBookmarked = btn.dataset.bookmarked === '1';

        // Kaydedilmişse: doğrudan toggle (sil)
        if (isBookmarked) {
            e.preventDefault();
            if (btn.dataset.busy === '1') return;

            var wasBookmarked = true;
            btn.dataset.bookmarked = '0';
            // İkon şekli sabit (bookmark), dolu/boş hâli sadece .bookmarked
            // class'ıyla CSS üzerinden yönetiliyor — textContent atamasına
            // gerek yok (öncesinde SVG'yi silip düz emoji metnine çeviriyordu).
            btn.classList.toggle('bookmarked', false);
            btn.dataset.busy = '1';

            try {
                var res = await fetch(btn.dataset.bookmarkUrl, {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'fetch',
                        'X-CSRF-Token': csrfHeader(),
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ collection_id: null }),
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                var data = await res.json();
                btn.dataset.bookmarked = data.bookmarked ? '1' : '0';
                btn.classList.toggle('bookmarked', data.bookmarked);
                btn.setAttribute('aria-label', data.bookmarked ? 'Kaydedilenlerden kaldır' : 'Kaydet');
            } catch (err) {
                btn.dataset.bookmarked = wasBookmarked ? '1' : '0';
                btn.classList.toggle('bookmarked', wasBookmarked);
                console.error('Kaydetme güncellenemedi:', err);
            } finally {
                btn.dataset.busy = '0';
            }
            return;
        }

        // Kaydedilmemişse: popover aç
        e.preventDefault();
        closePopover();

        var data = await fetchCollections();
        var collections = data.collections || [];

        var popover = document.createElement('div');
        popover.className = 'bookmark-picker';
        popover.setAttribute('role', 'menu');

        var html = '<button type="button" class="bookmark-picker-item" role="menuitem" data-collection-id="">' + (window.ICONS ? window.ICONS.get('folder', { size: 14 }) : '📁') + ' Genel</button>';
        collections.forEach(function (col) {
            html += '<button type="button" class="bookmark-picker-item" role="menuitem" data-collection-id="' + col.id + '">' + escapeHtml(col.name) + '</button>';
        });
        html += '<div class="bookmark-picker-new"><input type="text" placeholder="+ Yeni klasör" maxlength="40" class="bookmark-new-input"><button type="button" class="bookmark-new-btn" style="padding: 4px 8px; font-size: 12px;">Ekle</button></div>';

        popover.innerHTML = html;
        document.body.appendChild(popover);
        currentPopover = popover;

        var rect = btn.getBoundingClientRect();
        popover.style.position = 'fixed';
        popover.style.left = (rect.left) + 'px';
        popover.style.top = (rect.bottom + 8) + 'px';

        btn.setAttribute('aria-haspopup', 'menu');

        // --- Klasör seçimi ---
        popover.querySelectorAll('.bookmark-picker-item').forEach(function (item) {
            item.addEventListener('click', async function (e2) {
                e2.stopPropagation();
                var collectionId = item.dataset.collectionId || null;

                btn.dataset.busy = '1';
                try {
                    var res = await fetch(btn.dataset.bookmarkUrl, {
                        method: 'POST',
                        headers: {
                            'X-Requested-With': 'fetch',
                            'X-CSRF-Token': csrfHeader(),
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ collection_id: collectionId }),
                    });
                    if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
                    var resData = await res.json();
                    btn.dataset.bookmarked = resData.bookmarked ? '1' : '0';
                    btn.classList.toggle('bookmarked', resData.bookmarked);
                    btn.setAttribute('aria-label', resData.bookmarked ? 'Kaydedilenlerden kaldır' : 'Kaydet');
                    closePopover();
                } catch (err) {
                    console.error('Kaydetme başarısız:', err);
                } finally {
                    btn.dataset.busy = '0';
                }
            });
        });

        // --- Yeni klasör ---
        var newInput = popover.querySelector('.bookmark-new-input');
        var newBtn = popover.querySelector('.bookmark-new-btn');
        newBtn.addEventListener('click', async function (e2) {
            e2.stopPropagation();
            var name = newInput.value.trim();
            if (!name) return;

            try {
                var res = await fetch('/social/collections/new', {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'fetch',
                        'X-CSRF-Token': csrfHeader(),
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ name: name }),
                });
                if (!res.ok) throw new Error('Klasör oluşturulamadı');
                var resData = await res.json();
                newInput.value = '';

                collectionsCache.collections.push({ id: resData.id, name: resData.name });

                var newItem = document.createElement('button');
                newItem.type = 'button';
                newItem.className = 'bookmark-picker-item';
                newItem.setAttribute('role', 'menuitem');
                newItem.dataset.collectionId = resData.id;
                newItem.textContent = escapeHtml(resData.name);

                newItem.addEventListener('click', async function (e3) {
                    e3.stopPropagation();
                    var collectionId = resData.id;

                    btn.dataset.busy = '1';
                    try {
                        var res2 = await fetch(btn.dataset.bookmarkUrl, {
                            method: 'POST',
                            headers: {
                                'X-Requested-With': 'fetch',
                                'X-CSRF-Token': csrfHeader(),
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({ collection_id: collectionId }),
                        });
                        if (!res2.ok) throw new Error('İstek başarısız');
                        var resData2 = await res2.json();
                        btn.dataset.bookmarked = resData2.bookmarked ? '1' : '0';
                        btn.classList.toggle('bookmarked', resData2.bookmarked);
                        btn.setAttribute('aria-label', resData2.bookmarked ? 'Kaydedilenlerden kaldır' : 'Kaydet');
                        closePopover();
                    } catch (err) {
                        console.error('Kaydetme başarısız:', err);
                    } finally {
                        btn.dataset.busy = '0';
                    }
                });

                popover.insertBefore(newItem, popover.querySelector('.bookmark-picker-new'));
            } catch (err) {
                console.error('Klasör oluşturulamadı:', err);
            }
        });
    });

    // Popover dışına tıklanırsa / Escape ise kapat
    document.addEventListener('click', function (e) {
        if (currentPopover && !e.target.closest('.bookmark-picker') && !e.target.closest('.bookmark-btn')) {
            closePopover();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && currentPopover) {
            closePopover();
        }
    });
})();
