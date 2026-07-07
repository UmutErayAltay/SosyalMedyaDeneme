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

    function buildMessageHtml(msg, isMine, opts) {
        opts = opts || {};
        var cls = isMine ? 'mine' : 'theirs';
        var tempAttr = opts.tempId ? ' data-temp-id="' + opts.tempId + '"' : '';
        var html = '<div class="msg ' + cls + '" data-msg-id="' + (msg.id || '') + '"' + tempAttr + '>';

        if (opts.senderName) {
            html += '<span class="msg-sender">' + escapeHtml(opts.senderName) + '</span>';
        }
        if (msg.image_url) {
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
        html += '</span></div>';
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
            if (!content && !hasImage) return;

            sendTyping(false);
            var submitBtn = form.querySelector('button[type="submit"]');
            submitBtn.disabled = true;

            var tempId = 'temp-' + Date.now() + '-' + Math.random().toString(36).slice(2);
            var localImageUrl = hasImage ? URL.createObjectURL(imageInput.files[0]) : null;

            var node = appendMessage(
                { content: content, image_url: localImageUrl, created_at: null },
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
            } catch (err) {
                console.warn('Realtime başlatılamadı:', err);
            }
        }
    };

    // === Mesaj Tepkileri (Emoji Reactions) — Document Delegation ===
    // Picker tetikleme, emoji seçimi, chip tıklaması (panel AJAX geçişlerinden bağımsız)
    document.addEventListener('click', function (e) {
        var panel = e.target.closest('#conversation-panel');
        if (!panel) return; // yalnız messaging panel içindeki tıklamalar işlenir

        // 1. Tetik butonu: picker açma/kapama
        var trigger = e.target.closest('.msg-react-trigger');
        if (trigger) {
            e.preventDefault();
            var msg = trigger.closest('.msg');
            if (msg) {
                var picker = msg.querySelector('.msg-react-picker');
                if (picker) {
                    if (picker.hasAttribute('hidden')) {
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
            var msg = emojiBtn.closest('.msg');
            var picker = emojiBtn.closest('.msg-react-picker');
            if (msg && picker) {
                var msgId = msg.dataset.msgId;
                var emoji = emojiBtn.dataset.emoji;
                var reactUrl = msg.dataset.reactUrl;
                picker.setAttribute('hidden', '');

                if (!msgId || !reactUrl) return;

                // POST /messages/message/<msgId>/react
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

                    var reactionsDiv = msg.querySelector('.msg-reactions');
                    var chip = msg.querySelector('.msg-reaction-chip[data-reaction="' + emoji + '"]');

                    if (data.reaction) {
                        // Tepki eklendi: yeni chip veya sayı arttır
                        if (!chip) {
                            if (!reactionsDiv) {
                                reactionsDiv = document.createElement('div');
                                reactionsDiv.className = 'msg-reactions';
                                // time span'ından sonra ekle
                                var timeEl = msg.querySelector('.time');
                                if (timeEl) timeEl.insertAdjacentElement('afterend', reactionsDiv);
                                else msg.appendChild(reactionsDiv);
                            }
                            chip = document.createElement('button');
                            chip.type = 'button';
                            chip.className = 'msg-reaction-chip mine';
                            chip.dataset.reaction = emoji;
                            chip.dataset.count = 1;
                            chip.textContent = emoji + ' 1';
                            chip.title = '1 kişi';
                            reactionsDiv.appendChild(chip);
                        } else {
                            chip.classList.add('mine');
                            var count = parseInt(chip.dataset.count || 1, 10);
                            count += 1;
                            chip.dataset.count = count;
                            chip.textContent = emoji + ' ' + count;
                            chip.title = count + ' kişi';
                        }
                    } else {
                        // Tepki kaldırıldı: sayı azalt veya chip'i sil
                        if (chip) {
                            var count = parseInt(chip.dataset.count || 1, 10);
                            count -= 1;
                            if (count <= 0) {
                                chip.remove();
                            } else {
                                chip.classList.remove('mine');
                                chip.dataset.count = count;
                                chip.textContent = emoji + ' ' + count;
                                chip.title = count + ' kişi';
                            }
                        }
                    }
                })
                .catch(function (err) {
                    console.error('Tepki gönderilemedi:', err);
                });
            }
            return;
        }

        // 3. Chip tıklaması: kendi tepkimi toggle et (hızlı tıkla)
        var chip = e.target.closest('.msg-reaction-chip');
        if (chip && !chip.closest('.msg-react-picker')) {
            e.preventDefault();
            var msg = chip.closest('.msg');
            if (msg && chip.classList.contains('mine')) {
                var emoji = chip.dataset.reaction;
                var reactUrl = msg.dataset.reactUrl;
                var msgId = msg.dataset.msgId;

                if (!msgId || !reactUrl) return;

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
                .catch(function (err) {
                    console.error('Tepki toggle başarısız:', err);
                });
            }
            return;
        }
    });

    // İlk sayfa yüklemesinde çalıştır (AJAX geçişlerinde messagesPanel.js çağırır)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.initConversation);
    } else {
        window.initConversation();
    }
})();
