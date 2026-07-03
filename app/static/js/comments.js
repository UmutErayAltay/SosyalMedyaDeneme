// AJAX yorum gönderme — sayfa yenilenmeden DOM'a yorum ekler

(function () {
    var form = document.getElementById('comment-form');
    var input = document.getElementById('comment-input');
    var list = document.getElementById('comment-list');
    if (!form || !input || !list) return;

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
            // CSRF için fetch ile POST (form data)
            var formData = new FormData();
            formData.append('content', content);

            var res = await fetch('/social/comment/' + postId, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'fetch' },
            });

            if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
            var data = await res.json();

            // Boş durum mesajını kaldır
            var emptyMsg = list.querySelector('.muted');
            if (emptyMsg) emptyMsg.remove();

            // Yeni yorumu DOM'a ekle
            var article = document.createElement('article');
            article.className = 'card comment';
            article.dataset.commentId = data.id;

            var me = window.ME || {};
            var username = data.username || 'Sen';
            var avatarHtml = data.avatar_url
                ? '<img src="' + data.avatar_url + '" class="avatar avatar-sm" alt="" loading="lazy">'
                : '<div class="avatar avatar-sm avatar-placeholder" aria-hidden="true"></div>';

            article.innerHTML =
                '<div class="comment-meta">' +
                    avatarHtml +
                    '<a href="/u/' + encodeURIComponent(username) + '" class="username">' + username + '</a>' +
                    '<span class="time">şimdi</span>' +
                '</div>' +
                '<p>' + escapeHtml(content) + '</p>';

            list.appendChild(article);
            input.value = '';
            input.focus();
        } catch (err) {
            console.error('Yorum gönderilemedi:', err);
            alert('Yorum gönderilemedi. Tekrar dene.');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    });

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
})();
