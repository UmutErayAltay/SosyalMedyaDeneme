// Post paylaşma modalı — erişilebilir (focus trap, ESC, overlay click)
// Çoklu görsel önizleme + planlama + GIF seçici + konum

(function () {
    var modal = document.getElementById('post-modal');
    var modalBox = modal ? modal.querySelector('.modal') : null;
    var openBtn = document.getElementById('open-post-modal');
    var closeBtn = document.getElementById('close-post-modal');
    var fileInput = document.getElementById('post-image-input');
    var previewGrid = document.getElementById('post-image-preview');
    var videoInput = document.getElementById('post-video-input');
    var videoPreview = document.getElementById('post-video-preview');
    var pollToggleBtn = document.getElementById('poll-toggle-btn');
    var pollContainer = document.getElementById('poll-options-container');
    var pollAddOptionBtn = document.getElementById('poll-add-option-btn');
    var pollCancelBtn = document.getElementById('poll-cancel-btn');
    var attachMenuBtn = document.getElementById('attach-menu-btn');
    var attachMenu = document.getElementById('attach-menu');
    var scheduleModalBtn = document.getElementById('schedule-modal-btn');
    var scheduleRow = document.getElementById('schedule-row');
    var scheduleInput = document.getElementById('schedule-input');
    var scheduleConfirmBtn = document.getElementById('schedule-confirm-btn');
    var scheduleCancelBtn = document.getElementById('schedule-cancel-btn');
    var scheduleActionInput = document.getElementById('schedule-action-input');
    var scheduledAtInput = document.getElementById('scheduled-at-input');
    var gifToggleBtn = document.getElementById('gif-toggle-btn');
    var gifPickerPanel = document.getElementById('gif-picker-panel');
    var gifSearchInput = document.getElementById('gif-search-input');
    var gifResults = document.getElementById('gif-results');
    var gifLoadingMsg = document.getElementById('gif-loading-msg');
    var gifUrlInput = document.getElementById('gif-url-input');
    var gifPreviewWrap = document.getElementById('gif-preview-wrap');
    var gifPreviewImg = document.getElementById('gif-preview-img');
    var gifRemoveBtn = document.getElementById('gif-remove-btn');
    var locationToggleBtn = document.getElementById('location-toggle-btn');
    var locationBadgeWrap = document.getElementById('location-badge-wrap');
    var locationNameDisplay = document.getElementById('location-name-display');
    var locationRemoveBtn = document.getElementById('location-remove-btn');
    var locationLatInput = document.getElementById('location-lat-input');
    var locationLngInput = document.getElementById('location-lng-input');
    var locationNameInput = document.getElementById('location-name-input');
    var postVisibilityWrap = document.querySelector('.post-visibility-wrap');
    var postVisibilityBtn = document.getElementById('post-visibility-btn');
    var postVisibilityMenu = document.getElementById('post-visibility-menu');
    var postVisibilityBtnContent = document.getElementById('post-visibility-btn-content');
    var postVisibilityInput = document.getElementById('post-visibility-input');
    if (!modal || !openBtn) return;

    var lastFocused = null;
    var scheduledTime = null;
    var selectedLocation = null;

    function closeAttachMenu() {
        if (!attachMenu || attachMenu.hidden) return;
        attachMenu.hidden = true;
        if (attachMenuBtn) attachMenuBtn.setAttribute('aria-expanded', 'false');
    }

    function open() {
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        // Focus'u modal içine taşı
        setTimeout(function () {
            var ta = modal.querySelector('textarea');
            if (ta) ta.focus();
        }, 50);
    }

    function close() {
        modal.hidden = true;
        document.body.style.overflow = '';
        if (lastFocused) lastFocused.focus();
        if (pollContainer && !pollContainer.hidden) resetPollUI();
        closeAttachMenu();
        resetScheduleUI();
        resetGifPanel();
        resetPostVisibilityUI();
    }

    // --- Görünürlük dropdown'ı — native <select>'in emoji option'ları SVG
    // ikona hiç çevrilemediği için (kullanıcı raporu) attach-menu ile AYNI
    // özel dropdown deseni. Varsayılan sunucu tarafında hesabın gizlilik
    // durumuna göre hesaplanıp `.post-visibility-wrap[data-default-value]`'a
    // yazılıyor (gizli hesap -> followers, açık hesap -> public) — JS bunu
    // sabit kodlamak yerine DOM'dan okur (bkz. app/routes/posts.py my_is_private). ---
    function closePostVisibilityMenu() {
        if (!postVisibilityMenu || postVisibilityMenu.hidden) return;
        postVisibilityMenu.hidden = true;
        if (postVisibilityBtn) postVisibilityBtn.setAttribute('aria-expanded', 'false');
    }

    function resetPostVisibilityUI() {
        if (!postVisibilityMenu) return;
        var defaultValue = (postVisibilityWrap && postVisibilityWrap.dataset.defaultValue) || 'public';
        var defaultItem = postVisibilityMenu.querySelector('.post-visibility-item[data-value="' + defaultValue + '"]');
        if (defaultItem && postVisibilityBtnContent) postVisibilityBtnContent.innerHTML = defaultItem.innerHTML;
        postVisibilityMenu.querySelectorAll('.post-visibility-item').forEach(function (i) {
            i.classList.toggle('selected', i.dataset.value === defaultValue);
        });
        if (postVisibilityInput) postVisibilityInput.value = defaultValue;
        closePostVisibilityMenu();
    }

    if (postVisibilityBtn && postVisibilityMenu) {
        postVisibilityBtn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var willOpen = postVisibilityMenu.hidden;
            postVisibilityMenu.hidden = !willOpen;
            postVisibilityBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        });

        document.addEventListener('click', function (e) {
            var item = e.target.closest('.post-visibility-item');
            if (item && !postVisibilityMenu.hidden) {
                e.preventDefault();
                if (postVisibilityInput) postVisibilityInput.value = item.dataset.value;
                if (postVisibilityBtnContent) postVisibilityBtnContent.innerHTML = item.innerHTML;
                postVisibilityMenu.querySelectorAll('.post-visibility-item').forEach(function (i) {
                    i.classList.remove('selected');
                });
                item.classList.add('selected');
                closePostVisibilityMenu();
                return;
            }
            if (!postVisibilityMenu.hidden && !postVisibilityMenu.contains(e.target) && e.target !== postVisibilityBtn) {
                closePostVisibilityMenu();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !postVisibilityMenu.hidden) closePostVisibilityMenu();
        });
    }

    // Sadece satırı gizler — onaylanmış planı SİLMEZ (onay sonrası çağrılır)
    function hideScheduleRow() {
        if (scheduleRow) scheduleRow.hidden = true;
        if (scheduleInput) scheduleInput.value = '';
    }

    // Tam sıfırlama: hidden input'lardaki name/değer de temizlenir (modal kapanışı)
    function resetScheduleUI() {
        hideScheduleRow();
        scheduledTime = null;
        if (scheduleActionInput) {
            scheduleActionInput.value = '';
            scheduleActionInput.removeAttribute('name');
        }
        if (scheduledAtInput) scheduledAtInput.value = '';
        if (scheduleModalBtn) scheduleModalBtn.hidden = false;
    }

    function resetGifPanel() {
        if (!gifPickerPanel) return;
        gifPickerPanel.hidden = true;
        if (gifSearchInput) gifSearchInput.value = '';
        if (gifResults) gifResults.innerHTML = '';
    }

    // --- "Ekle" (⋯) menüsü: görsel/video/anket seçenekleri artık her zaman
    // görünen 3 ayrı buton yerine tek bir açılır menüde toplanıyor. ---
    if (attachMenuBtn && attachMenu) {
        attachMenuBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (attachMenu.hidden) {
                attachMenu.hidden = false;
                attachMenuBtn.setAttribute('aria-expanded', 'true');
            } else {
                closeAttachMenu();
            }
        });

        // Dışarıya tıklayınca kapat
        document.addEventListener('click', function (e) {
            if (!attachMenu.hidden && !attachMenu.contains(e.target) && e.target !== attachMenuBtn) {
                closeAttachMenu();
            }
        });

        // Bir seçeneğe tıklayınca (görsel/video seç, anket ekle) menü kapanır
        attachMenu.addEventListener('click', function (e) {
            if (e.target.closest('.attach-menu-item')) closeAttachMenu();
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !attachMenu.hidden) closeAttachMenu();
        });
    }

    openBtn.addEventListener('click', open);
    if (closeBtn) closeBtn.addEventListener('click', close);

    // Overlay'e tıklayınca kapat
    modal.addEventListener('click', function (e) {
        if (e.target === modal) close();
    });

    // ESC ile kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) close();
    });

    // Focus trap — modal açıkken Tab modal içinde kalır
    modal.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        var focusable = modal.querySelectorAll('button, textarea, input, a[href]');
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

    // --- Çoklu görsel önizleme ---
    if (fileInput && previewGrid) {
        fileInput.addEventListener('change', function (e) {
            previewGrid.innerHTML = '';
            var files = e.target.files;
            var count = Math.min(files.length, 4);
            for (var i = 0; i < count; i++) {
                (function (file) {
                    var reader = new FileReader();
                    reader.onload = function (ev) {
                        var wrap = document.createElement('div');
                        wrap.className = 'image-preview-item';
                        wrap.innerHTML = '<img src="' + ev.target.result + '" alt="Önizleme">';
                        previewGrid.appendChild(wrap);
                    };
                    reader.readAsDataURL(file);
                })(files[i]);
            }
            if (files.length > 4) {
                var note = document.createElement('p');
                note.className = 'muted';
                note.textContent = 'İlk 4 görsel yüklenecek.';
                previewGrid.appendChild(note);
            }
        });
    }

    // --- Video ekle: görsel, video ve anket artık AYNI postta birlikte
    // eklenebilir (kullanıcı isteğiyle mutual-exclusive kısıtlama kaldırıldı,
    // backend de aynı şekilde routes.create_post()'ta güncellendi). ---
    if (videoInput && videoPreview) {
        var isReelLabel = document.getElementById('is-reel-label');
        var isReelCheckbox = document.getElementById('is-reel-checkbox');
        videoInput.addEventListener('change', function (e) {
            var file = e.target.files[0];
            if (!file) {
                videoPreview.style.display = 'none';
                videoPreview.removeAttribute('src');
                if (isReelLabel) isReelLabel.hidden = true;
                if (isReelCheckbox) isReelCheckbox.checked = false;
                return;
            }
            videoPreview.src = URL.createObjectURL(file);
            videoPreview.style.display = 'block';
            // Reel toggle'ı SADECE video seçiliyken görünür — reel video
            // gerektirir (backend routes.create_post() da bunu zorunlu kılar).
            if (isReelLabel) isReelLabel.hidden = false;
        });
    }

    // --- Anket ekle ---
    function resetPollUI() {
        if (!pollContainer) return;
        pollContainer.hidden = true;
        if (pollToggleBtn) pollToggleBtn.hidden = false;
        pollContainer.querySelectorAll('input').forEach(function (inp, i) {
            inp.value = '';
            if (i >= 2) inp.hidden = true;
        });
        if (pollAddOptionBtn) pollAddOptionBtn.hidden = false;
    }

    if (pollToggleBtn && pollContainer) {
        pollToggleBtn.addEventListener('click', function () {
            pollContainer.hidden = false;
            pollToggleBtn.hidden = true;
            var firstInput = pollContainer.querySelector('input');
            if (firstInput) firstInput.focus();
        });
    }

    if (pollAddOptionBtn && pollContainer) {
        pollAddOptionBtn.addEventListener('click', function () {
            var hiddenInputs = pollContainer.querySelectorAll('input[hidden]');
            if (hiddenInputs.length === 0) return;
            hiddenInputs[0].hidden = false;
            hiddenInputs[0].focus();
            if (hiddenInputs.length === 1) pollAddOptionBtn.hidden = true; // 4 seçenek doldu
        });
    }

    if (pollCancelBtn) {
        pollCancelBtn.addEventListener('click', resetPollUI);
    }

    // --- Planlama ---
    if (scheduleModalBtn && scheduleRow) {
        scheduleModalBtn.addEventListener('click', function (e) {
            e.preventDefault();
            scheduleRow.hidden = false;
            if (scheduleInput) {
                var now = new Date();
                // datetime-local YEREL saat bekler; toISOString UTC döndürdüğü
                // için offset düşülmeden min UTC+3'te 3 saat geriye kayar
                var minTime = new Date(now.getTime() + 60000 - now.getTimezoneOffset() * 60000);
                scheduleInput.min = minTime.toISOString().slice(0, 16);
                scheduleInput.focus();
            }
        });
    }

    if (scheduleConfirmBtn && scheduleInput) {
        scheduleConfirmBtn.addEventListener('click', function () {
            var val = scheduleInput.value;
            if (!val) return;
            var dt = new Date(val);
            var now = new Date();
            if (dt <= now) {
                alert('Geçmiş bir tarih seçemezsin.');
                return;
            }
            scheduledTime = dt.toISOString();
            if (scheduleActionInput) {
                scheduleActionInput.setAttribute('name', 'action');
                scheduleActionInput.value = 'schedule';
            }
            if (scheduledAtInput) scheduledAtInput.value = scheduledTime;
            alert('Post ' + val.replace('T', ' ') + ' tarihine planlandı. "Paylaş" ile onayla.');
            hideScheduleRow();
            if (scheduleModalBtn) scheduleModalBtn.hidden = true;
        });
    }

    if (scheduleCancelBtn) {
        scheduleCancelBtn.addEventListener('click', resetScheduleUI);
    }

    // --- GIF Seçici ---
    if (gifToggleBtn && gifPickerPanel) {
        gifToggleBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (gifPickerPanel.hidden) {
                gifPickerPanel.hidden = false;
                if (gifSearchInput) gifSearchInput.focus();
                // İlk açılışta trending GIF'leri fetch et
                if (!gifResults.innerHTML) {
                    searchGifs('');
                }
            } else {
                resetGifPanel();
            }
        });
    }

    function searchGifs(q) {
        if (!gifLoadingMsg || !gifResults) return;
        gifLoadingMsg.hidden = false;
        gifResults.innerHTML = '';
        fetch('/gif/search?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                gifLoadingMsg.hidden = true;
                if (data.disabled) {
                    gifPickerPanel.innerHTML = '<p class="muted center">GIF servisi şu anda kullanılamıyor.</p>';
                    gifToggleBtn.hidden = true;
                    return;
                }
                if (!data.gifs || data.gifs.length === 0) {
                    gifResults.innerHTML = '<p class="muted center">Sonuç bulunamadı.</p>';
                    return;
                }
                data.gifs.forEach(function (gif) {
                    var img = document.createElement('img');
                    img.src = gif.preview || gif.url;
                    img.alt = 'GIF';
                    img.className = 'gif-picker-img';
                    img.addEventListener('click', function () {
                        selectGif(gif.url);
                    });
                    gifResults.appendChild(img);
                });
            })
            .catch(function (e) {
                gifLoadingMsg.hidden = true;
                gifResults.innerHTML = '<p class="muted center">Hata: ' + e.message + '</p>';
            });
    }

    function selectGif(url) {
        if (gifUrlInput) gifUrlInput.value = url;
        if (gifPreviewImg) gifPreviewImg.src = url;
        if (gifPreviewWrap) gifPreviewWrap.hidden = false;
        // Görsel/Video input'larını temizle (backend gif + dosya kabul etmiyor)
        if (fileInput) {
            fileInput.value = '';
            previewGrid.innerHTML = '';
        }
        if (videoInput) {
            videoInput.value = '';
            videoPreview.style.display = 'none';
            videoPreview.removeAttribute('src');
            var isReelLabel = document.getElementById('is-reel-label');
            var isReelCheckbox = document.getElementById('is-reel-checkbox');
            if (isReelLabel) isReelLabel.hidden = true;
            if (isReelCheckbox) isReelCheckbox.checked = false;
        }
        resetGifPanel();
    }

    if (gifSearchInput) {
        var gifSearchTimer = null;
        gifSearchInput.addEventListener('input', function () {
            var q = this.value;
            clearTimeout(gifSearchTimer);
            // Her tuş vuruşunda Tenor proxy'sine istek gitmesin
            gifSearchTimer = setTimeout(function () { searchGifs(q); }, 300);
        });
    }

    if (gifRemoveBtn && gifPreviewWrap) {
        gifRemoveBtn.addEventListener('click', function () {
            if (gifUrlInput) gifUrlInput.value = '';
            if (gifPreviewImg) gifPreviewImg.src = '';
            gifPreviewWrap.hidden = true;
        });
    }

    // --- Konum ---
    // Önceden konum adı için HER ZAMAN manuel prompt() isteniyordu — bu hem
    // ekstra bir adımdı hem de bazı tarayıcılarda/ortamlarda prompt() engellenip
    // sessizce "konum eklenmedi" izlenimi verebiliyordu (kullanıcı raporu:
    // "konumu direkt çekmeyi denemek istiyorum"). Artık koordinat alınır
    // alınmaz OpenStreetMap Nominatim ile TERS GEOCODING otomatik denenir
    // (gerçek yer adı — mahalle/cadde/işletme); sadece bu başarısız olursa
    // (ağ hatası, API kullanılamıyor) manuel isim için prompt()'a düşülür.
    async function reverseGeocode(lat, lng) {
        try {
            var res = await fetch(
                'https://nominatim.openstreetmap.org/reverse?format=json&lat=' + lat +
                '&lon=' + lng + '&zoom=16&addressdetails=1'
            );
            if (!res.ok) return null;
            var data = await res.json();
            var addr = data.address || {};
            return addr.amenity || addr.shop || addr.building || addr.road ||
                addr.neighbourhood || addr.suburb || addr.town || addr.city ||
                (data.display_name ? data.display_name.split(',')[0] : null);
        } catch (err) {
            return null;
        }
    }

    if (locationToggleBtn) {
        locationToggleBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (!navigator.geolocation) {
                alert('Tarayıcında Konum özelliği yok.');
                return;
            }
            var originalLabel = locationToggleBtn.textContent;
            locationToggleBtn.disabled = true;
            locationToggleBtn.textContent = 'Konum alınıyor...';
            navigator.geolocation.getCurrentPosition(
                async function (pos) {
                    var lat = pos.coords.latitude;
                    var lng = pos.coords.longitude;
                    var name = await reverseGeocode(lat, lng);
                    if (!name) {
                        name = prompt('Konum adı (örn: Kahveci, Park):');
                    }
                    locationToggleBtn.disabled = false;
                    locationToggleBtn.textContent = originalLabel;
                    if (!name) return;
                    if (locationLatInput) locationLatInput.value = lat;
                    if (locationLngInput) locationLngInput.value = lng;
                    if (locationNameInput) locationNameInput.value = name;
                    if (locationNameDisplay) locationNameDisplay.textContent = name;
                    if (locationBadgeWrap) locationBadgeWrap.hidden = false;
                    selectedLocation = { lat: lat, lng: lng, name: name };
                    closeAttachMenu();
                },
                function (err) {
                    locationToggleBtn.disabled = false;
                    locationToggleBtn.textContent = originalLabel;
                    alert('Konum alınamadı: ' + err.message);
                }
            );
        });
    }

    if (locationRemoveBtn && locationBadgeWrap) {
        locationRemoveBtn.addEventListener('click', function () {
            if (locationLatInput) locationLatInput.value = '';
            if (locationLngInput) locationLngInput.value = '';
            if (locationNameInput) locationNameInput.value = '';
            locationBadgeWrap.hidden = true;
            selectedLocation = null;
        });
    }

    // --- Sürükle-bırak görsel yükleme (tıklanabilir "Görsel Ekle" her zaman
    // alternatif olarak duruyor — WCAG 2.5.7 tek-imleçli alternatif) ---
    if (fileInput && modalBox) {
        ['dragover', 'dragenter'].forEach(function (evt) {
            modalBox.addEventListener(evt, function (e) {
                e.preventDefault();
                modalBox.classList.add('drag-over');
            });
        });
        ['dragleave', 'dragend'].forEach(function (evt) {
            modalBox.addEventListener(evt, function () {
                modalBox.classList.remove('drag-over');
            });
        });
        modalBox.addEventListener('drop', function (e) {
            e.preventDefault();
            modalBox.classList.remove('drag-over');
            var dropped = e.dataTransfer && e.dataTransfer.files;
            if (!dropped || !dropped.length) return;

            var dt = new DataTransfer();
            var count = 0;
            for (var i = 0; i < dropped.length && count < 4; i++) {
                if (dropped[i].type.startsWith('image/')) {
                    dt.items.add(dropped[i]);
                    count++;
                }
            }
            if (count === 0) return;
            fileInput.files = dt.files;
            fileInput.dispatchEvent(new Event('change'));
        });
    }
})();
