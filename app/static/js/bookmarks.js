// Kaydet butonu — optimistic UI ile AJAX toggle (beğeni butonuyla aynı desen,
// ama reaksiyon seçici yok, sadece iki durum: kaydedildi / kaydedilmedi).

document.addEventListener('click', async function (e) {
    var btn = e.target.closest('.bookmark-btn');
    if (!btn) return;
    e.preventDefault();
    if (btn.dataset.busy === '1') return;

    var wasBookmarked = btn.dataset.bookmarked === '1';
    var nextBookmarked = !wasBookmarked;
    btn.dataset.bookmarked = nextBookmarked ? '1' : '0';
    btn.classList.toggle('bookmarked', nextBookmarked);
    btn.textContent = nextBookmarked ? '🔖' : '📑';
    btn.dataset.busy = '1';

    try {
        var res = await fetch(btn.dataset.bookmarkUrl, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'fetch',
                'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
        });
        if (!res.ok) throw new Error('İstek başarısız: ' + res.status);
        var data = await res.json();
        btn.dataset.bookmarked = data.bookmarked ? '1' : '0';
        btn.classList.toggle('bookmarked', data.bookmarked);
        btn.textContent = data.bookmarked ? '🔖' : '📑';
        btn.setAttribute('aria-label', data.bookmarked ? 'Kaydedilenlerden kaldır' : 'Kaydet');
    } catch (err) {
        btn.dataset.bookmarked = wasBookmarked ? '1' : '0';
        btn.classList.toggle('bookmarked', wasBookmarked);
        btn.textContent = wasBookmarked ? '🔖' : '📑';
        console.error('Kaydetme güncellenemedi:', err);
    } finally {
        btn.dataset.busy = '0';
    }
});
