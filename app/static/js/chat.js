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

    // Karşı taraftan Realtime ile gelen INSERT payload'ı ham satırdır (JOIN
    // yok) — sticker_id varsa ama sticker objesi yoksa görsel URL'i ayrı bir
    // istekle çözülür (bkz. stickers.py get_sticker). Konuşmalar arası
    // paylaşılan cache: aynı sticker birden fazla mesajda kullanılırsa tekrar
    // istek atılmaz. Kullanıcı raporu: önceden bu panel yenilenince görünüyordu.
    var stickerCache = {};

    function appendMessageResolvingSticker(appendFn, msg, isMine, opts) {
        if (msg.sticker || !msg.sticker_id) {
            appendFn(msg, isMine, opts);
            return;
        }
        if (Object.prototype.hasOwnProperty.call(stickerCache, msg.sticker_id)) {
            msg.sticker = stickerCache[msg.sticker_id];
            appendFn(msg, isMine, opts);
            return;
        }
        fetch('/stickers/' + msg.sticker_id)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                stickerCache[msg.sticker_id] = data;
                msg.sticker = data;
                appendFn(msg, isMine, opts);
            })
            .catch(function () {
                appendFn(msg, isMine, opts); // sticker gösterilmeden en azından mesaj gelsin
            });
    }

    // Supabase created_at UTC ISO döner ("...+00:00"); önceden ham string'in
    // 11:16 karakterleri kesilip UTC saat yerel saatmiş gibi gösteriliyordu
    // (kullanıcı raporu: 3 saatlik fark). Sabit +3 saat eklenir — sunucu
    // tarafındaki local_time filtresiyle (app/__init__.py, aynı gerekçe:
    // Türkiye 2016'dan beri DST uygulamıyor) tutarlı kalması için tarayıcının
    // Intl saat dilimi listesine (bazı ortamlarda eksik olabilir) güvenilmez.
    function formatLocalTime(iso) {
        if (!iso) return 'şimdi';
        try {
            var d = new Date(iso);
            if (isNaN(d.getTime())) return iso.substring(11, 16);
            var local = new Date(d.getTime() + 3 * 60 * 60 * 1000);
            var hh = String(local.getUTCHours()).padStart(2, '0');
            var mm = String(local.getUTCMinutes()).padStart(2, '0');
            return hh + ':' + mm;
        } catch (e) {
            return iso.substring(11, 16);
        }
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
        var time = formatLocalTime(msg.created_at);
        html += '<span class="time">' + time;
        if (isMine) {
            var read = !!msg.read_at;
            html += ' <span class="read-receipt' + (read ? ' read' : '') + '" aria-label="' + (read ? 'Okundu' : 'İletildi') + '">'
                + (read ? '✓✓' : '✓') + '</span>';
        }
        html += '</span>';
        // Sunucu render'ındaki tepki tetikleyicisi + picker — bunlar olmadan
        // yeni gönderilen/gelen mesajlara sayfa yenilenmeden tepki verilemezdi.
        // msg-react-row: chip'ler + tetik + sil TEK flex satırda (tetik
        // butonu chip eklendikçe alt satıra düşmesin — kullanıcı raporu)
        html += '<div class="msg-react-row">';
        html += '<button class="msg-react-trigger" aria-label="Emoji tepkisi ekle" type="button">🙂+</button>';
        html += '<div class="msg-react-picker" hidden>';
        REACT_EMOJIS.forEach(function (em) {
            html += '<button type="button" data-emoji="' + em + '" aria-label="' + em + ' tepkisi">' + em + '</button>';
        });
        html += '</div>';
        // Sadece kendi mesajım silinebilir (sunucu render'ıyla aynı desen)
        if (isMine) {
            html += '<button class="msg-delete-btn" aria-label="Mesajı sil" type="button">🗑</button>';
        }
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
    // "Yazıyor..." broadcast'i AYRI, private+RLS korumalı bir kanalda akar —
    // mesaj içeriği kanalına (activeChannel) BİLEREK dokunulmadı (bkz. aşağıdaki
    // kurulum yorumu). Konuşma başına tek kanal, konuşma geçişinde yeniden kurulur.
    var typingChannel = null;

    // Sol konuşma listesi CANLI güncellenir: yeni mesaj gelen/gönderilen
    // konuşma en üste taşınır ve önizleme metni yenilenir (kullanıcı isteği —
    // önceden liste sadece sayfa yüklenirken sıralanıyordu)
    function bumpInboxItem(convId, preview) {
        var item = document.querySelector('.inbox-item[data-conversation-id="' + convId + '"]');
        if (!item) return;
        if (preview) {
            var p = item.querySelector('.preview');
            if (p) p.textContent = String(preview).slice(0, 40);
        }
        // Açık OLMAYAN sohbete mesaj geldi → okunmamış işaretle (açık sohbet
        // zaten okunuyor; kendi gönderdiğimiz mesaj da hep aktif sohbette).
        // Temizleme: messagesPanel.js sohbeti açarken class+noktayı kaldırır.
        if (!item.classList.contains('active')) {
            item.classList.add('unread');
            if (!item.querySelector('.unread-dot')) {
                var dot = document.createElement('span');
                dot.className = 'unread-dot';
                dot.setAttribute('aria-label', 'Okunmamış mesaj');
                item.appendChild(dot);
            }
        }
        var parent = item.parentNode;
        var first = parent ? parent.querySelector('.inbox-item') : null;
        if (first && first !== item) parent.insertBefore(item, first);
    }
    // liveBadges.js için dışa açık: AÇIK OLMAYAN sohbete mesaj gelince de
    // sol liste canlı sıralansın (bildirim satırı DB olayı tetikler)
    window._bumpInboxItem = bumpInboxItem;

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

        // AYNI panel DOM'una ikinci init çağrısı (örn. sayfa yüklemesi +
        // messagesPanel'in tekrar çağırması) tüm listener'ları ÇİFT bağlar —
        // her mesaj iki kez gönderilirdi (kullanıcı raporu). Yeni panel DOM'u
        // bu attribute'suz gelir, gerçek geçişlerde init normal çalışır.
        if (panel.dataset.chatInit === '1') return;
        panel.dataset.chatInit = '1';

        var conversationId = panel.dataset.conversationId;
        var sendUrl = panel.dataset.sendUrl;
        var otherUsername = panel.dataset.otherUsername || 'Kullanıcı';
        var isGroup = panel.dataset.isGroup === '1';

        // --- Mesaj HIZLI YOLU (broadcast önizleme) durumu ---
        // Gönderen, "Gönder"e bastığı ANDA mesajı kanaldan broadcast eder —
        // karşı taraf sohbeti açıksa DB turunu (insert + WAL + realtime,
        // ~0.5-1sn) beklemeden ~100-200ms'de görür. DB'den gerçek INSERT
        // gelince önizleme balonu yerinde "gerçek" haline yükseltilir; çift
        // görünmeyi anahtar eşleştirmesi önler. Görsel yüklemeleri hariç
        // (dosya URL'i henüz yok) — onlar eski yoldan düşer.
        var pendingPreviews = {}; // key -> [önizleme DOM node'ları, FIFO]
        var recentInserts = {};   // key -> true (INSERT önce geldiyse geç kalan önizleme yutulur)
        function previewKey(senderId, content, imageUrl, stickerId) {
            return senderId + '|' + (content || '') + '|' + (imageUrl || '') + '|' + (stickerId || '');
        }
        var memberMap = {};
        try { memberMap = JSON.parse(panel.dataset.memberMap || '{}'); } catch (err) { memberMap = {}; }
        if (!conversationId || !sendUrl) return;

        // Sohbet içi "aktiflik" nabzı — bu sohbeti AÇIK tuttuğumuzu sunucuya
        // bildirir (bkz. app/messaging/_common.py mark_active/is_active_in),
        // böylece karşı taraf mesaj atınca bize bildirim/push ÜRETİLMEZ
        // (kullanıcı isteği: "sohbette olmama rağmen bildirim geliyor").
        // pingActive() her tetiklendiğinde LİVE panelin conversation-id'sini
        // okur (kapanmış closure değil) — böylece visibilitychange listener'ı
        // TEK sefer bağlansa da (document-level, sohbet geçişlerinde
        // birikmesin diye) her zaman GÜNCEL sohbeti bildirir.
        if (window._chatActiveTimer) { clearInterval(window._chatActiveTimer); window._chatActiveTimer = null; }
        function pingActive() {
            if (document.hidden) return; // sekme arka plandaysa "aktif" sayılmaz
            var liveP = document.getElementById('conversation-panel');
            var cid = liveP ? liveP.dataset.conversationId : null;
            if (!cid) return;
            var csrfAct = liveP.querySelector('input[name="csrf_token"]');
            fetch('/messages/' + cid + '/active', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfAct ? csrfAct.value : '' }
            }).then(function (r) { return r.json(); }).then(function (d) {
                // Çevrimiçi göstergesi: sohbeti şu an açık tutan DİĞER katılımcılar
                // (sunucu bellek-içi presence'tan sayar; 45sn TTL, 25sn ping)
                var pres = document.getElementById('conv-presence');
                if (!pres || !d) return;
                var here = d.here || 0;
                if (here > 0) {
                    var grp = liveP.dataset.isGroup === '1';
                    pres.textContent = grp ? here + ' kişi şu anda burada' : 'şu anda burada';
                    pres.hidden = false;
                } else {
                    pres.hidden = true;
                }
            }).catch(function () {});
        }
        pingActive();
        window._chatActiveTimer = setInterval(pingActive, 25000);
        if (!window._chatActiveVisibilityBound) {
            window._chatActiveVisibilityBound = true;
            document.addEventListener('visibilitychange', function () {
                if (!document.hidden) pingActive();
            });
        }

        ensureLightbox();

        function scrollToBottom() {
            stream.scrollTop = stream.scrollHeight;
        }

        // Başlangıç konumu: okunmamış ilk mesajın çapası varsa oradan,
        // yoksa en son mesajdan. Görsel/sticker'lar geç yüklenince
        // scrollHeight büyüyüp konum "ortada" kalıyordu (kullanıcı raporu) —
        // kullanıcı elle kaydırana dek her görsel yüklemesinde konum tazelenir.
        var userScrolled = false;
        function scrollToStart() {
            if (userScrolled) return;
            var anchor = document.getElementById('first-unread-msg');
            if (anchor) {
                stream.scrollTop = anchor.offsetTop - stream.offsetTop - 8;
            } else {
                scrollToBottom();
            }
        }
        scrollToStart();
        stream.querySelectorAll('img').forEach(function (im) {
            if (!im.complete) im.addEventListener('load', scrollToStart, { once: true });
        });
        stream.addEventListener('wheel', function () { userScrolled = true; }, { passive: true, once: true });
        stream.addEventListener('touchmove', function () { userScrolled = true; }, { passive: true, once: true });
        formatSharedPosts(stream);

        // Tepki picker'ı artık position:fixed (viewport koordinatlı) — stream
        // kaydırılınca konumu güncellenmez, mesajdan kopmuş görünür. Basit ve
        // standart çözüm: kaydırma başlayınca açık picker'ı kapat.
        stream.addEventListener('scroll', function () { closeAllReactPickers(); }, { passive: true });

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
                if (f) sendTyping(true, 'image');
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

        // activity: 'text' (varsayılan) | 'voice' | 'image' — karşı tarafa
        // sadece "yazıyor" değil, TAM OLARAK ne yaptığımı gösterebilmek için
        // (kullanıcı isteği: ses kaydı/görsel seçimi de göstergeyi tetiklesin).
        // Sticker/GIF seçimi BİLEREK dahil edilmedi: ikisi de seçilir seçilmez
        // otomatik gönderiliyor (bkz. stickers.js/chat.js autosubmit), mesaj
        // Realtime'la neredeyse anında gelir — bir "activity" sinyali göstermeye
        // vakit kalmadan zaten yerini asıl mesaja bırakırdı, görünür bir
        // fayda sağlamaz.
        function sendTyping(isTyping, activity) {
            if (!typingChannel) return;
            var now = Date.now();
            if (isTyping && now - lastTypingSentAt < 2000) return; // en fazla 2sn'de bir gönder
            lastTypingSentAt = isTyping ? now : 0;
            typingChannel.send({
                type: 'broadcast', event: 'typing',
                payload: { typing: isTyping, username: myUsername, activity: activity || 'text' },
            });
        }

        if (typingIndicator) {
            input.addEventListener('input', function () { sendTyping(true, 'text'); });
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

        // call.js arama sonucu mesajını (süre/cevapsız/reddedildi) gönderdikten
        // sonra balonu bu kancayla akışa düşürür — realtime kendi mesajlarımızı
        // bilerek atladığı için başka yolu yok
        window._chatAppendMine = function (saved) {
            appendMessage(saved, true);
            bumpInboxItem(conversationId, saved.content || '');
        };

        // --- Sesli mesaj kaydı (MediaRecorder API) — SADECE tarayıcı destekliyorsa
        // gösterilir (progressive enhancement). Kayıt/önizleme AYRI bir çubukta
        // değil, doğrudan mesaj yazma kutusunun yerinde görünür; gönderme TEK
        // "Gönder" butonuyla olur (submit handler'ı aşağıda, recordedBlob'a bakar);
        // "Sil" butonu SADECE bir kayıt hazırken Gönder'in yanında belirir.
        var voiceBtn = document.getElementById('voice-record-btn');
        var voiceDiscardBtn = document.getElementById('voice-discard-btn');
        var voiceStopBtn = document.getElementById('voice-stop-btn');
        var voiceRecordingStatus = document.getElementById('voice-recording-status');
        var voiceRecordingStatusText = document.getElementById('voice-recording-status-text');
        var voicePreviewAudio = document.getElementById('voice-preview-audio');
        var recordedVoiceBlob = null;
        var sendAfterStop = false;

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
                voiceRecordingStatusText.textContent = '';
                voicePreviewAudio.hidden = true;
                voicePreviewAudio.removeAttribute('src');
                voiceDiscardBtn.hidden = true;
                voiceBtn.hidden = false;
                voiceBtn.textContent = '🎤';
                voiceBtn.classList.remove('recording');
                recordedVoiceBlob = null;
                recordedChunks = [];
                sendAfterStop = false;
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

                    // Gönder tuşu kaydediyorsa, durdurmadan sonra direkt gönder
                    // (önizleme seçeneği atlanır). Sayaç durdurulup durum metni
                    // güncellenir — yoksa gönderim sürerken "Kaydediliyor..."
                    // saymaya devam ediyordu; karşı tarafın "kaydediyor"
                    // göstergesi de burada kapatılır (normal akıştaki gibi).
                    if (sendAfterStop) {
                        sendAfterStop = false;
                        if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
                        voiceRecordingStatusText.textContent = '📤 Gönderiliyor...';
                        sendTyping(false);
                        form.requestSubmit();
                        return;
                    }

                    // Normal akış: kayıt bitti, kullanıcı sil/gönder seçebilir
                    voiceRecordingStatus.hidden = true;
                    voicePreviewAudio.src = URL.createObjectURL(recordedVoiceBlob);
                    voicePreviewAudio.hidden = false;
                    voiceDiscardBtn.hidden = false;
                    voiceBtn.hidden = true;
                    if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
                    // Kayıt bitti (gönderilecek mi silinecek mi henüz belli değil) —
                    // "kaydediyor" göstergesi burada kapatılır, aktif eylem sona erdi
                    sendTyping(false);
                };
                mediaRecorder.start();
                recordingStartedAt = Date.now();
                input.hidden = true;
                voiceRecordingStatus.hidden = false;
                voiceRecordingStatusText.textContent = '🔴 Kaydediliyor... 0:00';
                voiceBtn.textContent = '⏹';
                voiceBtn.classList.add('recording');
                sendTyping(true, 'voice');
                recordingTimer = setInterval(function () {
                    voiceRecordingStatusText.textContent = '🔴 Kaydediliyor... ' + formatElapsed(Date.now() - recordingStartedAt);
                }, 500);
            }

            voiceBtn.addEventListener('click', function () {
                if (mediaRecorder && mediaRecorder.state === 'recording') {
                    mediaRecorder.stop();
                } else {
                    startRecording();
                }
            });

            voiceStopBtn.addEventListener('click', function () {
                if (mediaRecorder && mediaRecorder.state === 'recording') {
                    mediaRecorder.stop();
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

            // Kaydediyse: durdurup, onstop çağrılınca yeniden submit et
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                sendAfterStop = true;
                mediaRecorder.stop();
                return;
            }

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
            bumpInboxItem(conversationId,
                content || (stickerVal ? '🏷️ Çıkartma' : (gifVal ? 'GIF' : '📷 Görsel')));

            // HIZLI YOL: karşı taraf da bu sohbeti açıksa mesajı DB turunu
            // beklemeden anında ilet (bkz. pendingPreviews yorumu). Broadcast
            // düşmezse sorun yok — mesaj zaten DB + postgres_changes yoluyla
            // ulaşır, bu sadece kestirme.
            if (!hasImage && activeChannel) {
                try {
                    activeChannel.send({
                        type: 'broadcast', event: 'msg-preview',
                        payload: {
                            sender_id: window.ME_ID,
                            content: content,
                            image_url: gifVal || null,
                            sticker: optimisticMsg.sticker || null
                        }
                    });
                } catch (err) { /* kestirme başarısız — normal yol devrede */ }
            }

            // Gönderilecek veriyi HEMEN yakala ve inputları HEMEN temizle —
            // kullanıcı bir sonraki mesajı beklemeden yazabilir (bekletme yok);
            // ağ isteği kuyruğa girer (sıra korunur, ard arda gönderimde
            // mesajlar sunucuya doğru sırayla ulaşır — beğeni deseninin aynısı)
            var formData = new FormData(form);
            formData.set('content', content);
            if (!hasImage) formData.delete('image');

            input.value = '';
            input.style.height = 'auto';
            if (imageName) imageName.textContent = '';
            if (imageInput) imageInput.value = '';
            if (stickerIdInput) {
                stickerIdInput.value = '';
                delete stickerIdInput.dataset.imageUrl;
            }
            if (gifUrlInput) gifUrlInput.value = '';
            input.focus();

            form._sendChain = (form._sendChain || Promise.resolve()).then(async function () {
                try {
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
                            timeEl.textContent = formatLocalTime(saved.created_at);
                        }
                    }
                } catch (err) {
                    console.error('Mesaj gönderilemedi:', err);
                    if (node) node.remove();
                    alert('Mesaj gönderilemedi.');
                } finally {
                    if (localImageUrl) URL.revokeObjectURL(localImageUrl);
                }
            });
        });

        // --- Yoklama (polling) yedeği: Realtime kanalı KURULAMAZSA devreye
        // girer. Bazı ortamlarda (VPN, antivirüs, kurumsal ağ, bazı tarayıcı
        // katmanları) websocket join'i reddediliyor ve kullanıcı mesajları
        // ancak F5 ile görebiliyordu (canlı test bulgusu, Brave + 401/
        // CHANNEL_ERROR). Yedek: 4sn'de bir konuşma partial'ı çekilir, DOM'da
        // olmayan mesajlar sunucu render'ıyla eklenir. Sunucu tarafı fetch
        // zaten mark-read yaptığı için rozetler de doğru kalır. ---
        if (window._chatPollTimer) { clearInterval(window._chatPollTimer); window._chatPollTimer = null; }

        function startPollingFallback() {
            if (window._chatPollTimer) return;
            console.warn('Realtime kurulamadı — 4sn yoklama moduna geçildi (mesajlar yine düşecek)');
            window._chatPollTimer = setInterval(async function () {
                try {
                    var res = await fetch('/messages/' + conversationId, {
                        headers: { 'X-Requested-With': 'fetch' }
                    });
                    if (!res.ok) return;
                    var tmp = document.createElement('div');
                    tmp.innerHTML = await res.text();
                    var added = false;
                    tmp.querySelectorAll('.msg[data-msg-id]').forEach(function (m) {
                        var id = m.dataset.msgId;
                        if (!id || stream.querySelector('.msg[data-msg-id="' + id + '"]')) return;
                        stream.appendChild(m);
                        added = true;
                    });
                    if (added) {
                        formatSharedPosts(stream);
                        // Kullanıcı en alttaysa yeni mesaja kaydır (yukarı okuyorsa rahatsız etme)
                        if (stream.scrollHeight - stream.scrollTop - stream.clientHeight < 160) scrollToBottom();
                    }
                } catch (e) { /* bir sonraki turda tekrar denenir */ }
            }, 4000);
        }

        function stopPollingFallback() {
            if (window._chatPollTimer) { clearInterval(window._chatPollTimer); window._chatPollTimer = null; }
        }

        // --- Supabase Realtime: önceki kanalı kapat, yeni konuşmaya abone ol ---
        if (activeChannel && window.supabaseClient) {
            try { window.supabaseClient.removeChannel(activeChannel); } catch (err) { /* yut */ }
            activeChannel = null;
        }
        if (typingChannel && window.supabaseClient) {
            try { window.supabaseClient.removeChannel(typingChannel); } catch (err) { /* yut */ }
            typingChannel = null;
        }
        if (!window.supabaseClient) startPollingFallback();

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
                        // HIZLI YOL eşleştirmesi: bu mesajın broadcast önizlemesi
                        // zaten ekrandaysa yeni balon AÇMA — mevcut balonu gerçek
                        // id/saat ile yükselt (çift görünme olmasın)
                        var dedupeKey = previewKey(msg.sender_id, msg.content, msg.image_url, msg.sticker_id);
                        recentInserts[dedupeKey] = true;
                        setTimeout(function () { delete recentInserts[dedupeKey]; }, 10000);
                        var previewQueue = pendingPreviews[dedupeKey];
                        var previewNode = previewQueue && previewQueue.shift();
                        if (previewNode && previewNode.isConnected) {
                            previewNode.dataset.msgId = msg.id;
                            previewNode.dataset.reactUrl = reactUrlFor(msg.id);
                            var previewTime = previewNode.querySelector('.time');
                            if (previewTime && msg.created_at) {
                                previewTime.textContent = formatLocalTime(msg.created_at);
                            }
                        } else {
                            var senderName = isGroup ? (memberMap[msg.sender_id] || 'Bilinmeyen') : null;
                            appendMessageResolvingSticker(appendMessage, msg, isMine, { senderName: senderName });
                        }
                        bumpInboxItem(conversationId, msg.content || '📷 Medya');

                        // Haritada olmayan üye (sayfa açıldıktan sonra katılan) —
                        // 'Bilinmeyen' yerine profili çekip adı yerine yaz
                        if (isGroup && !memberMap[msg.sender_id] && window.supabaseClient) {
                            window.supabaseClient.from('profiles').select('username')
                                .eq('id', msg.sender_id).single().then(function (r) {
                                    if (r.data && r.data.username) {
                                        memberMap[msg.sender_id] = r.data.username;
                                        var senderEl = stream.querySelector('[data-msg-id="' + msg.id + '"] .msg-sender');
                                        if (senderEl) senderEl.textContent = r.data.username;
                                    }
                                });
                        }

                        // Sohbetin İÇİNDEYKEN gelen mesaj anında okundu sayılır —
                        // yoksa navbar/mesaj listesi rozeti sohbetteyken bile
                        // 1-2-3 diye birikiyordu (kullanıcı raporu). Karşı taraf
                        // da UPDATE olayıyla ✓✓'yi canlı görür.
                        if (!isGroup) {
                            var csrfIn = document.querySelector('input[name="csrf_token"]');
                            fetch('/messages/' + conversationId + '/mark-read', {
                                method: 'POST',
                                headers: { 'X-CSRF-Token': csrfIn ? csrfIn.value : '' }
                            }).then(function () {
                                if (window.refreshMessagesBadge) window.refreshMessagesBadge();
                            }).catch(function () { /* rozet en geç sayfa yenilemede düzelir */ });
                        }
                    }
                }).on('broadcast', { event: 'msg-preview' }, function (msg) {
                    // HIZLI YOL alıcı ucu: gönderenin submit ANINDA yolladığı
                    // önizlemeyi hemen göster; gerçek kayıt (INSERT) gelince
                    // yukarıdaki eşleştirme bu balonu yükseltir. NOT: bu kanal
                    // şimdilik public (bilinen geçici RLS boşluğu — bkz.
                    // typingChannel notu); conversation id'ler tahmin edilemez
                    // UUID, kalıcı çözüm RLS izole testi sonrası.
                    var p = msg.payload || {};
                    if (!p.sender_id || p.sender_id === window.ME_ID) return;
                    var key = previewKey(p.sender_id, p.content, p.image_url, p.sticker && p.sticker.id);
                    if (recentInserts[key]) return; // gerçek kayıt zaten geldi (sıra dışı ulaşan önizleme)
                    var pSenderName = isGroup ? (memberMap[p.sender_id] || 'Bilinmeyen') : null;
                    var pNode = appendMessage({
                        content: p.content, image_url: p.image_url,
                        sticker: p.sticker || null, created_at: null
                    }, false, { senderName: pSenderName });
                    if (!pNode) return;
                    (pendingPreviews[key] = pendingPreviews[key] || []).push(pNode);
                    bumpInboxItem(conversationId, p.content || '📷 Medya');
                    // Gerçek kayıt hiç gelmezse (gönderim sunucuda başarısız
                    // olduysa) önizleme 20sn sonra sessizce kaldırılır
                    setTimeout(function () {
                        var q = pendingPreviews[key];
                        if (q) {
                            var i = q.indexOf(pNode);
                            if (i !== -1) q.splice(i, 1);
                        }
                        if (!pNode.dataset.msgId) pNode.remove();
                    }, 20000);
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
                }).on('postgres_changes', {
                    event: 'DELETE',
                    schema: 'public',
                    table: 'messages',
                    filter: 'conversation_id=eq.' + conversationId
                }, function (payload) {
                    // Karşı taraf bir mesajını sildi — canlı kaldır. DELETE
                    // payload'ında sadece REPLICA IDENTITY'de olan alanlar
                    // (varsayılan: primary key) gelir, id her zaman yeterli.
                    var deletedId = payload.old && payload.old.id;
                    if (!deletedId) return;
                    var delEl = stream.querySelector('[data-msg-id="' + deletedId + '"]');
                    if (delEl) delEl.remove();
                }).subscribe(function (status) {
                    if (status === 'SUBSCRIBED') {
                        stopPollingFallback(); // canlı kanal kuruldu, yoklamaya gerek yok
                    } else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                        console.warn('Realtime bağlantı sorunu, durum:', status);
                        startPollingFallback();
                    }
                });
                activeChannel = channel;

                // "Yazıyor..." broadcast'i AYRI bir kanalda (mesaj kanalına
                // dokunulmadı, bkz. yukarıdaki not). GEÇİCİ GERİ ALMA
                // (2026-07-10): private:true canlıda CHANNEL_ERROR'a yol
                // açtı (bkz. call.js'teki aynı geri alma notu) — kök neden
                // netleşene kadar public kanala dönüldü, RLS policy'leri
                // DB'de duruyor (sql/migration_realtime_broadcast_rls.sql).
                typingChannel = window.supabaseClient.channel('typing:' + conversationId, {
                    config: { broadcast: { self: false } }
                });
                typingChannel.on('broadcast', { event: 'typing' }, function (msg) {
                    if (!typingIndicator) return;
                    var payload = msg.payload || {};
                    if (typingClearTimer) clearTimeout(typingClearTimer);
                    if (payload.typing) {
                        // activity'e göre farklı metin — sadece klavye değil, ses
                        // kaydı/görsel seçimi de gösterge tetikler (kullanıcı isteği).
                        var ACTIVITY_TEXT = {
                            text: 'yazıyor', voice: 'sesli mesaj kaydediyor', image: 'görsel gönderiyor'
                        };
                        var verb = ACTIVITY_TEXT[payload.activity] || 'yazıyor';
                        // Grup sohbetinde KİMİN yazdığını göstermek gerekir (sabit
                        // "otherUsername" bir grupta anlamsız olurdu) — payload'daki
                        // gönderen adı kullanılır, 1:1'de de tutarlı çalışır.
                        var newText = (payload.username || otherUsername) + ' ' + verb + '...';
                        // Metin değişmiyorsa (aynı activity devam ediyor) yeniden
                        // yazılmaz — aria-live her keystroke'ta tekrar tekrar
                        // duyurmasın diye (SADECE durum/activity değişiminde duyurur).
                        if (typingIndicator.hidden || typingIndicator.textContent !== newText) {
                            typingIndicator.textContent = newText;
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
                }).subscribe();

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
                    // Chip'ler tetik butonuyla AYNI flex satırda (msg-react-row)
                    // yaşar — satırın başına eklenir ki tetik hep sonda kalsın
                    var rowEl = msg.querySelector('.msg-react-row');
                    if (rowEl) rowEl.insertAdjacentElement('afterbegin', reactionsDiv);
                    else {
                        var timeEl = msg.querySelector('.time');
                        if (timeEl) timeEl.insertAdjacentElement('afterend', reactionsDiv);
                        else msg.appendChild(reactionsDiv);
                    }
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
                        // position:fixed + viewport koordinatları (kullanıcı raporu:
                        // .msg içinde absolute konumlanınca .message-stream'in
                        // (overflow-y:auto) scrollHeight'ını büyütüyordu — picker
                        // açılınca mesaj listesi "büyüyüp küçülüyormuş" gibi
                        // görünüyordu, çünkü scroll konteynerinin içerik yüksekliği
                        // gerçekten değişiyordu). fixed olduğu için artık stream'in
                        // scroll akışına hiç dahil olmuyor, salt viewport üzerinde
                        // bir overlay gibi davranıyor. HER ZAMAN tetikleyicinin
                        // ALTINDA açılır, sol kenar tıklanan butonun tam konumundan
                        // başlar (mesaj kenarına değil).
                        var rect = trigger.getBoundingClientRect();
                        picker.style.top = (rect.bottom + 4) + 'px';
                        picker.style.left = rect.left + 'px';
                        picker.removeAttribute('hidden');
                        // Sağ kenar viewport dışına taşarsa geri çek (görünürlük
                        // garantisi — konum yine tıklanan noktadan başlar, sadece
                        // ekrandan taşmayacak kadar sola kayar).
                        var overflow = picker.getBoundingClientRect().right - window.innerWidth;
                        if (overflow > 0) {
                            picker.style.left = (rect.left - overflow - 8) + 'px';
                        }
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

        // 4. Mesaj silme (sadece kendi mesajım, sunucu tarafında da kontrol edilir)
        var deleteBtn = e.target.closest('.msg-delete-btn');
        if (deleteBtn) {
            e.preventDefault();
            var deleteMsgEl = deleteBtn.closest('.msg');
            var deleteMsgId = deleteMsgEl && deleteMsgEl.dataset.msgId;
            if (!deleteMsgId) return;
            (async function() {
                if (!await window.appConfirm('Bu mesajı silmek istiyor musun?')) return;
                var csrfInput = document.querySelector('input[name="csrf_token"]');
                fetch('/messages/message/' + deleteMsgId + '/delete', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfInput ? csrfInput.value : '' },
                })
                .then(function (res) {
                    if (res.ok) deleteMsgEl.remove();
                })
                .catch(function (err) {
                    console.error('Mesaj silinemedi:', err);
                });
            })();
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
