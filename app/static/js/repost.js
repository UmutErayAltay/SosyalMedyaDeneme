// app/static/js/repost.js
// Repost (yeniden paylaşım) özelliği — alıntı metni ile

(function () {
    const modal = document.getElementById('repost-modal');
    const closeBtn = document.getElementById('close-repost-modal');
    const contentInput = document.getElementById('repost-content-input');
    const submitBtn = document.getElementById('repost-submit-btn');

    if (!modal) return;

    let currentPostId = null;
    let lastFocused = null;

    // Repost butonlarını dinle (document-level delegation)
    document.body.addEventListener('click', function (e) {
        const btn = e.target.closest('.repost-btn');
        if (btn) {
            currentPostId = btn.getAttribute('data-post-id');
            openModal();
        }
    });

    function openModal() {
        lastFocused = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';

        contentInput.value = '';

        setTimeout(() => contentInput.focus(), 50);
    }

    function closeModal() {
        modal.hidden = true;
        document.body.style.overflow = '';

        currentPostId = null;
        contentInput.value = '';

        // Odağı modalı açan butona geri döndür (WCAG)
        if (lastFocused) lastFocused.focus();
    }

    closeBtn.addEventListener('click', closeModal);

    // Focus trap — modal açıkken Tab modal içinde kalır
    modal.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        const focusable = modal.querySelectorAll(
            'button:not([disabled]), textarea, input, a[href], [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });

    modal.addEventListener('click', function (e) {
        if (e.target === modal) {
            closeModal();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) {
            closeModal();
        }
    });

    // REPOST GÖNDER
    submitBtn.addEventListener('click', async function () {
        if (!currentPostId) {
            return;
        }

        submitBtn.disabled = true;
        const originalText = submitBtn.textContent;
        submitBtn.textContent = 'Gönderiliyor...';

        try {
            const res = await fetch(`/post/${currentPostId}/repost`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: new URLSearchParams({
                    content: contentInput.value
                })
            });

            if (res.status === 409) {
                // Zaten reposted
                if (typeof appAlert !== 'undefined') {
                    appAlert('Bu gönderiyi zaten yeniden paylaşmışsın');
                } else {
                    alert('Bu gönderiyi zaten yeniden paylaşmışsın');
                }
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
                return;
            }

            if (res.status === 403 || res.status === 400) {
                // Yapılamaz: private, blocked, not public vb.
                if (typeof appAlert !== 'undefined') {
                    appAlert('Bu gönderi yeniden paylaşılamaz');
                } else {
                    alert('Bu gönderi yeniden paylaşılamaz');
                }
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
                return;
            }

            if (!res.ok) {
                throw new Error("Repost başarısız.");
            }

            // BAŞARILI — buton durumu da sıfırlanır, yoksa modal bir sonraki
            // açılışta "Gönderiliyor..." halinde kilitli kalırdı
            if (typeof appAlert !== 'undefined') {
                appAlert('Yeniden paylaşıldı ✓');
            } else {
                alert('Yeniden paylaşıldı');
            }
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
            closeModal();

        } catch (err) {
            console.error(err);
            if (typeof appAlert !== 'undefined') {
                appAlert('Yeniden paylaşım sırasında hata oluştu');
            } else {
                alert('Yeniden paylaşım sırasında hata oluştu');
            }
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    });

})();
