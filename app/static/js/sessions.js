(function() {
    'use strict';

    // CSRF token'ını meta tag'ından al
    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    // Oturum kapatma işlemi
    async function revokeSession(sessionId) {
        try {
            var response = await fetch('/sessions/' + sessionId + '/revoke', {
                method: 'POST',
                headers: {
                    'X-CSRF-Token': getCsrfToken(),
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                var data = await response.json();
                if (response.status === 400 && data.error === 'use_logout') {
                    // Kendi oturum → logout gerekli
                    if (typeof window.appAlert === 'function') {
                        window.appAlert('Bu oturum sonlandırılamaz. Çıkış yap ve yeniden giriş yapmayı dene.');
                    } else {
                        alert('Bu oturum sonlandırılamaz. Çıkış yap ve yeniden giriş yapmayı dene.');
                    }
                } else {
                    // Diğer hatalar (403 vb.)
                    if (typeof window.appAlert === 'function') {
                        window.appAlert('Oturum sonlandırılamadı. Lütfen tekrar dene.');
                    } else {
                        alert('Oturum sonlandırılamadı. Lütfen tekrar dene.');
                    }
                }
                return;
            }

            // Başarılı: satırı DOM'dan kaldır
            var btn = document.querySelector('[data-session-id="' + sessionId + '"]');
            if (btn) {
                var row = btn.closest('.session-row');
                if (row) {
                    row.remove();
                }
            }
        } catch (err) {
            if (typeof window.appAlert === 'function') {
                window.appAlert('Bağlantı hatası: ' + err.message);
            } else {
                alert('Bağlantı hatası: ' + err.message);
            }
        }
    }

    // Diğer tüm oturumları kapatma
    async function revokeOthers() {
        try {
            var response = await fetch('/sessions/revoke-others', {
                method: 'POST',
                headers: {
                    'X-CSRF-Token': getCsrfToken(),
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                if (typeof window.appAlert === 'function') {
                    window.appAlert('İşlem başarısız. Lütfen tekrar dene.');
                } else {
                    alert('İşlem başarısız. Lütfen tekrar dene.');
                }
                return;
            }

            // Başarılı: sayfayı yenile
            location.reload();
        } catch (err) {
            if (typeof window.appAlert === 'function') {
                window.appAlert('Bağlantı hatası: ' + err.message);
            } else {
                alert('Bağlantı hatası: ' + err.message);
            }
        }
    }

    // Event delegation: .session-revoke-btn click'leri
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('session-revoke-btn')) {
            var sessionId = e.target.getAttribute('data-session-id');
            if (sessionId) {
                revokeSession(sessionId);
            }
        }
    });

    // Diğer tüm oturumları kapat butonu
    var revokeOthersBtn = document.getElementById('revoke-others-btn');
    if (revokeOthersBtn) {
        revokeOthersBtn.addEventListener('click', async function() {
            // appConfirm var mı kontrol et, yoksa native confirm
            if (typeof window.appConfirm === 'function') {
                var confirmed = await window.appConfirm('Diğer tüm oturumları kapatmak istediğinizden emin misiniz?');
                if (confirmed) {
                    revokeOthers();
                }
            } else {
                if (confirm('Diğer tüm oturumları kapatmak istediğinizden emin misiniz?')) {
                    revokeOthers();
                }
            }
        });
    }
})();
