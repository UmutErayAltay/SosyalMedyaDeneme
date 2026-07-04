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

    function buildCommentHtml(data, content) {
        var username = data.username || 'Sen';
        var avatarHtml = data.avatar_url
            ? '<img src="' + escapeHtml(data.avatar_url) + '" class="avatar avatar-sm" alt="" loading="lazy">'
            : '<div class="avatar avatar-sm avatar-placeholder" aria-hidden="true"></div>';
        return '<div class="comment-meta">' + avatarHtml +
            '<a href="/u/' + encodeURIComponent(username) + '" class="username">' + escapeHtml(username) + '</a>' +
            '<span class="time">şimdi</span></div>' +
            '<p>' + escapeHtml(content) + '</p>';
    }

    // --- Ana yorum gönderme ---
    var form = document.getElementById('comment-form');
    var input = document.getElementById('comment-input');
    var list = document.getElementById('comment-list');
    if (form && input && list) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var content = input.value.trim();
            if (!content) return;
            var postId = form.dataset.postId;
            var submitBtn = form.querySelector('button[type="submit"]');
            var originalText = submitBtn.textContent;
            submitBtn.disabled = true;
            submitBtn.textContent = 'Gönderiliyor...';
            try {
                var formData = new FormData();
                formData.append('content', content);
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
                article.innerHTML = buildCommentHtml(data, content) +
                    '<div class="comment-actions">' +
                    '<button type="button" class="btn btn-ghost small reply-toggle" data-comment-id="' + data.id + '">Yanıtla</button>' +
                    '</div>' +
                    '<form class="reply-form" data-parent-id="' + data.id + '" data-post-id="' + postId + '" hidden>' +
                    '<textarea name="content" placeholder="Yanıtını yaz..." rows="2"></textarea>' +
                    '<button type="submit" class="btn btn-primary small">Gönder</button>' +
                    '</form>';
                list.appendChild(article);
                input.value = '';
                input.focus();
            } catch (err) {
                console.error('Yorum gönderilemedi:', err);
                alert('Yorum gönderilemedi.');
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        });
    }

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
        var ta = replyForm.querySelector('textarea');
        var content = ta.value.trim();
        if (!content) return;
        var parentId = replyForm.dataset.parentId;
        var postId = replyForm.dataset.postId;

        try {
            var formData = new FormData();
            formData.append('content', content);
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
            replyArticle.innerHTML = buildCommentHtml(data, content) +
                '<div class="comment-actions">' +
                '<button type="button" class="btn btn-ghost small comment-like-btn" data-liked="0" ' +
                'data-like-url="/social/comment/like/' + data.id + '">♥ <span class="like-count">0</span></button>' +
                '</div>';
            repliesDiv.appendChild(replyArticle);
            replyForm.hidden = true;
            ta.value = '';
        } catch (err) {
            console.error('Yanıt gönderilemedi:', err);
            alert('Yanıt gönderilemedi.');
        }
    });
})();
