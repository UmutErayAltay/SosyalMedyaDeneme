// Rozetlerin GERÇEK-ZAMANLI tazelenmesi — notifications tablosuna DB-düzeyi
// abonelik (postgres_changes). Önceden zil ve Mesajlar rozetleri yalnızca
// 25sn'lik polling ile dönüyordu; bildirim en kötü ~45sn geç görünüyordu
// (25sn poll + 20sn sunucu cache'i). Artık kendi bildirim satırıma her
// INSERT/UPDATE'te rozetler saniyeler içinde tazelenir; polling güvenlik
// ağı olarak kalır (realtime koparsa en geç 25sn'de düzelir).
//
// Güvenlik: RLS "notifications read" policy'si (recipient_id = auth.uid())
// sayesinde WALRUS yalnızca KENDİ satırlarımı iletir — açık sohbetin mesaj
// aboneliğiyle (chat.js activeChannel) birebir aynı, canlıda çalışan desen.
// _supabase_core.html yükler (login'li her sayfa), call.js'ten sonra.

(function () {
    if (!window.ME_ID) return;

    var timer = null;
    function refreshBadges() {
        // Art arda gelen olaylarda (örn. hızlı mesaj serisi) tek fetch
        clearTimeout(timer);
        timer = setTimeout(function () {
            if (window.refreshNotifBadge) window.refreshNotifBadge();
            if (window.refreshMessagesBadge) window.refreshMessagesBadge();
        }, 250);
    }

    function handleChange(payload) {
        refreshBadges();
        // Mesaj bildirimi + mesajlar sayfası açıksa: açık OLMAYAN sohbetin
        // inbox satırını en üste taşı (önizleme metni bildirimde yok —
        // yalnızca sıralama; metin sohbete girince/poll'da tazelenir)
        var row = payload && payload.new;
        if (row && row.type === 'message' && row.conversation_id && window._bumpInboxItem) {
            window._bumpInboxItem(row.conversation_id);
        }
    }

    // Bayat token'la subscribe CHANNEL_ERROR üretebilir — core'un taze
    // token fetch'i beklenir (call.js ile aynı desen)
    (window.SB_TOKEN_READY || Promise.resolve()).then(function () {
        if (!window.supabaseClient) return;
        window.supabaseClient
            .channel('notif-db:' + window.ME_ID)
            .on('postgres_changes', {
                event: '*',
                schema: 'public',
                table: 'notifications',
                filter: 'recipient_id=eq.' + window.ME_ID
            }, handleChange)
            .subscribe(function (status) {
                if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                    // Polling güvenlik ağı devrede — sessiz log yeterli
                    console.warn('[liveBadges] kanal durumu: ' + status);
                }
            });
    });
})();
