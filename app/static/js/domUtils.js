// Ortak DOM yardımcıları — icons.js'in window.ICONS deseniyle AYNI mantık:
// tek kaynaktan global'a yazılır, diğer dosyalar onu okur (bkz. build-js.mjs
// üstündeki açıklama). escapeHtml() önceden chat.js, comments.js, stories.js,
// storyHighlights.js içinde AYRI AYRI (4 kopya, hepsi birebir aynı) tanımlıydı —
// denetimde çıkan teknik borç maddesi olarak TEK modüle taşındı.
//
// MANIFEST'te (scripts/build-js.mjs) bu dosyayı kullanan HER bundle'da o
// dosyadan ÖNCE sıralanmalı — 'common' bundle'a eklendi, HER sayfada (base.html)
// diğer tüm bundle'lardan önce yüklendiği için ek bir sıralama gerekmedi.
(function () {
    // textContent -> innerHTML tricki: tarayıcının kendi HTML entity encode'unu
    // kullanır (&, <, >, " vb.), regex tabanlı elle kaçışlamadan daha güvenilir.
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    window.escapeHtml = escapeHtml;
})();
