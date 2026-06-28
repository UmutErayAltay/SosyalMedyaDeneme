// Mesaj akışını en alta kaydır (yeni mesaj geldiğinde)
(function () {
    var stream = document.getElementById("stream");
    if (stream) stream.scrollTop = stream.scrollHeight;
})();
