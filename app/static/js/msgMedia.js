// Sohbette paylaşılan medya galerisi — msg-media-toggle-btn ile açılır.
// Modal `_conversation_panel.html` içinde yaşıyor ve panel her konuşma
// değişiminde AJAX ile yeniden oluşuyor (messagesPanel.js) — bu yüzden
// groupAdmin.js'teki gibi TÜM dinleyiciler document-level delegation kullanır.

(function () {
    var lastFocused = null;

    function getModal() {
        return document.getElementById('msg-media-modal');
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function formatTime(iso) {
        try {
            var d = new Date(iso);
            var local = new Date(d.getTime() + 3 * 60 * 60 * 1000);
            var hh = String(local.getUTCHours()).padStart(2, '0');
            var mm = String(local.getUTCMinutes()).padStart(2, '0');
            return hh + ':' + mm;
        } catch (e) {
            return '';
        }
    }

    async function loadMedia() {
        var modal = getModal();
        if (!modal) return;
        var convId = modal.dataset.conversationId;
        var imagesEl = document.getElementById('msg-media-images');
        var audiosEl = document.getElementById('msg-media-audios');
        var emptyEl = document.getElementById('msg-media-empty');
        imagesEl.innerHTML = '';
        audiosEl.innerHTML = '';
        emptyEl.hidden = true;
        try {
            var res = await fetch('/messages/' + convId + '/media', { headers: { 'X-Requested-With': 'fetch' } });
            var data = await res.json();
            var images = data.images || [];
            var audios = data.audios || [];

            if (images.length === 0 && audios.length === 0) {
                emptyEl.hidden = false;
                return;
            }

            images.forEach(function (m) {
                var img = document.createElement('img');
                img.src = m.url;
                img.alt = 'Paylaşılan görsel';
                img.loading = 'lazy';
                imagesEl.appendChild(img);
            });

            audios.forEach(function (m) {
                var wrap = document.createElement('div');
                wrap.className = 'voice-player';
                wrap.dataset.voicePlayer = '';
                wrap.innerHTML = '<button type="button" class="voice-play-btn" aria-label="Sesli mesajı oynat">' + (window.ICONS ? window.ICONS.get('play', { size: 14 }) : '▶') + '</button>'
                    + '<div class="voice-waveform" aria-hidden="true"></div>'
                    + '<span class="voice-duration">0:00</span>'
                    + '<audio src="' + m.url + '" class="msg-audio" preload="metadata" hidden></audio>';
                var timeLabel = document.createElement('span');
                timeLabel.className = 'muted';
                timeLabel.style.fontSize = '11px';
                timeLabel.textContent = formatTime(m.created_at);
                var row = document.createElement('div');
                row.style.display = 'flex';
                row.style.flexDirection = 'column';
                row.style.gap = '2px';
                row.appendChild(wrap);
                row.appendChild(timeLabel);
                audiosEl.appendChild(row);
            });

            if (window.initVoicePlayers) window.initVoicePlayers(audiosEl);
        } catch (err) {
            emptyEl.hidden = false;
            emptyEl.textContent = 'Medya yüklenemedi.';
        }
    }

    function openModal() {
        var modal = getModal();
        if (!modal) return;
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        loadMedia();
    }

    function closeModal() {
        var modal = getModal();
        if (!modal) return;
        modal.hidden = true;
        document.body.style.overflow = '';
        if (lastFocused) lastFocused.focus();
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest('#msg-media-toggle-btn')) {
            openModal();
            return;
        }
        var modal = getModal();
        if (!modal || modal.hidden) return;

        if (e.target.closest('#close-msg-media-modal')) {
            closeModal();
            return;
        }
        if (e.target === modal) {
            closeModal();
            return;
        }
        var tab = e.target.closest('.msg-media-tab');
        if (tab) {
            var target = tab.dataset.mediaTab;
            modal.querySelectorAll('.msg-media-tab').forEach(function (t) {
                var active = t === tab;
                t.classList.toggle('active', active);
                t.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            document.getElementById('msg-media-images').hidden = target !== 'images';
            document.getElementById('msg-media-audios').hidden = target !== 'audios';
        }
    });

    document.addEventListener('keydown', function (e) {
        var modal = getModal();
        if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
    });
})();
