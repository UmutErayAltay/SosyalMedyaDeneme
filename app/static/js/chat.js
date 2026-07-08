// Sohbet: AJAX mesaj gönderme (optimistic UI) + Enter/Shift+Enter + görsel + Realtime
// NotebookLM standartları: aria-live, klavye erişilebilirliği, otomatik scroll
//
// window.initConversation() dışa açık ve TEKRAR ÇAĞRILABİLİR: hem ilk sayfa
// yüklemesinde hem de messagesPanel.js konuşmalar arasında AJAX ile geçiş
// yaptığında (tam sayfa yenilemeden) yeniden çalıştırılır. Bu yüzden burada
// eski Supabase Realtime kanalı her seferinde kapatılıp yenisi açılır.

(function () {
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    var REACT_EMOJIS = ['❤️', '😂', '👍', '😮', '😢', '🔥'];

    function reactUrlFor(msgId) {
        return '/messages/message/' + msgId + '/react';
    }

    function buildMessageHtml(msg, isMine, opts) {
        opts = opts || {};
        var cls = isMine ? 'mine' : 'theirs';
        var tempAttr = opts.tempId ? ' data-temp-id="' + opts.tempId + '"' : '';
        var reactAttr = msg.id ? ' data-react-url="' + reactUrlFor(msg.id) + '"' : '';
        var html = '<div class="msg ' + cls + '" data-msg-id="' + (msg.id || '') + '"' + tempAttr + reactAttr + '>';

        if (opts.senderName) {
            html += '<span class="msg-sender">' + escapeHtml(opts.senderName) + '</span>';
        }
        if (msg.sticker && msg.sticker.image_url) {
            // Sunucu render'ıyla (_conversation_panel.html) birebir aynı yapı:
            // optimistic balon aksi halde küçük düz görsel olarak görünüyor,
            // yıldız (kaydet) butonu ancak sayfa yenilenince geliyordu.
            html += '<div class="sticker-wrap">';
            html += '<img src="' + escapeHtml(msg.sticker.image_url) + '" class="sticker-rendered msg-sticker"'
                + (msg.sticker.id ? ' data-sticker-id="' + escapeHtml(msg.sticker.id) + '"' : '')
                + ' alt="Sticker">';
            if (msg.sticker.id) {
                html += '<button type="button" class="sticker-star-btn" data-sticker-id="'
                    + escapeHtml(msg.sticker.id) + '" aria-label="Sticker&#39;ı kaydet">⭐</button>';
            }
            html += '</div>';
        } else if (msg.image_url) {
            html += '<div class="msg-image-wrapper' + (opts.uploading ? ' uploading' : '') + '">';
            html += '<img src="' + escapeHtml(msg.image_url) + '" class="msg-image" alt="Görsel mesaj" loading="lazy">';
            if (opts.uploading) {
                html += '<div class="upload-spinner" role="status" aria-label="Görsel yükleniyor"></div>';
            }
            html += '</div>';
        }
        if (msg.audio_url) {
            html += '<audio src="' + escapeHtml(msg.audio_url) + '" class="msg-audio" controls></audio>';
        }
        if (msg.content) {
            html += '<p>' + escapeHtml(msg.content) + '</p>';
        }
        var time = msg.created_at ? msg.created_at.substring(11, 16) : 'şimdi';
        html += '<span class="time">' + time;
        if (isMine) {
            var read = !!msg.read_at;
            html += ' <span class="read-receipt' + (read ? ' read' : '') + '" aria-label="' + (read ? 'Okundu' : 'İletildi') + '">'
                + (read ? '✓✓' : '✓') + '</span>';
        }
        html += '</span>';
        // Sunucu render'ındaki tepki tetikleyicisi + picker — bunlar olmadan
        // yeni gönderilen/gelen mesajlara sayfa yenilenmeden tepki verilemezdi
        html += '<button class="msg-react-trigger" aria-label="Emoji tepkisi ekle" type="button">🙂+</button>';
        html += '<div class="msg-react-picker" hidden>';
        REACT_EMOJIS.forEach(function (em) {
            html += '<button type="button" data-emoji="' + em + '" aria-label="' + em + ' tepkisi">' + em + '</button>';
        });
        html += '</div>';
        html += '</div>';
        return html;
    }

    // Paylaşılan post mesajlarını kart görünümüne çevirir. p.innerHTML kasıtlı
    // kullanılıyor: Jinja {{ m.content }} sunucuda escape ettiği için innerHTML
    // her zaman entity-encoded metin döner; bu metni yeniden innerHTML'e
    // yazınca escape durumu korunur (XSS'e karşı güvenli round-trip).
    function formatSharedPosts(stream) {
        stream.querySelectorAll('.msg').forEach(function (msgDiv) {
            var p = msgDiv.querySelector('p');
            if (!p || p.dataset.formatted) return;

            var text = p.innerHTML;

            if (text.includes('📎 Paylaşılan post:')) {
                var parts = text.split('📎 Paylaşılan post: ');
                var note = parts[0].trim();
                var rest = parts[1] || '';
                var lines = rest.split('\n').map(function (l) { return l.trim(); }).filter(function (l) { return l !== ''; });

                var postUrl = lines[0] || '#';
                var authorLineIndex = lines.length - 1;
                var author = lines[authorLineIndex] || '';
                if (author.startsWith('—')) author = author.replace('—', '').trim();

                var postContent = lines.slice(1, authorLineIndex).join(' ').replace(/^"|"$/g, '').trim();

                var cardHtml = ''
                    + (note ? '<div class="share-note">' + note.replace(/\n/g, '<br>') + '</div>' : '')
                    + '<a href="' + postUrl + '" class="shared-card">'
                    + '<div class="shared-card-header"><strong>' + author + '</strong></div>'
                    + '<div class="shared-card-body">'
                    + '<p>' + (postContent || 'Görsel gönderisi') + '</p>'
                    + '<span class="shared-card-btn">Gönderiyi Gör</span>'
                    + '</div></a>';

                p.innerHTML = cardHtml;
                p.style.margin = '0';

                var img = msgDiv.querySelector('img.msg-image');
                if (img) {
                    img.classList.remove('msg-image');
                    img.classList.add('shared-card-img');
                    var body = p.querySelector('.shared-card-body');
                    if (body) body.insertAdjacentElement('afterbegin', img);
                }
            } else {
                p.innerHTML = text.replace(/\n/g, '<br>');
            }

            p.dataset.formatted = 'true';
        });
    }

    // --- Lightbox: tek seferlik kurulum (sayfa boyunca tek bir overlay yeter) ---
    var lightbox = null;
    var lightboxImg = null;
    function ensureLightbox() {
        if (lightbox) return;
        lightbox = document.getElementById('lightbox-overlay');
        if (!lightbox) {
            lightbox = document.createElement('div');
            lightbox.id = 'lightbox-overlay';
            lightbox.className = 'lightbox-overlay hidden';
            lightbox.innerHTML = '<img alt="Büyütülmüş görsel">';
            document.body.appendChild(lightbox);
        }
        lightboxImg = lightbox.querySelector('img');

        function closeLightbox() {
            lightbox.classList.add('hidden');
            lightboxImg.src = '';
        }
        lightbox.addEventListener('click', closeLightbox);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeLightbox();
        });
        // Event delegation: her yeni #stream için ayrı listener gerekmez
        document.addEventListener('click', function (e) {
            var img = e.target.closest('.msg-image');
            if (img && !img.closest('.uploading') && img.closest('#stream')) {
                lightboxImg.src = img.src;
                lightbox.classList.remove('hidden');
            }
        });
    }

    // --- Aktif Realtime kanalı: konuşmalar arası geçişte kapatılıp yeniden açılır ---
    var activeChannel = null;

    window.initConversation = function () {
        var panel = document.getElementById('conversation-panel');
        var form = document.getElementById('msg-form');
        var input = document.getElementById('msg-input');
        var stream = document.getElementById('stream');
        var imageInput = document.getElementById('msg-image-input');
        var imageName = document.getElementById('msg-image-name');
        var gifToggleBtn = document.getElementById('gif-toggle-msg-btn');
        var gifPickerPanel = document.getElementById('gif-picker-msg-panel');
        var gifSearchInput = document.getElementById('gif-search-msg-input');
        var gifResults = document.getElementById('gif-results-msg');
        var gifLoadingMsg = document.getElementById('gif-loading-msg-msg');
        var gifUrlInput = form ? form.querySelector('input[name="gif_url"]') : null;
        if (!panel || !form || !input || !stream) return;

        var conversationId = panel.dataset.conversationId;
        var sendUrl = panel.dataset.sendUrl;
        var otherUsername = panel.dataset.otherUsername || 'Kullanıcı';
        var isGroup = panel.dataset.isGroup === '1';
        var memberMap = {};
        try { memberMap = JSON.parse(panel.dataset.memberMap || '{}'); } catch (err) { memberMap = {}; }
        if (!conversationId || !sendUrl) return;

        ensureLightbox();

        function scrollToBottom() {
            stream.scrollTop = stream.scrollHeight;
        }
        scrollToBottom();
        formatSharedPosts(stream);

        var observer = new MutationObserver(function () { formatSharedPosts(stream); });
        observer.observe(stream, { childList: true, subtree: true });

        // --- Ekleme menüsü açma/kapama ---
        var attachMenuBtn = document.getElementById('msg-attach-menu-btn');
        var attachMenu = document.getElementById('msg-attach-menu');

        function closeAttachMenu() {
            if (!attachMenu || attachMenu.hidden) return;
            attachMenu.hidden = true;
            if (attachMenuBtn) attachMenuBtn.setAttribute('aria-expanded', 'false');
        }

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

            // Menü öğelerine tıklayınca kapat (GIF/Sticker picker açılırken menü kapanır)
            attachMenu.addEventListener('click', function (e) {
                if (e.target.closest('.attach-menu-item')) closeAttachMenu();
            });

            // ESC tuşu
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && !attachMenu.hidden) closeAttachMenu();
            });
        }

        // --- Enter = gönder, Shift+Enter = yeni satır (WCAG 2.1.1) ---
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                form.requestSubmit();
            }
        });

        // Textarea otomatik büyüme
        input.addEventListener('input', function () {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        });

        // --- Görsel seçimi ---
        if (imageInput && imageName) {
            imageInput.addEventListener('change', function () {
                var f = imageInput.files[0];
                imageName.textContent = f ? f.name : '';
            });
        }

        // --- Sürükle-bırak görsel yükleme (tıklanabilir dosya seçici zaten var — WCAG
        // 2.5.7 tek-imleçli alternatif olarak korunuyor, sürükleme sadece ek kolaylık) ---
        var dropHint = document.getElementById('drop-hint');
        if (imageInput) {
            ['dragover', 'dragenter'].forEach(function (evt) {
                panel.addEventListener(evt, function (e) {
                    e.preventDefault();
                    panel.classList.add('drag-over');
                });
            });
            ['dragleave', 'dragend'].forEach(function (evt) {
                panel.addEventListener(evt, function () {
                    panel.classList.remove('drag-over');
                });
            });
            panel.addEventListener('drop', function (e) {
                e.preventDefault();
                panel.classList.remove('drag-over');
                var files = e.dataTransfer && e.dataTransfer.files;
                if (!files || !files.length) return;
                var file = files[0];
                if (!file.type.startsWith('image/')) {
                    if (dropHint) dropHint.textContent = 'Sadece görsel dosyaları desteklenir.';
                    return;
                }
                var dt = new DataTransfer();
                dt.items.add(file);
                imageInput.files = dt.files;
                imageInput.dispatchEvent(new Event('change'));
                if (dropHint) dropHint.textContent = file.name + ' eklendi.';
            });
        }

        // --- "Yazıyor..." göstergesi: broadcast ile (DB'ye yazılmaz), throttle'lı gönderim ---
        var typingIndicator = document.getElementById('typing-indicator');
        var typingClearTimer = null;
        var lastTypingSentAt = 0;

        var myUsername = panel.dataset.myUsername || 'Sen';

        function sendTyping(isTyping) {
            if (!activeChannel) return;
            var now = Date.now();
            if (isTyping && now - lastTypingSentAt < 2000) return; // en fazla 2sn'de bir gönder
            lastTypingSentAt = isTyping ? now : 0;
            activeChannel.send({
                type: 'broadcast', event: 'typing',
                payload: { typing: isTyping, username: myUsername },
            });
        }

        if (typingIndicator) {
            input.addEventListener('input', function () { sendTyping(true); });
            input.addEventListener('blur', function () { sendTyping(false); });
        }

        function appendMessage(msg, isMine, opts) {
            var empty = stream.querySelector('.muted.center');
            if (empty) empty.remove();

            // Duplikat kontrolü (realtime + optimistic çakışması)
            if (msg.id && stream.querySelector('[data-msg-id="' + msg.id + '"]')) return null;

            stream.insertAdjacentHTML('beforeend', buildMessageHtml(msg, isMine, opts));
            scrollToBottom();
            return stream.lastElementChild;
        }

        // --- Sesli mesaj kaydı (MediaRecorder API) — SADECE tarayıcı destekliyorsa
        // gösterilir (progressive enhancement). Kayıt/önizleme AYRI bir çubukta
        // değil, doğrudan mesaj yazma kutusunun yerinde görünür; gönderme TEK
        // "Gönder" butonuyla olur (submit handler'ı aşağıda, recordedBlob'a bakar);
        // "Sil" butonu SADECE bir kayıt hazırken Gönder'in yanında belirir.
        var voiceBtn = document.getElementById('voice-record-btn');
        var voiceDiscardBtn = document.getElementById('voice-discard-btn');
        var voiceRecordingStatus = document.getElementById('voice-recording-status');
        var voicePreviewAudio = document.getElementById('voice-preview-audio');
        var recordedVoiceBlob = null;

        function formatElapsed(ms) {
            var totalSec = Math.floor(ms / 1000);
            var m = Math.floor(totalSec / 60);
            var s = totalSec % 60;
            return m + ':' + (s < 10 ? '0' : '') + s;
        }

        if (voiceBtn && voiceRecordingStatus && voicePreviewAudio
                && window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            voiceBtn.hidden = false;

            var mediaRecorder = null;
            var recordedChunks = [];
            var mediaStream = null;
            var recordingStartedAt = 0;
            var recordingTimer = null;

            function resetVoiceUI() {
                input.hidden = false;
                voiceRecordingStatus.hidden = true;
                voiceRecordingStatus.textContent = '';
                voicePreviewAudio.hidden = true;
                voicePreviewAudio.removeAttribute('src');
                voiceDiscardBtn.hidden = true;
                voiceBtn.hidden = false;
                voiceBtn.textContent = '🎤';
                voiceBtn.classList.remove('recording');
                recordedVoiceBlob = null;
                recordedChunks = [];
                if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
            }

            async function startRecording() {
                try {
                    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                } catch (err) {
                    alert('Mikrofon izni verilmedi.');
                    return;
                }
                recordedChunks = [];
                mediaRecorder = new MediaRecorder(mediaStream);
                mediaRecorder.ondataavailable = function (e) {
                    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
                };
                mediaRecorder.onstop = function () {
                    recordedVoiceBlob = new Blob(recordedChunks, { type: 'audio/webm' });
                    mediaStream.getTracks().forEach(function (t) { t.stop(); });
                    voiceRecordingStatus.hidden = true;
                    voicePreviewAudio.src = URL.createObjectURL(recordedVoiceBlob);
                    voicePreviewAudio.hidden = false;
                    voiceDiscardBtn.hidden = false;
                    voiceBtn.hidden = true;
                    if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
                };
                mediaRecorder.start();
                recordingStartedAt = Date.now();
                input.hidden = true;
                voiceRecordingStatus.hidden = false;
                voiceRecordingStatus.textContent = '🔴 Kaydediliyor... 0:00';
                voiceBtn.textContent = '⏹';
                voiceBtn.classList.add('recording');
                recordingTimer = setInterval(function () {
                    voiceRecordingStatus.textContent = '🔴 Kaydediliyor... ' + formatElapsed(Date.now() - recordingStartedAt);
                }, 500);
            }

            voiceBtn.addEventListener('click', function () {
                if (mediaRecorder && mediaRecorder.state === 'recording') {
                    mediaRecorder.stop();
                } else {
                    startRecording();
                }
            });

            voiceDiscardBtn.addEventListener('click', resetVoiceUI);
        }

        // --- GIF Toggle ve Search ---
        if (gifToggleBtn && gifPickerPanel) {
            gifToggleBtn.addEventListener('click', function (e) {
                e.preventDefault();
                if (gifPickerPanel.hidden) {
                    gifPickerPanel.hidden = false;
                    if (gifSearchInput) gifSearchInput.focus();
                    // İlk açılışta trending GIF'leri fetch et
                    if (!gifResults.innerHTML) {
                        searchGifsMsg('');
                    }
                } else {
                    gifPickerPanel.hidden = true;
                }
            });
        }

        function searchGifsMsg(q) {
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
                            selectGifMsg(gif.url);
                        });
                        gifResults.appendChild(img);
                    });
                })
                .catch(function (e) {
                    gifLoadingMsg.hidden = true;
                    gifResults.innerHTML = '<p class="muted center">Hata: ' + e.message + '</p>';
                });
        }

        function selectGifMsg(url) {
            if (gifUrlInput) gifUrlInput.value = url;
            gifPickerPanel.hidden = true;
            form.requestSubmit();
        }

        if (gifSearchInput) {
            var gifSearchTimer = null;
            gifSearchInput.addEventListener('input', function () {
                var q = this.value;
                clearTimeout(gifSearchTimer);
                gifSearchTimer = setTimeout(function () { searchGifsMsg(q); }, 300);
            });
        }

        // --- Form submit: AJAX + optimistic UI (görsel VEYA sesli kayıt) ---
        form.addEventListener('submit', async function (e) {
            e.preventDefault();

            // Bekleyen bir ses kaydı varsa (Sil butonu görünürken) TEK Gönder
            // butonu onu gönderir — metin/görsel akışından tamamen ayrı bir yol.
            if (recordedVoiceBlob) {
                var blobToSend = recordedVoiceBlob;
                var submitBtnVoice = form.querySelector('button[type="submit"]');
                submitBtnVoice.disabled = true;
                if (voiceDiscardBtn) voiceDiscardBtn.disabled = true;
                try {
                    var voiceFormData = new FormData();
                    voiceFormData.append('audio', blobToSend, 'voice-message.webm');
                    var csrfInput = form.querySelector('input[name="csrf_token"]');
                    voiceFormData.append('csrf_token', csrfInput ? csrfInput.value : '');

                    var voiceRes = await fetch(sendUrl, {
                        method: 'POST',
                        headers: { 'Accept': 'application/json' },
                        body: voiceFormData,
                    });
                    if (!voiceRes.ok) throw new Error('İstek başarısız: ' + voiceRes.status);
                    var savedVoice = await voiceRes.json();
                    appendMessage(savedVoice, true);
                } catch (err) {
                    console.error('Sesli mesaj gönderilemedi:', err);
                    alert('Sesli mesaj gönderilemedi.');
                } finally {
                    submitBtnVoice.disabled = false;
                    if (voiceDiscardBtn) voiceDiscardBtn.disabled = false;
                    if (typeof resetVoiceUI === 'function') resetVoiceUI();
                    input.focus();
                }
                return;
            }

            var content = input.value.trim();
            var hasImage = imageInput && imageInput.files.length > 0;
            var stickerIdInput = form.querySelector('input[name="sticker_id"]');
            var stickerVal = stickerIdInput ? stickerIdInput.value : '';
            var gifVal = gifUrlInput ? gifUrlInput.value : '';
            // Sticker/GIF seçiliyken metin boş olabilir
            if (!content && !hasImage && !stickerVal && !gifVal) return;

            sendTyping(false);
            var submitBtn = form.querySelector('button[type="submit"]');
            submitBtn.disabled = true;

            var tempId = 'temp-' + Date.now() + '-' + Math.random().toString(36).slice(2);
            // Optimistic balonda GIF görsel olarak, sticker ise sunucu
            // render'ıyla aynı sticker-wrap yapısıyla gösterilir
            var localImageUrl = hasImage ? URL.createObjectURL(imageInput.files[0]) : (gifVal || null);
            var optimisticMsg = { content: content, image_url: localImageUrl, created_at: null };
            if (stickerVal && stickerIdInput.dataset.imageUrl) {
                optimisticMsg.sticker = { id: stickerVal, image_url: stickerIdInput.dataset.imageUrl };
            }

            var node = appendMessage(
                optimisticMsg,
                true,
                { tempId: tempId, uploading: hasImage }
            );

            var sentContent = content;
            input.value = '';
            input.style.height = 'auto';
            if (imageName) imageName.textContent = '';

            try {
                var formData = new FormData(form);
                formData.set('content', sentContent);
                if (!hasImage) formData.delete('image');

                var res = await fetch(sendUrl, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: formData,
                });
                if (!res.ok) throw new Error('İstek başarısız: ' + res.status);

                var saved = await res.json();

                if (node) {
                    node.dataset.msgId = saved.id;
                    node.dataset.reactUrl = reactUrlFor(saved.id);
                    var wrapper = node.querySelector('.msg-image-wrapper');
                    if (wrapper) {
                        wrapper.classList.remove('uploading');
                        var spinner = wrapper.querySelector('.upload-spinner');
                        if (spinner) spinner.remove();
                        var img = wrapper.querySelector('.msg-image');
                        if (img && saved.image_url) img.src = saved.image_url;
                    }
                    var timeEl = node.querySelector('.time');
                    if (timeEl && saved.created_at) {
                        timeEl.textContent = saved.created_at.substring(11, 16);
                    }
                }

                if (imageInput) imageInput.value = '';
                // Sticker/GIF seçimleri tek kullanımlık — temizlenmezse sonraki
                // her mesaj aynı sticker'ı/GIF'i tekrar gönderir
                if (stickerIdInput) {
                    stickerIdInput.value = '';
                    delete stickerIdInput.dataset.imageUrl;
                }
                if (gifUrlInput) gifUrlInput.value = '';
            } catch (err) {
                console.error('Mesaj gönderilemedi:', err);
                if (node) node.remove();
                alert('Mesaj gönderilemedi.');
            } finally {
                if (localImageUrl) URL.revokeObjectURL(localImageUrl);
                submitBtn.disabled = false;
                input.focus();
            }
        });

        // --- Supabase Realtime: önceki kanalı kapat, yeni konuşmaya abone ol ---
        if (activeChannel && window.supabaseClient) {
            try { window.supabaseClient.removeChannel(activeChannel); } catch (err) { /* yut */ }
            activeChannel = null;
        }

        if (window.supabaseClient) {
            try {
                var topic = 'messages:' + conversationId;
                // self:false — kendi "yazıyor" broadcast'imizi kendimize geri göstermeyelim
                var channel = window.supabaseClient.channel(topic, {
                    config: { broadcast: { self: false } }
                });
                channel.on('postgres_changes', {
                    event: 'INSERT',
                    schema: 'public',
                    table: 'messages',
                    filter: 'conversation_id=eq.' + conversationId
                }, function (payload) {
                    var msg = payload.new;
                    var isMine = msg.sender_id === window.ME_ID;
                    if (!isMine) {
                        var senderName = isGroup ? (memberMap[msg.sender_id] || 'Bilinmeyen') : null;
                        appendMessage(msg, isMine, { senderName: senderName });
                    }
                }).on('postgres_changes', {
                    event: 'UPDATE',
                    schema: 'public',
                    table: 'messages',
                    filter: 'conversation_id=eq.' + conversationId
                }, function (payload) {
                    // Karşı taraf mesajımızı okudu — checkmark'ı canlı güncelle
                    var msg = payload.new;
                    if (msg.sender_id !== window.ME_ID || !msg.read_at) return;
                    var el = stream.querySelector('[data-msg-id="' + msg.id + '"]');
                    var receipt = el && el.querySelector('.read-receipt');
                    if (receipt) {
                        receipt.textContent = '✓✓';
                        receipt.classList.add('read');
                        receipt.setAttribute('aria-label', 'Okundu');
                    }
                }).on('broadcast', { event: 'typing' }, function (msg) {
                    if (!typingIndicator) return;
                    var payload = msg.payload || {};
                    if (typingClearTimer) clearTimeout(typingClearTimer);
                    if (payload.typing) {
                        if (typingIndicator.hidden) {
                            // Grup sohbetinde KİMİN yazdığını göstermek gerekir (sabit
                            // "otherUsername" bir grupta anlamsız olurdu) — payload'daki
                            // gönderen adı kullanılır, 1:1'de de tutarlı çalışır.
                            typingIndicator.textContent = (payload.username || otherUsername) + ' yazıyor...';
                            typingIndicator.hidden = false;
                        }
                        typingClearTimer = setTimeout(function () {
                            typingIndicator.hidden = true;
                            typingIndicator.textContent = '';
                        }, 4000);
                    } else {
                        typingIndicator.hidden = true;
                        typingIndicator.textContent = '';
                    }
                }).subscribe(function (status) {
                    if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                        console.warn('Realtime bağlantı sorunu, durum:', status);
                    }
                });
                activeChannel = channel;

                // WebRTC arama sistemi: call.js'i başlat
                if (window.initCallSystem && !isGroup) {
                    window.initCallSystem(conversationId, window.ME_ID, activeChannel, panel.dataset.otherUserId || '');
                }
            } catch (err) {
                console.warn('Realtime başlatılamadı:', err);
            }
        }
    };

    // === Mesaj Tepkileri (Emoji Reactions) — Document Delegation ===
    // Picker tetikleme, emoji seçimi, chip tıklaması (panel AJAX geçişlerinden bağımsız)

    function closeAllReactPickers(except) {
        document.querySelectorAll('.msg-react-picker:not([hidden])').forEach(function (p) {
            if (p !== except) p.setAttribute('hidden', '');
        });
    }

    function setChipCount(chip, count) {
        if (count <= 0) {
            chip.remove();
            return;
        }
        chip.dataset.count = count;
        chip.textContent = chip.dataset.reaction + ' ' + count;
        chip.title = count + ' kişi';
    }

    // Sunucu yanıtına göre chip'leri günceller. newReaction = benim aktif
    // tepkim (null → kaldırıldı). Backend "farklı emojiye geçiş"te eski
    // tepkiyi silip yenisini yazar — eski "mine" chip'i de burada düşürülür,
    // yoksa mesajda iki emojiyi birden atmışım gibi görünür (kullanıcı raporu).
    function applyReactionResult(msg, emoji, newReaction) {
        if (newReaction) {
            msg.querySelectorAll('.msg-reaction-chip.mine').forEach(function (old) {
                if (old.dataset.reaction !== newReaction) {
                    old.classList.remove('mine');
                    setChipCount(old, parseInt(old.dataset.count || 1, 10) - 1);
                }
            });
            var chip = msg.querySelector('.msg-reaction-chip[data-reaction="' + newReaction + '"]');
            if (!chip) {
                var reactionsDiv = msg.querySelector('.msg-reactions');
                if (!reactionsDiv) {
                    reactionsDiv = document.createElement('div');
                    reactionsDiv.className = 'msg-reactions';
                    var timeEl = msg.querySelector('.time');
                    if (timeEl) timeEl.insertAdjacentElement('afterend', reactionsDiv);
                    else msg.appendChild(reactionsDiv);
                }
                chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'msg-reaction-chip mine';
                chip.dataset.reaction = newReaction;
                reactionsDiv.appendChild(chip);
                setChipCount(chip, 1);
            } else if (!chip.classList.contains('mine')) {
                chip.classList.add('mine');
                setChipCount(chip, parseInt(chip.dataset.count || 1, 10) + 1);
            }
        } else {
            var removed = msg.querySelector('.msg-reaction-chip[data-reaction="' + emoji + '"]');
            if (removed) {
                removed.classList.remove('mine');
                setChipCount(removed, parseInt(removed.dataset.count || 1, 10) - 1);
            }
        }
    }

    function sendReaction(msg, emoji) {
        var reactUrl = msg.dataset.reactUrl;
        if (!msg.dataset.msgId || !reactUrl) return;

        var csrfInput = document.querySelector('input[name="csrf_token"]');
        fetch(reactUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfInput ? csrfInput.value : ''
            },
            body: JSON.stringify({ reaction: emoji })
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (!data.ok) return;
            applyReactionResult(msg, emoji, data.reaction);
        })
        .catch(function (err) {
            console.error('Tepki gönderilemedi:', err);
        });
    }

    document.addEventListener('click', function (e) {
        var panel = e.target.closest('#conversation-panel');
        if (!panel) {
            // Panel dışına tıklandı — açık picker'ları kapat
            closeAllReactPickers();
            return;
        }

        // 1. Tetik butonu: picker açma/kapama (aynı anda tek picker açık kalır)
        var trigger = e.target.closest('.msg-react-trigger');
        if (trigger) {
            e.preventDefault();
            var msg = trigger.closest('.msg');
            if (msg) {
                var picker = msg.querySelector('.msg-react-picker');
                if (picker) {
                    if (picker.hasAttribute('hidden')) {
                        closeAllReactPickers(picker);
                        // Varsayılan balonun ÜSTÜNDE açılır (son mesajda alta
                        // açılınca stream dışına taşıyordu) — mesaj stream'in
                        // görünür üst kenarına yakınsa alta açılır.
                        var streamEl = document.getElementById('stream');
                        picker.classList.remove('below');
                        if (streamEl) {
                            var msgTop = msg.getBoundingClientRect().top;
                            var streamTop = streamEl.getBoundingClientRect().top;
                            if (msgTop - streamTop < 48) picker.classList.add('below');
                        }
                        picker.removeAttribute('hidden');
                    } else {
                        picker.setAttribute('hidden', '');
                    }
                }
            }
            return;
        }

        // 2. Emoji seçimi (picker içindeki emoji butonları)
        var emojiBtn = e.target.closest('.msg-react-picker button[data-emoji]');
        if (emojiBtn) {
            e.preventDefault();
            var msgEl = emojiBtn.closest('.msg');
            var pickerEl = emojiBtn.closest('.msg-react-picker');
            if (msgEl && pickerEl) {
                pickerEl.setAttribute('hidden', '');
                sendReaction(msgEl, emojiBtn.dataset.emoji);
            }
            return;
        }

        // 3. Chip tıklaması: o emojiyi toggle et (benimse kaldır, değilse ekle/geç)
        var chip = e.target.closest('.msg-reaction-chip');
        if (chip && !chip.closest('.msg-react-picker')) {
            e.preventDefault();
            var chipMsg = chip.closest('.msg');
            if (chipMsg) sendReaction(chipMsg, chip.dataset.reaction);
            return;
        }

        // Panel içinde ama picker/tetik dışında bir yere tıklandı — kapat
        closeAllReactPickers();
    });

    // Escape tuşu — açık tepki picker'larını kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAllReactPickers();
    });

    // İlk sayfa yüklemesinde çalıştır (AJAX geçişlerinde messagesPanel.js çağırır)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.initConversation);
    } else {
        window.initConversation();
    }
})();
