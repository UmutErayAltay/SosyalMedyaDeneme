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

        function appendMessage(msg, isMine, opts) {
            var empty = stream.querySelector('.muted.center');
            if (empty) empty.remove();

            // Duplikat kontrolü (realtime + optimistic çakışması)
            if (msg.id && stream.querySelector('[data-msg-id="' + msg.id + '"]')) return null;

            stream.insertAdjacentHTML('beforeend', buildMessageHtml(msg, isMine, opts));
            scrollToBottom();
            return stream.lastElementChild;
        }

        // --- Form submit: AJAX + optimistic UI (görsel dahil) ---
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var content = input.value.trim();
            var hasImage = imageInput && imageInput.files.length > 0;
            if (!content && !hasImage) return;

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
                var channel = window.supabaseClient.channel(topic);
                channel.on('postgres_changes', {
                    event: 'INSERT',
                    schema: 'public',
                    table: 'messages',
                    filter: 'conversation_id=eq.' + conversationId
                }, function (payload) {
                    var msg = payload.new;
                    var isMine = msg.sender_id === window.ME_ID;
                    if (!isMine) appendMessage(msg, isMine);
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

    // İlk sayfa yüklemesinde çalıştır (AJAX geçişlerinde messagesPanel.js çağırır)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.initConversation);
    } else {
        window.initConversation();
    }
})();
