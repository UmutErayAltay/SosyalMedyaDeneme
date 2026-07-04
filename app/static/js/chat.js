// Sohbet: AJAX mesaj gönderme (optimistic UI) + Enter/Shift+Enter + görsel + Realtime
// NotebookLM standartları: aria-live, klavye erişilebilirliği, otomatik scroll

(function () {
    if (window.__chatJsInitialized) return;
    window.__chatJsInitialized = true;

    var form = document.getElementById('msg-form');
    var input = document.getElementById('msg-input');
    var stream = document.getElementById('stream');
    var imageInput = document.getElementById('msg-image-input');
    var imageName = document.getElementById('msg-image-name');
    if (!form || !input || !stream) return;

    function scrollToBottom() {
        stream.scrollTop = stream.scrollHeight;
    }
    scrollToBottom();

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
    imageInput.addEventListener('change', function () {
        var f = imageInput.files[0];
        imageName.textContent = f ? f.name : '';
    });

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

        if (msg.image_url) {
            html += '<div class="msg-image-wrapper' + (opts.uploading ? ' uploading' : '') + '">';
            html += '<img src="' + escapeHtml(msg.image_url) + '" class="msg-image" alt="Görsel mesaj" loading="lazy">';
            if (opts.uploading) {
                html += '<div class="upload-spinner" role="status" aria-label="Görsel yükleniyor"></div>';
            }
            html += '</div>';
        }
        if (msg.content) {
            html += '<p>' + escapeHtml(msg.content) + '</p>';
        }
        var time = msg.created_at ? msg.created_at.substring(11, 16) : 'şimdi';
        html += '<span class="time">' + time + '</span></div>';
        return html;
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

    // --- Görsele tıklayınca büyütme (lightbox) ---
    var lightbox = document.getElementById('lightbox-overlay');
    if (!lightbox) {
        lightbox = document.createElement('div');
        lightbox.id = 'lightbox-overlay';
        lightbox.className = 'lightbox-overlay hidden';
        lightbox.innerHTML = '<img alt="Büyütülmüş görsel">';
        document.body.appendChild(lightbox);
    }
    var lightboxImg = lightbox.querySelector('img');

    function openLightbox(src) {
        lightboxImg.src = src;
        lightbox.classList.remove('hidden');
    }
    function closeLightbox() {
        lightbox.classList.add('hidden');
        lightboxImg.src = '';
    }
    lightbox.addEventListener('click', closeLightbox);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeLightbox();
    });
    stream.addEventListener('click', function (e) {
        var img = e.target.closest('.msg-image');
        if (img && !img.closest('.uploading')) openLightbox(img.src);
    });

    // --- Form submit: AJAX + optimistic UI (görsel dahil) ---
    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        var content = input.value.trim();
        var hasImage = imageInput.files.length > 0;
        if (!content && !hasImage) return;

        var submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;

        var tempId = 'temp-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        var localImageUrl = hasImage ? URL.createObjectURL(imageInput.files[0]) : null;

        // Optimistic UI: mesaj + görsel (varsa spinner ile) anında göster
        var node = appendMessage(
            { content: content, image_url: localImageUrl, created_at: null },
            true,
            { tempId: tempId, uploading: hasImage }
        );

        input.value = '';
        input.style.height = 'auto';
        imageName.textContent = '';

        try {
            var formData = new FormData(form);
            if (!hasImage) formData.delete('image');

            var res = await fetch(window.SEND_URL, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData,
            });
            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);

            var saved = await res.json();

            // Optimistic node'u gerçek veriyle güncelle (spinner kalksın, gerçek URL gelsin)
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

            imageInput.value = '';
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

    // --- Supabase Realtime: sadece KARŞI TARAFIN mesajlarını al ---
    // (kendi mesajlarımızı zaten optimistic UI + fetch cevabıyla güncelliyoruz)
    if (window.supabaseClient) {
        try {
            var sb = window.supabaseClient;
            var topic = 'messages:' + window.CONVERSATION_ID;

            sb.getChannels().forEach(function (ch) {
                if (ch.topic === 'realtime:' + topic) sb.removeChannel(ch);
            });

            var channel = sb.channel(topic);
            channel.on('postgres_changes', {
                event: 'INSERT',
                schema: 'public',
                table: 'messages',
                filter: 'conversation_id=eq.' + window.CONVERSATION_ID
            }, function (payload) {
                var msg = payload.new;
                var isMine = msg.sender_id === window.ME_ID;
                if (!isMine) appendMessage(msg, isMine);
            }).subscribe(function (status) {
                if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                    console.warn('Realtime bağlantı sorunu, durum:', status);
                }
            });
        } catch (err) {
            console.warn('Realtime başlatılamadı, polling fallback aktif:', err);
            setInterval(function () { location.reload(); }, 5000);
        }
    } else {
        setInterval(function () {
            if (document.visibilityState === 'visible') location.reload();
        }, 10000);
    }
})();