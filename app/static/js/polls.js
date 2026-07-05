// Anket oylama — AJAX, tıklanan seçeneğe göre sonuç barlarını günceller.

(function () {
    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    document.addEventListener('click', async function (e) {
        var btn = e.target.closest('.poll-option');
        if (!btn) return;
        e.preventDefault();
        if (btn.dataset.busy === '1') return;
        btn.dataset.busy = '1';

        var widget = btn.closest('.poll-widget');
        var voteUrl = btn.dataset.voteUrl;
        var optionId = btn.dataset.optionId;

        try {
            var formData = new FormData();
            formData.append('option_id', optionId);
            formData.append('csrf_token', csrfToken());

            var res = await fetch(voteUrl, {
                method: 'POST', body: formData,
                headers: { 'X-Requested-With': 'fetch', 'X-CSRF-Token': csrfToken() },
            });
            if (!res.ok) throw new Error('İstek başarısız');
            var data = await res.json();

            var totalEl = widget.querySelector('.poll-total');
            if (totalEl) totalEl.textContent = data.total_votes + ' oy';

            data.options.forEach(function (opt) {
                var optBtn = widget.querySelector('[data-option-id="' + opt.id + '"]');
                if (!optBtn) return;
                optBtn.classList.toggle('voted', data.my_vote === opt.id);
                var bar = optBtn.querySelector('.poll-option-bar');
                if (bar) bar.style.width = opt.pct + '%';
                var pct = optBtn.querySelector('.poll-option-pct');
                if (pct) pct.textContent = opt.pct + '%';
                optBtn.setAttribute('aria-label', opt.text + ', yüzde ' + opt.pct + ', ' + opt.votes + ' oy');
            });
        } catch (err) {
            console.error('Oy kullanılamadı:', err);
        } finally {
            btn.dataset.busy = '0';
        }
    });
})();
