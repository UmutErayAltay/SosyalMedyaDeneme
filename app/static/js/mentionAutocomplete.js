// @etiketleme otomatik tamamlama — post composer, yorum/yanıt kutuları ve
// mesaj kutusunda ortak çalışır. Sunucu tarafı endpoint (GET
// /social/mentions/search?q=) zaten takip ilişkisine göre sıralı en fazla 3
// sonuç döner; burada SADECE prefix yakalama + dropdown gösterimi var,
// yeniden sıralama YAPILMAZ. document-level delegation kullanılır çünkü
// yorum yanıt kutuları (.reply-form textarea) comments.js tarafından hem
// sunucu render'ında hem sonradan AJAX ile DOM'a ekleniyor.
(function () {
    'use strict';

    // app/mentions.py'deki MENTION_RE ile aynı karakter sınıfı (chat.js/
    // comments.js'teki MENTION_RE_JS ile birebir aynı) — imlecin O ANA
    // KADARKİ kısmında satır sonunda aktif bir "@prefix" yazımı var mı bakar.
    var TRIGGER_RE = /(?:^|\s)@([\p{L}\p{N}_.-]+)$/u;

    var TARGET_SELECTOR = '#post-modal textarea[name="content"], #comment-input, .reply-form textarea, #msg-input';

    var DEBOUNCE_MS = 200;

    var dropdown = null;
    var activeTextarea = null;
    var mentionStart = -1;  // aktif textarea'da "@" karakterinin index'i
    var mentionEnd = -1;    // imlecin o anki konumu (replace aralığının sonu)
    var debounceTimer = null;
    var requestToken = 0;   // geç gelen (stale) fetch yanıtlarını yok saymak için

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function ensureDropdown() {
        if (dropdown) return dropdown;
        dropdown = document.createElement('div');
        dropdown.id = 'mention-autocomplete-dropdown';
        dropdown.className = 'mention-autocomplete-dropdown';
        dropdown.setAttribute('hidden', '');
        document.body.appendChild(dropdown);
        return dropdown;
    }

    function hideDropdown() {
        if (!dropdown) return;
        dropdown.hidden = true;
        dropdown.innerHTML = '';
        activeTextarea = null;
        mentionStart = -1;
        mentionEnd = -1;
    }

    function positionDropdown(textarea) {
        var rect = textarea.getBoundingClientRect();
        var dd = ensureDropdown();
        dd.style.left = rect.left + 'px';
        dd.style.top = rect.bottom + 'px';
        dd.style.minWidth = Math.min(rect.width, 260) + 'px';
    }

    function renderResults(users, textarea) {
        var dd = ensureDropdown();
        if (!users || !users.length) {
            hideDropdown();
            return;
        }
        var html = '';
        for (var i = 0; i < users.length; i++) {
            var u = users[i];
            var avatarHtml = u.avatar_url
                ? '<img src="' + escapeHtml(u.avatar_url) + '" class="avatar avatar-sm" alt="" loading="lazy">'
                : '<div class="avatar avatar-sm avatar-placeholder" aria-hidden="true"></div>';
            html += '<button type="button" class="mention-autocomplete-item" data-username="' +
                escapeHtml(u.username) + '">' + avatarHtml +
                '<span class="mention-autocomplete-username">' + escapeHtml(u.username) + '</span></button>';
        }
        dd.innerHTML = html;
        positionDropdown(textarea);
        dd.hidden = false;
    }

    function fetchSuggestions(prefix, textarea) {
        var myToken = ++requestToken;
        fetch('/social/mentions/search?q=' + encodeURIComponent(prefix))
            .then(function (res) { return res.ok ? res.json() : { users: [] }; })
            .then(function (data) {
                // Kullanıcı bu arada yazmaya devam ettiyse veya dropdown'ı
                // kapattıysa (aktif textarea değişti) bu yanıtı yok say.
                if (myToken !== requestToken || activeTextarea !== textarea) return;
                renderResults(data.users, textarea);
            })
            .catch(function () { /* sessizce yut — otomatik tamamlama kritik değil */ });
    }

    function handleInput(textarea) {
        var value = textarea.value.slice(0, textarea.selectionStart);
        var match = value.match(TRIGGER_RE);
        if (!match) {
            hideDropdown();
            return;
        }
        var prefix = match[1];
        activeTextarea = textarea;
        mentionEnd = textarea.selectionStart;
        mentionStart = mentionEnd - 1 - prefix.length;

        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function () {
            fetchSuggestions(prefix, textarea);
        }, DEBOUNCE_MS);
    }

    function selectUser(username) {
        var textarea = activeTextarea;
        if (!textarea || mentionStart < 0) return;
        var value = textarea.value;
        var before = value.slice(0, mentionStart);
        var after = value.slice(mentionEnd);
        var insertion = '@' + username + ' ';
        textarea.value = before + insertion + after;
        var caret = before.length + insertion.length;
        textarea.selectionStart = textarea.selectionEnd = caret;
        textarea.focus();
        hideDropdown();
    }

    document.addEventListener('input', function (e) {
        var ta = e.target;
        if (!ta.matches || !ta.matches(TARGET_SELECTOR)) return;
        handleInput(ta);
    });

    // Dropdown öğesine tıklama: "click" DEĞİL "mousedown" dinlenir — textarea
    // blur'u click'ten ÖNCE tetiklenir, mousedown ise blur'dan önce çalışıp
    // seçimi garantiler (klasik "dropdown tıklarken blur kapatıyor" tuzağı).
    document.addEventListener('mousedown', function (e) {
        var item = e.target.closest('.mention-autocomplete-item');
        if (item) {
            e.preventDefault();
            selectUser(item.getAttribute('data-username'));
            return;
        }
        // Dışarı tıklama: dropdown açıksa ve tıklama ne dropdown'ın ne de
        // aktif textarea'nın içindeyse kapat.
        if (dropdown && !dropdown.hidden && !dropdown.contains(e.target) &&
            e.target !== activeTextarea) {
            hideDropdown();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && dropdown && !dropdown.hidden) {
            hideDropdown();
        }
    });

    // blur: küçük bir gecikmeyle kapat ki dropdown'a mousedown ile tıklama
    // önce işlensin (yukarıdaki mousedown handler'ı blur'dan önce çalışır,
    // ama yine de emniyet payı olarak gecikme bırakılıyor).
    document.addEventListener('blur', function (e) {
        var ta = e.target;
        if (!ta.matches || !ta.matches(TARGET_SELECTOR)) return;
        setTimeout(function () {
            if (activeTextarea === ta) hideDropdown();
        }, 150);
    }, true);
}());
