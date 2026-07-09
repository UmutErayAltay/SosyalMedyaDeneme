// Çıkartmalar (Sticker) modülü — paylaşılan picker, document-level delegation
// - Popover tabanlı picker: [data-sticker-picker-btn] tıklanınca açılır
// - Lazy yükleme: /stickers/mine GET, cache'ler, save/remove/new sonrası invalidate
// - Seçim sonrası: data-sticker-autosubmit ise formu gönder, değilse önizleme göster
// - Yıldızla kaydetme: .sticker-rendered görsellerde hover'da ⭐ butonu

(function () {
    var cache = null;
    var cacheTimer = null;

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function invalidateCache() {
        cache = null;
        if (cacheTimer) clearTimeout(cacheTimer);
    }

    function fetchStickers(callback) {
        if (cache) {
            callback(cache);
            return;
        }

        fetch('/stickers/mine')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                cache = data.stickers || [];
                // Cache 5 dakika geçerli
                cacheTimer = setTimeout(invalidateCache, 5 * 60 * 1000);
                callback(cache);
            })
            .catch(function (e) {
                console.error('Sticker yüklenemedi:', e);
                callback([]);
            });
    }

    function renderStickerPicker(btn) {
        // Buton bir ekleme menüsünün (attach-menu) İÇİNDEYSE picker menünün
        // DIŞINA konur — menü öğe tıklamasında kapandığı için picker menü
        // içinde kalırsa onunla birlikte gizlenirdi (sohbet composer'ı)
        var host = btn.closest('.attach-menu-wrap') || btn;
        var existing = host.parentNode.querySelector('.sticker-picker');
        if (existing) {
            existing.remove();
            return;
        }

        var pickerDiv = document.createElement('div');
        pickerDiv.className = 'sticker-picker';
        pickerDiv._triggerBtn = btn; // seçim handler'ı tetikleyen butonu bulabilsin
        pickerDiv.innerHTML = '<p class="muted">Yükleniyor...</p>';
        host.parentNode.insertBefore(pickerDiv, host.nextSibling);

        fetchStickers(function (stickers) {
            pickerDiv.innerHTML = '<div class="sticker-grid">';
            var grid = pickerDiv.querySelector('.sticker-grid');

            if (stickers && stickers.length > 0) {
                stickers.forEach(function (s) {
                    var wrap = document.createElement('div');
                    wrap.className = 'sticker-item-wrap';
                    wrap.innerHTML = '<div class="sticker-item" data-sticker-id="' + s.id + '">' +
                        '<img src="' + escapeHtml(s.image_url) + '" alt="Sticker" class="sticker-item-img">' +
                        (s.mine_created ? '<button type="button" class="sticker-delete-btn" aria-label="Sil">🗑</button>' : '') +
                        '</div>';
                    grid.appendChild(wrap);
                });
            }

            // Upload butonu
            var uploadWrap = document.createElement('div');
            uploadWrap.className = 'sticker-item-wrap sticker-upload-wrap';
            uploadWrap.innerHTML = '<button type="button" class="sticker-upload-btn" aria-label="Sticker yükle">➕ Yükle</button>' +
                '<input type="file" name="sticker-file" accept="image/*" hidden>';
            grid.appendChild(uploadWrap);

            pickerDiv.appendChild(grid);
        });
    }

    // Picker dışına tıklayınca kapatma (document-level)
    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-sticker-picker-btn]') || e.target.closest('.sticker-picker')) {
            // Buton veya picker içinde — işlem yapma
            return;
        }
        // Diğer yerde tıklandı — açık picker'ları kapat
        var openPickers = document.querySelectorAll('.sticker-picker');
        openPickers.forEach(function (p) { p.remove(); });
    });

    // Escape tuşu — açık picker'ları kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            var openPickers = document.querySelectorAll('.sticker-picker');
            openPickers.forEach(function (p) { p.remove(); });
        }
    });

    // Document-level delegation: sticker picker açma/kapama
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-sticker-picker-btn]');
        if (!btn) return;

        e.preventDefault();
        renderStickerPicker(btn);
    });

    // Sticker seçimi
    document.addEventListener('click', function (e) {
        var itemDiv = e.target.closest('.sticker-item');
        if (!itemDiv) return;

        var pickerId = e.target.closest('.sticker-picker');
        if (!pickerId) return;

        e.preventDefault();
        var stickerId = itemDiv.dataset.stickerId;
        var btn = pickerId._triggerBtn || pickerId.previousElementSibling;

        // Form'u bul (button'ın en yakın form parent'ı)
        var form = btn.closest('form');
        if (!form) return;

        // Hidden input'ları doldur (+ optimistic balon için görsel URL'i dataset'e)
        var stickerIdInput = form.querySelector('input[name="sticker_id"]');
        if (stickerIdInput) {
            stickerIdInput.value = stickerId;
            var imgEl = itemDiv.querySelector('img');
            if (imgEl) stickerIdInput.dataset.imageUrl = imgEl.src;
        }

        // Autosubmit?
        if (btn.dataset.stickerAutosubmit === '1') {
            form.requestSubmit();
            pickerId.remove();
        } else {
            // Önizleme: sticker preview container'ını göster
            var previewArea = form.querySelector('[data-sticker-preview]');
            if (!previewArea) {
                previewArea = document.createElement('div');
                previewArea.className = 'sticker-preview-area';
                previewArea.dataset.stickerPreview = '1';
                form.appendChild(previewArea);
            }

            var sticker = null;
            fetchStickers(function (stickers) {
                sticker = stickers.find(function (s) { return s.id === stickerId; });
                if (sticker) {
                    previewArea.innerHTML = '<div class="sticker-preview-item">' +
                        '<img src="' + escapeHtml(sticker.image_url) + '" alt="Sticker">' +
                        '<button type="button" class="sticker-preview-remove" aria-label="Kaldır">×</button>' +
                        '</div>';
                }
            });

            pickerId.remove();
        }
    });

    // Sticker silme (upload panel'de)
    document.addEventListener('click', function (e) {
        var deleteBtn = e.target.closest('.sticker-delete-btn');
        if (!deleteBtn) return;

        e.preventDefault();
        var itemDiv = deleteBtn.closest('.sticker-item');
        var stickerId = itemDiv.dataset.stickerId;

        if (!stickerId) return;

        fetch('/stickers/' + stickerId + '/remove', {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken() }
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    invalidateCache();
                    itemDiv.parentNode.remove();
                }
            })
            .catch(function (e) { console.error('Sticker silinemedi:', e); });
    });

    // Preview'da kaldır
    document.addEventListener('click', function (e) {
        var removeBtn = e.target.closest('.sticker-preview-remove');
        if (!removeBtn) return;

        e.preventDefault();
        var form = removeBtn.closest('form');
        var previewArea = form.querySelector('[data-sticker-preview]');
        var stickerIdInput = form.querySelector('input[name="sticker_id"]');

        if (previewArea) previewArea.remove();
        if (stickerIdInput) stickerIdInput.value = '';
    });

    // Yeni sticker yükle (file input'a tıklanınca)
    document.addEventListener('change', function (e) {
        var fileInput = e.target;
        if (fileInput.name !== 'sticker-file') return;

        var file = fileInput.files[0];
        if (!file) return;

        var formData = new FormData();
        formData.append('image', file);

        fetch('/stickers/new', {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken() },
            body: formData
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.id) {
                    invalidateCache();
                    // Picker'ı yeniden render et
                    var picker = fileInput.closest('.sticker-picker');
                    var btn = picker._triggerBtn || picker.previousElementSibling;
                    picker.remove();
                    renderStickerPicker(btn);
                }
            })
            .catch(function (e) { console.error('Sticker yüklenemedi:', e); });
    });

    // Upload butonu tıklaması
    document.addEventListener('click', function (e) {
        var uploadBtn = e.target.closest('.sticker-upload-btn');
        if (!uploadBtn) return;

        e.preventDefault();
        var fileInput = uploadBtn.parentNode.querySelector('input[name="sticker-file"]');
        if (fileInput) fileInput.click();
    });

    // --- Yıldızla kaydetme (Rendered sticker'lar) ---
    document.addEventListener('click', function (e) {
        var starBtn = e.target.closest('.sticker-star-btn');
        if (!starBtn) return;

        e.preventDefault();
        var stickerId = starBtn.dataset.stickerId;
        if (!stickerId) return;

        fetch('/stickers/' + stickerId + '/save', {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken() }
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    starBtn.textContent = '✅';
                    starBtn.disabled = true;
                    setTimeout(function () {
                        starBtn.textContent = '⭐';
                        starBtn.disabled = false;
                    }, 1500);
                }
            })
            .catch(function (e) { console.error('Sticker kaydedilemedi:', e); });
    });

    // Profil "Çıkartmalarım" sekmesinin grid'i profile.html içindeki inline
    // script tarafından (tab tıklanınca, lazy) dolduruluyor — TEK kaynak.
    // Burada eskiden ikinci bir renderer daha vardı (DOMContentLoaded'da
    // hemen çalışan), aynı container'a `.sticker-grid` sarmalayıcısı OLMADAN
    // yazıyordu; bu yüzden stickerlar grid'e oturmuyor, tam genişlikte akıp
    // devasa görünüyordu. Çakışan iki kaynağı önlemek için kaldırıldı (bkz.
    // Sprint 65 notu, active_context.md).
})();
