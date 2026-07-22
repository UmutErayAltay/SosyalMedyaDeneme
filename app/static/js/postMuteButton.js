// Başkasının postundaki sessize alma butonu (feed/discover'da görülen başka kullanıcıların
// postları) — form submit'i fetch'e çevirerek sayfayı yenilenmeden state'i günceller.
// Document-level delegation kullanılır (AJAX ile yeniden eklenen kartlarda çalışması için).

(function () {
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form.action.includes('/post/') || !form.action.includes('/mute')) {
            return;
        }

        var button = form.querySelector('button[type="submit"]');
        if (!button || !button.classList.contains('mute-post-btn')) {
            return;
        }

        e.preventDefault();

        var isMuted = button.dataset.muted === '1';
        var newMuted = !isMuted;

        fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRF-Token': document.querySelector('input[name="csrf_token"]').value,
                'X-Requested-With': 'fetch'
            }
        })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) {
                // Başarılı: buton state'ini güncelle
                if (data.ok) {
                    button.dataset.muted = newMuted ? '1' : '0';
                    var svg = button.querySelector('svg');
                    var text = button.textContent.trim();

                    // İkonu değiştir: bell-off <-> bell (SVG class veya data-attr ile)
                    // Sunucunun bell-off/bell dönüşünü takip edemeyeceğimiz için
                    // form action'undan ikonem tekrar render ettirtmeliyiz... VEYA
                    // Jinja'da renderlanmış olsa da, burada HTML'i elle parse etmeliyiz.
                    // Basit çözüm: icon adlarını CSS gibi yönetmek yerine,
                    // sunucu başkasının postundaki SVG'yi HTML içinde template etmiş.
                    // İkon değişimi için: window.ICONS.get() kullanılabilir ama
                    // başkasının postunda icon DOM'da zaten var. Basit: harf değişimi.

                    // Mute/unmute state'ine göre aria-label + buton metni güncelle
                    if (newMuted) {
                        button.setAttribute('aria-label', 'Bildirimleri Aç');
                        // İkonu bell-off'a çevir — SVG yerine yeniden render etmekle
                        // karmaşıklık artacak. Bunun yerine CSS class'ı toggle et
                        // veya data-attr ile işaretle. Şu an HTML'de icon hardcoded
                        // olduğu için, ikonu güncellememek (sadece aria-label)
                        // third-party tool gerektirmez. Kullanıcı ikonun durumunu
                        // text'ten anlayacak (ve sonraki yükleme'de doğru görünür).
                        // VEYA sunucu endpoint JSON dönüşünde icon data döner.
                        // Şu an sadece ok:true/false döndürdüğü için, ikonu manuel
                        // fetch'le değiştir — window.ICONS global'den oku.
                        if (window.ICONS) {
                            var bellOffIcon = window.ICONS.get('bell-off', { size: 16 });
                            if (bellOffIcon) {
                                button.innerHTML = bellOffIcon;
                            }
                        }
                    } else {
                        button.setAttribute('aria-label', 'Bildirimleri Sessize Al');
                        if (window.ICONS) {
                            var bellIcon = window.ICONS.get('bell', { size: 16 });
                            if (bellIcon) {
                                button.innerHTML = bellIcon;
                            }
                        }
                    }
                }
            })
            .catch(function (err) {
                console.error('Mute işlemi başarısız:', err);
            });
    });
})();
