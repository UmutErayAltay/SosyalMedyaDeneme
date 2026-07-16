// AJAX yorum gönderme + yanıtlama + beğenme — sayfa yenilenmeden

(function () {
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    // Sunucu render'ındaki linkify_mentions (mentions.py) filtresinin JS
    // karşılığı — chat.js:linkifyMentionsClient ile AYNI desen. AJAX ile
    // eklenen yorum/yanıtlar Jinja'dan GEÇMEZ, bu yüzden burada da
    // uygulanmazsa @etiket sayfa yenilenene kadar tıklanamaz düz metin
    // kalırdı (kullanıcı raporu). #comment-list'in data-valid-usernames'i
    // (post_detail.html, {kucuk_harfli: gercek_kullanici_adi}) tek seferlik
    // okunur — bu sayfa AJAX ile yeniden yüklenmiyor, panel geçişi yok.
    var validUsernamesMap = new Map();
    (function initValidUsernames() {
        var list = document.getElementById('comment-list');
        if (!list) return;
        try {
            var obj = JSON.parse(list.dataset.validUsernames || '{}');
            validUsernamesMap = new Map(Object.keys(obj).map(function (k) { return [k, obj[k]]; }));
        } catch (err) { validUsernamesMap = new Map(); }
    })();
    var MENTION_RE_JS = /@([\p{L}\p{N}_.-]+)/gu;

    function linkifyMentionsClient(text) {
        if (!text || validUsernamesMap.size === 0) return escapeHtml(text || '');
        var result = '';
        var lastIndex = 0;
        var match;
        MENTION_RE_JS.lastIndex = 0;
        while ((match = MENTION_RE_JS.exec(text)) !== null) {
            var uname = match[1];
            var realUname = validUsernamesMap.get(uname.toLowerCase());
            if (realUname === undefined) continue;
            result += escapeHtml(text.slice(lastIndex, match.index));
            result += '<a href="/u/' + encodeURIComponent(realUname) + '" class="mention-link">@' + escapeHtml(realUname) + '</a>';
            lastIndex = match.index + match[0].length;
        }
        result += escapeHtml(text.slice(lastIndex));
        return result;
    }

    // --- Otomatik büyüyen textarea (chat.js #msg-input deseniyle aynı) ---
    // Document-level delegation: yanıt formları dinamik olarak eklenir/açılır,
    // her birine ayrı listener bağlamaya gerek yok. Kullanıcı isteği: önceden
    // fare ile manuel resize ediliyordu (global textarea{resize:vertical}).
    document.addEventListener('input', function (e) {
        var ta = e.target;
        if (ta.tagName !== 'TEXTAREA') return;
        if (ta.id !== 'comment-input' && !ta.closest('.reply-form')) return;
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
    });

    // --- Enter = gönder, Shift+Enter = yeni satır (mesaj composer'ıyla aynı
    // davranış, kullanıcı isteğiyle eklendi) ---
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || e.shiftKey) return;
        var ta = e.target;
        if (ta.tagName !== 'TEXTAREA') return;
        if (ta.id === 'comment-input') {
            e.preventDefault();
            var mainForm = document.getElementById('comment-form');
            if (mainForm) mainForm.requestSubmit();
        } else {
            var replyForm = ta.closest('.reply-form');
            if (replyForm) {
                e.preventDefault();
                replyForm.requestSubmit();
            }
        }
    });

    // Yeni (AJAX ile eklenen) yorum/yanıtlara emoji tepki UI'ı ekler —
    // server-render edilen yorumlarla aynı markup (post_detail.html), boş
    // reaksiyon listesiyle başlar (yeni yorumda henüz kimse tepki vermemiştir).
    function buildReactWrapHtml(commentId) {
        return '<div class="comment-react-wrap" data-react-url="/social/comment/' + commentId + '/react">' +
            '<button class="comment-react-trigger" aria-label="Emoji tepkisi ekle" type="button">' + (window.ICONS ? window.ICONS.get('plus', { size: 14 }) : '🙂+') + '</button>' +
            '<div class="comment-react-picker" hidden>' +
            '<button type="button" data-emoji="❤️" aria-label="Kalp tepkisi">❤️</button>' +
            '<button type="button" data-emoji="😂" aria-label="Gül tepkisi">😂</button>' +
            '<button type="button" data-emoji="👍" aria-label="Beğen tepkisi">👍</button>' +
            '<button type="button" data-emoji="😮" aria-label="Şaşır tepkisi">😮</button>' +
            '<button type="button" data-emoji="😢" aria-label="Üzül tepkisi">😢</button>' +
            '<button type="button" data-emoji="🔥" aria-label="Ateş tepkisi">🔥</button>' +
            '</div></div>';
    }

    // Yeni eklenen yorum/yanıt HER ZAMAN gönderenin kendi yorumudur — sunucu
    // tarafındaki "{% if me and c.user_id == me.id %}" kontrolüne eşdeğer,
    // burada koşulsuz eklenir (server-render sonrası sayfa yenilenene kadar
    // sil butonu hiç görünmüyordu, kullanıcı raporu).
    function buildLikeBtnHtml(commentId) {
        return '<button type="button" class="btn btn-ghost small comment-like-btn" data-liked="0" ' +
            'data-like-url="/social/comment/like/' + commentId + '"><span class="comment-heart-icon">' +
            (window.ICONS ? window.ICONS.get('heart', { size: 14 }) : '♥') + '</span> <span class="like-count">0</span></button>';
    }

    function buildDeleteFormHtml(commentId, csrf) {
        return '<form action="/social/comment/' + commentId + '/delete" method="post" data-confirm="Yorumu sil?">' +
            '<input type="hidden" name="csrf_token" value="' + csrf + '">' +
            '<button type="submit" class="btn btn-ghost danger small" aria-label="Yorumu sil">Sil</button>' +
            '</form>';
    }

    function buildCommentHtml(data, content) {
        var username = data.username || 'Sen';
        var avatarHtml = data.avatar_url
            ? '<img src="' + escapeHtml(data.avatar_url) + '" class="avatar avatar-sm" alt="" loading="lazy">'
            : '<div class="avatar avatar-sm avatar-placeholder" aria-hidden="true"></div>';
        return '<div class="comment-meta">' + avatarHtml +
            '<a href="/u/' + encodeURIComponent(username) + '" class="username">' + escapeHtml(username) + '</a>' +
            '<span class="time">şimdi</span></div>' +
            '<p>' + linkifyMentionsClient(content) + '</p>';
    }

    // --- GIF Panel — Ana yorum formu ---
    var gifToggleCommentBtn = document.getElementById('gif-toggle-comment-btn');
    var gifPickerCommentPanel = document.getElementById('gif-picker-comment-panel');
    var gifSearchCommentInput = document.getElementById('gif-search-comment-input');
    var gifResultsComment = document.getElementById('gif-results-comment');
    var gifLoadingCommentMsg = document.getElementById('gif-loading-comment-msg');

    if (gifToggleCommentBtn && gifPickerCommentPanel) {
        gifToggleCommentBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (gifPickerCommentPanel.hidden) {
                gifPickerCommentPanel.hidden = false;
                if (gifSearchCommentInput) gifSearchCommentInput.focus();
                if (!gifResultsComment.innerHTML) {
                    searchGifsComment('');
                }
            } else {
                gifPickerCommentPanel.hidden = true;
            }
        });
    }

    function searchGifsComment(q) {
        if (!gifLoadingCommentMsg || !gifResultsComment) return;
        gifLoadingCommentMsg.hidden = false;
        gifResultsComment.innerHTML = '';
        fetch('/gif/search?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                gifLoadingCommentMsg.hidden = true;
                if (data.disabled) {
                    gifPickerCommentPanel.innerHTML = '<p class="muted center">GIF servisi şu anda kullanılamıyor.</p>';
                    gifToggleCommentBtn.hidden = true;
                    return;
                }
                if (!data.gifs || data.gifs.length === 0) {
                    gifResultsComment.innerHTML = '<p class="muted center">Sonuç bulunamadı.</p>';
                    return;
                }
                data.gifs.forEach(function (gif) {
                    var img = document.createElement('img');
                    img.src = gif.preview || gif.url;
                    img.alt = 'GIF';
                    img.className = 'gif-picker-img';
                    img.addEventListener('click', function () {
                        selectGifComment(gif.url);
                    });
                    gifResultsComment.appendChild(img);
                });
            })
            .catch(function (e) {
                gifLoadingCommentMsg.hidden = true;
                gifResultsComment.innerHTML = '<p class="muted center">Hata: ' + e.message + '</p>';
            });
    }

    function selectGifComment(url) {
        var form = document.getElementById('comment-form');
        if (form) {
            var gifUrlInput = form.querySelector('input[name="gif_url"]');
            if (gifUrlInput) {
                gifUrlInput.value = url;
                form.requestSubmit();
            }
        }
        gifPickerCommentPanel.hidden = true;
    }

    if (gifSearchCommentInput) {
        var gifSearchTimer = null;
        gifSearchCommentInput.addEventListener('input', function () {
            var q = this.value;
            clearTimeout(gifSearchTimer);
            gifSearchTimer = setTimeout(function () { searchGifsComment(q); }, 300);
        });
    }

    // GIF panel dışına tıklayınca kapatma (document-level)
    document.addEventListener('click', function (e) {
        if (e.target.closest('#gif-toggle-comment-btn') || e.target.closest('#gif-picker-comment-panel')) {
            // Buton veya panel içinde — işlem yapma
            return;
        }
        // Diğer yerde tıklandı — ana yorum formu GIF panel'ini kapat
        if (gifPickerCommentPanel && !gifPickerCommentPanel.hidden) {
            gifPickerCommentPanel.hidden = true;
        }
    });

    // Escape tuşu — GIF panel'i kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && gifPickerCommentPanel) {
            gifPickerCommentPanel.hidden = true;
        }
    });

    // --- Ana yorum gönderme ---
    var form = document.getElementById('comment-form');
    var input = document.getElementById('comment-input');
    var list = document.getElementById('comment-list');
    if (form && input && list) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            // Enter'a art arda basmak her seferinde requestSubmit() çağırıyor
            // (aşağıdaki keydown handler'ı) — submitBtn.disabled bunu
            // ENGELLEMEZ (programatik submit, buton durumuna bakmaz), bu
            // yüzden aynı yorum 4-5 kere gönderiliyordu (kullanıcı raporu).
            // Form üzerinde ayrı bir busy bayrağı ile en baştan koşulsuz çık.
            if (form.dataset.busy === '1') return;
            var content = input.value.trim();
            var stickerVal = (form.querySelector('input[name="sticker_id"]') || {}).value || '';
            var gifVal = (form.querySelector('input[name="gif_url"]') || {}).value || '';
            // Sticker/GIF seçiliyken metin boş olabilir
            if (!content && !stickerVal && !gifVal) return;
            form.dataset.busy = '1';
            var postId = form.dataset.postId;
            var submitBtn = form.querySelector('button[type="submit"]');
            // NOT: submitBtn artık sadece SVG ikon içeriyor (metin YOK) —
            // önceden burada submitBtn.textContent = 'Gönderiliyor...' yapılıyordu,
            // bu SVG'yi silip yerine düz metin koyardı. disabled + .busy
            // (opacity) yeterli görsel geri bildirim.
            submitBtn.disabled = true;
            submitBtn.classList.add('busy');
            try {
                var formData = new FormData(form);
                formData.set('content', content);
                var res = await fetch('/social/comment/' + postId, {
                    method: 'POST', body: formData,
                    headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfToken() },
                });
                if (!res.ok) throw new Error('İstek başarısız');
                var data = await res.json();
                var emptyMsg = list.querySelector('.muted');
                if (emptyMsg) emptyMsg.remove();
                var article = document.createElement('article');
                article.className = 'card comment';
                article.dataset.commentId = data.id;
                var mediaHtml = '';
                // Optimistic render: sticker veya GIF göster
                if (stickerVal) {
                    var imgUrl = form.querySelector('input[name="sticker_id"]').dataset.imageUrl;
                    if (imgUrl) {
                        mediaHtml = '<div class="sticker-wrap">' +
                            '<img src="' + escapeHtml(imgUrl) + '" class="sticker-rendered comment-sticker" data-sticker-id="' + escapeHtml(stickerVal) + '" alt="Sticker" loading="lazy">' +
                            '<button type="button" class="sticker-star-btn" data-sticker-id="' + escapeHtml(stickerVal) + '" aria-label="Sticker\'ı kaydet">' + (window.ICONS ? window.ICONS.get('star', { size: 14 }) : '⭐') + '</button>' +
                            '</div>';
                    }
                } else if (gifVal) {
                    mediaHtml = '<img src="' + escapeHtml(gifVal) + '" class="comment-gif" alt="GIF" loading="lazy">';
                }
                article.innerHTML = buildCommentHtml(data, content) + mediaHtml +
                    '<div class="comment-actions">' +
                    buildLikeBtnHtml(data.id) +
                    '<button type="button" class="btn btn-ghost small reply-toggle" data-comment-id="' + data.id + '">Yanıtla</button>' +
                    buildDeleteFormHtml(data.id, csrfToken()) +
                    buildReactWrapHtml(data.id) +
                    '</div>' +
                    '<form class="reply-form" data-parent-id="' + data.id + '" data-post-id="' + postId + '" hidden>' +
                    '<input type="hidden" name="csrf_token" value="' + csrfToken() + '">' +
                    '<input type="hidden" name="sticker_id" value="">' +
                    '<input type="hidden" name="gif_url" value="">' +
                    '<div class="comment-form-actions">' +
                    '<button type="button" class="comment-icon-btn" aria-label="Çıkartma ekle" data-sticker-picker-btn data-sticker-autosubmit="1">' +
                    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' +
                    '</button>' +
                    '<button type="button" class="reply-gif-toggle-btn comment-icon-btn" aria-label="GIF ekle">' +
                    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="1"/><circle cx="9" cy="15" r="1"/><circle cx="15" cy="9" r="1"/><circle cx="15" cy="15" r="1"/></svg>' +
                    '</button>' +
                    '</div>' +
                    '<textarea name="content" placeholder="Yanıtını yaz..." rows="1" aria-label="Yanıt içeriği"></textarea>' +
                    '<button type="submit" class="comment-send-btn" aria-label="Yanıtı gönder">' +
                    '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.6915026,12.4744748 L3.50612381,13.2599618 C3.19218622,13.2599618 3.03521743,13.4170592 3.03521743,13.5741566 L1.15159189,20.0151496 C0.8376543,20.8006365 0.99,21.89 1.77946707,22.52 C2.41,22.99 3.50612381,23.1 4.13399899,22.8429026 L21.714504,14.0454487 C22.6563168,13.5741566 23.1272231,12.6315722 22.9702544,11.6889879 L4.13399899,1.16554995 C3.50612381,-0.1 2.40999899,0.0570974056 1.77946707,0.4744748 C0.994623095,1.10604706 0.837654326,2.0486314 1.15159189,2.99701575 L3.03521743,9.43800871 C3.03521743,9.59510618 3.34915502,9.75220365 3.50612381,9.75220365 L16.6915026,10.5376906 C16.6915026,10.5376906 17.1624089,10.5376906 17.1624089,11.0089827 L17.1624089,12.0515671 C17.1624089,12.5228591 16.6915026,12.4744748 16.6915026,12.4744748 Z"/></svg>' +
                    '</button>' +
                    '<div data-sticker-preview hidden></div>' +
                    '</form>';
                list.appendChild(article);
                input.value = '';
                input.style.height = 'auto';
                // Sticker/GIF inputlarını temizle (sonraki yorum için)
                form.querySelector('input[name="sticker_id"]').value = '';
                form.querySelector('input[name="sticker_id"]').dataset.imageUrl = '';
                form.querySelector('input[name="gif_url"]').value = '';
                input.focus();
            } catch (err) {
                console.error('Yorum gönderilemedi:', err);
                alert('Yorum gönderilemedi.');
            } finally {
                submitBtn.disabled = false;
                submitBtn.classList.remove('busy');
                form.dataset.busy = '0';
            }
        });
    }

    // GIF panel dışına tıklayınca kapatma (dinamik yanıt panelleri için)
    document.addEventListener('click', function (e) {
        if (e.target.closest('.reply-gif-toggle-btn') || e.target.closest('.gif-picker-comment-panel')) {
            // Buton veya panel içinde — işlem yapma
            return;
        }
        // Diğer yerde tıklandı — açık reply GIF panel'lerini kapat
        var openReplyPanels = document.querySelectorAll('.reply-form .gif-picker-comment-panel:not([hidden])');
        openReplyPanels.forEach(function (p) { p.hidden = true; });
    });

    // Escape tuşu — reply GIF panel'lerini kapat
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            var openReplyPanels = document.querySelectorAll('.reply-form .gif-picker-comment-panel:not([hidden])');
            openReplyPanels.forEach(function (p) { p.hidden = true; });
        }
    });

    // --- Yanıt toggle + gönderme (event delegation) ---
    document.addEventListener('click', async function (e) {
        var toggleBtn = e.target.closest('.reply-toggle');
        if (toggleBtn) {
            var commentEl = toggleBtn.closest('.comment');
            var replyForm = commentEl.querySelector('.reply-form');
            if (replyForm) {
                replyForm.hidden = !replyForm.hidden;
                if (!replyForm.hidden) {
                    var ta = replyForm.querySelector('textarea');
                    if (ta) ta.focus();
                    // GIF toggle butonu setup
                    var gifToggleBtn = replyForm.querySelector('.reply-gif-toggle-btn');
                    if (gifToggleBtn && !gifToggleBtn.__setupDone) {
                        gifToggleBtn.__setupDone = true;
                        gifToggleBtn.addEventListener('click', function (ev) {
                            ev.preventDefault();
                            var gifPanel = document.getElementById('gif-picker-reply-panel-' + replyForm.dataset.parentId);
                            if (!gifPanel) {
                                gifPanel = document.createElement('div');
                                gifPanel.id = 'gif-picker-reply-panel-' + replyForm.dataset.parentId;
                                gifPanel.className = 'gif-picker-comment-panel';
                                gifPanel.innerHTML = '<div class="gif-picker-search"><input type="text" class="gif-search-reply" placeholder="GIF ara..." aria-label="GIF ara"></div>' +
                                    '<div class="gif-results-grid gif-results-reply" style="max-height: 250px;"></div>' +
                                    '<p class="muted center gif-loading-reply" hidden style="margin-top: 8px;">Yükleniyor...</p>';
                                replyForm.appendChild(gifPanel);
                                var searchInput = gifPanel.querySelector('.gif-search-reply');
                                var resultsDiv = gifPanel.querySelector('.gif-results-reply');
                                var loadingMsg = gifPanel.querySelector('.gif-loading-reply');
                                if (searchInput) {
                                    var timer = null;
                                    searchInput.addEventListener('input', function () {
                                        var q = this.value;
                                        clearTimeout(timer);
                                        timer = setTimeout(function () {
                                            loadingMsg.hidden = false;
                                            resultsDiv.innerHTML = '';
                                            fetch('/gif/search?q=' + encodeURIComponent(q))
                                                .then(function (r) { return r.json(); })
                                                .then(function (data) {
                                                    loadingMsg.hidden = true;
                                                    if (!data.gifs || data.gifs.length === 0) {
                                                        resultsDiv.innerHTML = '<p class="muted center">Sonuç bulunamadı.</p>';
                                                        return;
                                                    }
                                                    data.gifs.forEach(function (gif) {
                                                        var img = document.createElement('img');
                                                        img.src = gif.preview || gif.url;
                                                        img.alt = 'GIF';
                                                        img.className = 'gif-picker-img';
                                                        img.addEventListener('click', function () {
                                                            var gifUrlInput = replyForm.querySelector('input[name="gif_url"]');
                                                            if (gifUrlInput) gifUrlInput.value = gif.url;
                                                            gifPanel.hidden = true;
                                                        });
                                                        resultsDiv.appendChild(img);
                                                    });
                                                })
                                                .catch(function (e) {
                                                    loadingMsg.hidden = true;
                                                    resultsDiv.innerHTML = '<p class="muted center">Hata: ' + e.message + '</p>';
                                                });
                                        }, 300);
                                    });
                                    searchInput.focus();
                                    searchInput.dispatchEvent(new Event('input'));
                                }
                            } else {
                                gifPanel.hidden = !gifPanel.hidden;
                            }
                        });
                    }
                }
            }
            return;
        }

        // Yorum beğenme
        var likeBtn = e.target.closest('.comment-like-btn');
        if (likeBtn) {
            e.preventDefault();
            if (likeBtn.dataset.busy === '1') return;
            var wasLiked = likeBtn.dataset.liked === '1';
            var nextLiked = !wasLiked;
            var countEl = likeBtn.querySelector('.like-count');
            var prevCount = parseInt(countEl.textContent, 10) || 0;
            likeBtn.dataset.liked = nextLiked ? '1' : '0';
            likeBtn.classList.toggle('liked', nextLiked);
            countEl.textContent = prevCount + (nextLiked ? 1 : -1);
            likeBtn.dataset.busy = '1';
            try {
                var res = await fetch(likeBtn.dataset.likeUrl, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfToken() },
                });
                if (!res.ok) throw new Error('İstek başarısız');
                var data = await res.json();
                likeBtn.dataset.liked = data.liked ? '1' : '0';
                likeBtn.classList.toggle('liked', data.liked);
                countEl.textContent = data.count;
            } catch (err) {
                likeBtn.dataset.liked = wasLiked ? '1' : '0';
                likeBtn.classList.toggle('liked', wasLiked);
                countEl.textContent = prevCount;
                console.error('Beğeni güncellenemedi:', err);
            } finally {
                likeBtn.dataset.busy = '0';
            }
            return;
        }
    });

    // --- Yanıt formu submit (event delegation) ---
    document.addEventListener('submit', async function (e) {
        var replyForm = e.target.closest('.reply-form');
        if (!replyForm) return;
        e.preventDefault();
        // Ana yorum formundaki AYNI bug: Enter'a art arda basmak her seferinde
        // requestSubmit() çağırıyor, burada hiç busy koruması yoktu — aynı
        // yanıt birden fazla kez gönderiliyordu.
        if (replyForm.dataset.busy === '1') return;
        var ta = replyForm.querySelector('textarea');
        var content = ta.value.trim();
        var stickerIdInput = replyForm.querySelector('input[name="sticker_id"]');
        var gifUrlInput = replyForm.querySelector('input[name="gif_url"]');
        var replyStickerVal = stickerIdInput ? stickerIdInput.value : '';
        var replyGifVal = gifUrlInput ? gifUrlInput.value : '';
        // Sticker/GIF seçiliyken metin boş olabilir
        if (!content && !replyStickerVal && !replyGifVal) return;
        replyForm.dataset.busy = '1';
        var parentId = replyForm.dataset.parentId;
        var postId = replyForm.dataset.postId;

        try {
            var formData = new FormData();
            formData.append('content', content);
            if (replyStickerVal) formData.append('sticker_id', replyStickerVal);
            if (replyGifVal) formData.append('gif_url', replyGifVal);
            var res = await fetch('/social/comment/' + postId + '/reply/' + parentId, {
                method: 'POST', body: formData,
                headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfToken() },
            });
            if (!res.ok) throw new Error('İstek başarısız');
            var data = await res.json();

            var commentEl = replyForm.closest('.comment');
            var repliesDiv = commentEl.querySelector('.replies');
            if (!repliesDiv) {
                repliesDiv = document.createElement('div');
                repliesDiv.className = 'replies';
                commentEl.appendChild(repliesDiv);
            }
            var replyArticle = document.createElement('article');
            replyArticle.className = 'comment reply';
            var mediaHtml = '';
            // Optimistic render: sticker veya GIF göster
            if (replyStickerVal) {
                var imgUrl = stickerIdInput.dataset.imageUrl;
                if (imgUrl) {
                    mediaHtml = '<div class="sticker-wrap">' +
                        '<img src="' + escapeHtml(imgUrl) + '" class="sticker-rendered comment-sticker" data-sticker-id="' + escapeHtml(replyStickerVal) + '" alt="Sticker" loading="lazy">' +
                        '<button type="button" class="sticker-star-btn" data-sticker-id="' + escapeHtml(replyStickerVal) + '" aria-label="Sticker\'ı kaydet">' + (window.ICONS ? window.ICONS.get('star', { size: 14 }) : '⭐') + '</button>' +
                        '</div>';
                }
            } else if (replyGifVal) {
                mediaHtml = '<img src="' + escapeHtml(replyGifVal) + '" class="comment-gif" alt="GIF" loading="lazy">';
            }
            replyArticle.innerHTML = buildCommentHtml(data, content) + mediaHtml +
                '<div class="comment-actions">' +
                buildLikeBtnHtml(data.id) +
                buildDeleteFormHtml(data.id, csrfToken()) +
                buildReactWrapHtml(data.id) +
                '</div>';
            repliesDiv.appendChild(replyArticle);
            replyForm.hidden = true;
            ta.value = '';
            ta.style.height = 'auto';
            // Sticker/GIF inputlarını temizle (sonraki yanıt için)
            stickerIdInput.value = '';
            stickerIdInput.dataset.imageUrl = '';
            gifUrlInput.value = '';
        } catch (err) {
            console.error('Yanıt gönderilemedi:', err);
            alert('Yanıt gönderilemedi.');
        } finally {
            replyForm.dataset.busy = '0';
        }
    });

    // === Yorum Emoji Tepkileri — Document Delegation ===
    // chat.js'teki mesaj reaksiyon bloğunun (chat.js:774-924) yorumlara
    // uyarlanmış hali. Fark: chip'ler/trigger/picker burada .comment-react-wrap
    // içinde toplu duruyor (mesajlarda doğrudan .msg altında). position:fixed
    // kullanılıyor — sebep aynı: absolute konumlanma sayfayı büyütmesin.

    function closeAllCommentReactPickers(except) {
        document.querySelectorAll('.comment-react-picker:not([hidden])').forEach(function (p) {
            if (p !== except) p.setAttribute('hidden', '');
        });
    }

    function setCommentChipCount(chip, count) {
        if (count <= 0) {
            chip.remove();
            return;
        }
        chip.dataset.count = count;
        chip.textContent = chip.dataset.reaction + ' ' + count;
        chip.title = count + ' kişi';
    }

    function applyCommentReactionResult(wrap, emoji, newReaction) {
        if (newReaction) {
            wrap.querySelectorAll('.comment-reaction-chip.mine').forEach(function (old) {
                if (old.dataset.reaction !== newReaction) {
                    old.classList.remove('mine');
                    setCommentChipCount(old, parseInt(old.dataset.count || 1, 10) - 1);
                }
            });
            var chip = wrap.querySelector('.comment-reaction-chip[data-reaction="' + newReaction + '"]');
            if (!chip) {
                var reactionsDiv = wrap.querySelector('.comment-reactions');
                if (!reactionsDiv) {
                    reactionsDiv = document.createElement('div');
                    reactionsDiv.className = 'comment-reactions';
                    wrap.insertBefore(reactionsDiv, wrap.firstChild);
                }
                chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'comment-reaction-chip mine';
                chip.dataset.reaction = newReaction;
                reactionsDiv.appendChild(chip);
                setCommentChipCount(chip, 1);
            } else if (!chip.classList.contains('mine')) {
                chip.classList.add('mine');
                setCommentChipCount(chip, parseInt(chip.dataset.count || 1, 10) + 1);
            }
        } else {
            var removed = wrap.querySelector('.comment-reaction-chip[data-reaction="' + emoji + '"]');
            if (removed) {
                removed.classList.remove('mine');
                setCommentChipCount(removed, parseInt(removed.dataset.count || 1, 10) - 1);
            }
        }
    }

    function sendCommentReaction(wrap, emoji) {
        var reactUrl = wrap.dataset.reactUrl;
        if (!reactUrl) return;

        fetch(reactUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken()
            },
            body: JSON.stringify({ reaction: emoji })
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (!data.ok) return;
            applyCommentReactionResult(wrap, emoji, data.reaction);
        })
        .catch(function (err) {
            console.error('Yorum tepkisi gönderilemedi:', err);
        });
    }

    document.addEventListener('click', function (e) {
        var list = e.target.closest('#comment-list');
        if (!list) {
            closeAllCommentReactPickers();
            return;
        }

        var trigger = e.target.closest('.comment-react-trigger');
        if (trigger) {
            e.preventDefault();
            var wrap = trigger.closest('.comment-react-wrap');
            if (wrap) {
                var picker = wrap.querySelector('.comment-react-picker');
                if (picker) {
                    if (picker.hasAttribute('hidden')) {
                        closeAllCommentReactPickers(picker);
                        var rect = trigger.getBoundingClientRect();
                        picker.style.top = (rect.bottom + 4) + 'px';
                        picker.style.left = rect.left + 'px';
                        picker.removeAttribute('hidden');
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

        var emojiBtn = e.target.closest('.comment-react-picker button[data-emoji]');
        if (emojiBtn) {
            e.preventDefault();
            var wrapEl = emojiBtn.closest('.comment-react-wrap');
            var pickerEl = emojiBtn.closest('.comment-react-picker');
            if (wrapEl && pickerEl) {
                pickerEl.setAttribute('hidden', '');
                sendCommentReaction(wrapEl, emojiBtn.dataset.emoji);
            }
            return;
        }

        var chip = e.target.closest('.comment-reaction-chip');
        if (chip && !chip.closest('.comment-react-picker')) {
            e.preventDefault();
            var chipWrap = chip.closest('.comment-react-wrap');
            if (chipWrap) sendCommentReaction(chipWrap, chip.dataset.reaction);
        }
    });

    // Sayfa scroll'unda açık picker'ları kapat — position:fixed olduğu için
    // scroll ile birlikte kaymaz (chat.js'teki #stream scroll listener'ının
    // eşdeğeri, burada scroll konteyneri sayfanın kendisi).
    window.addEventListener('scroll', function () {
        closeAllCommentReactPickers();
    }, { passive: true });
})();
