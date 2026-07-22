// Sesli mesaj dalga formu — sohbet balonlarındaki .voice-player'ları özel
// play/pause + tıkla-atla arayüzüne kavuşturur. Native <audio> gizli kalıp
// SADECE oynatma motoru olarak kullanılır; dalga formu decodeAudioData ile
// tek seferlik hesaplanıp CSS bar'lar olarak çizilir (canvas yerine — DOM
// bar'ların CSS ile "played" durumu boyamak çok daha basit).
//
// Bu dosya document-level delegation KULLANMAZ (chat.js'teki gibi) çünkü
// her player kendi <audio> elementine 1:1 bağlanır ve mesaj panelinin AJAX
// yenilemesinde eski player'lar DOM'dan tamamen kalkar — yeni gelenler
// initVoicePlayers() ile ayrıca kurulur (bkz. chat.js initConversation +
// appendMessage çağrıları).

(function () {
    var BAR_COUNT = 28;

    function formatTime(sec) {
        if (!isFinite(sec) || sec < 0) sec = 0;
        var m = Math.floor(sec / 60);
        var s = Math.floor(sec % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function renderFlatBars(waveformEl) {
        var html = '';
        for (var i = 0; i < BAR_COUNT; i++) {
            html += '<span class="voice-bar" style="height:35%"></span>';
        }
        waveformEl.innerHTML = html;
    }

    function renderPeakBars(waveformEl, peaks) {
        var html = '';
        for (var i = 0; i < peaks.length; i++) {
            var h = Math.round(15 + peaks[i] * 85); // %10-100 arası, çok kısa barlar görünmesin diye taban 15
            html += '<span class="voice-bar" style="height:' + h + '%"></span>';
        }
        waveformEl.innerHTML = html;
    }

    function computePeaks(audioBuffer, barCount) {
        var data = audioBuffer.getChannelData(0);
        var blockSize = Math.max(1, Math.floor(data.length / barCount));
        var peaks = [];
        for (var i = 0; i < barCount; i++) {
            var start = i * blockSize;
            var max = 0;
            for (var j = 0; j < blockSize && start + j < data.length; j++) {
                var v = Math.abs(data[start + j]);
                if (v > max) max = v;
            }
            peaks.push(max);
        }
        var maxPeak = Math.max.apply(null, peaks) || 1;
        return peaks.map(function (p) { return p / maxPeak; });
    }

    function updateProgress(bars, ratio) {
        var filled = Math.round(ratio * bars.length);
        for (var i = 0; i < bars.length; i++) {
            bars[i].classList.toggle('played', i < filled);
        }
    }

    function initVoicePlayer(wrapper) {
        if (wrapper.dataset.wfInit === '1') return;
        wrapper.dataset.wfInit = '1';

        var audio = wrapper.querySelector('audio.msg-audio');
        var playBtn = wrapper.querySelector('.voice-play-btn');
        var speedBtn = wrapper.querySelector('.voice-speed-btn');
        var waveform = wrapper.querySelector('.voice-waveform');
        var durationEl = wrapper.querySelector('.voice-duration');
        if (!audio || !playBtn || !waveform || !durationEl) return;

        renderFlatBars(waveform);
        var bars = Array.prototype.slice.call(waveform.querySelectorAll('.voice-bar'));

        // Süre: metadata yüklenince güncellenir (bazı tarayıcılarda audio_url
        // her zaman doğru duration vermeyebilir — decodeAudioData'dan gelen
        // gerçek buffer süresi öncelikli, o gelene kadar audio.duration kullanılır).
        audio.addEventListener('loadedmetadata', function () {
            if (isFinite(audio.duration)) durationEl.textContent = formatTime(audio.duration);
        });

        fetch(audio.src)
            .then(function (r) { return r.arrayBuffer(); })
            .then(function (buf) {
                var Ctx = window.AudioContext || window.webkitAudioContext;
                if (!Ctx) throw new Error('Web Audio API yok');
                var ctx = new Ctx();
                return ctx.decodeAudioData(buf).then(function (audioBuffer) {
                    var peaks = computePeaks(audioBuffer, BAR_COUNT);
                    renderPeakBars(waveform, peaks);
                    bars = Array.prototype.slice.call(waveform.querySelectorAll('.voice-bar'));
                    durationEl.textContent = formatTime(audioBuffer.duration);
                    ctx.close();
                });
            })
            .catch(function () {
                // Dalga formu çizilemedi (CORS/decode hatası) — düz bar'larla
                // oynatma yine de tam çalışır durumda kalır, sessizce geç.
            });

        playBtn.addEventListener('click', function () {
            if (audio.paused) {
                // Aynı anda tek sesli mesaj çalsın — diğer açık oynatıcılar durur
                document.querySelectorAll('.voice-player audio.msg-audio').forEach(function (a) {
                    if (a !== audio && !a.paused) a.pause();
                });
                audio.play();
            } else {
                audio.pause();
            }
        });

        // Oynatma hızı kontrolü: 1x → 1.5x → 2x → 1x döngüsü
        if (speedBtn) {
            var speeds = [1, 1.5, 2];
            var currentSpeedIndex = 0;

            speedBtn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                currentSpeedIndex = (currentSpeedIndex + 1) % speeds.length;
                var newSpeed = speeds[currentSpeedIndex];
                audio.playbackRate = newSpeed;
                speedBtn.textContent = newSpeed === 1 ? '1x' : newSpeed + 'x';
            });
        }

        audio.addEventListener('play', function () {
            playBtn.innerHTML = window.ICONS ? window.ICONS.get('pause', { size: 14 }) : '⏸';
            playBtn.setAttribute('aria-label', 'Sesli mesajı duraklat');
        });
        audio.addEventListener('pause', function () {
            playBtn.innerHTML = window.ICONS ? window.ICONS.get('play', { size: 14 }) : '▶';
            playBtn.setAttribute('aria-label', 'Sesli mesajı oynat');
        });
        audio.addEventListener('ended', function () {
            updateProgress(bars, 0);
        });
        audio.addEventListener('timeupdate', function () {
            if (!audio.duration) return;
            updateProgress(bars, audio.currentTime / audio.duration);
        });

        waveform.addEventListener('click', function (e) {
            if (!audio.duration) return;
            var rect = waveform.getBoundingClientRect();
            var ratio = (e.clientX - rect.left) / rect.width;
            ratio = Math.min(1, Math.max(0, ratio));
            audio.currentTime = ratio * audio.duration;
            updateProgress(bars, ratio);
        });
    }

    window.initVoicePlayers = function (root) {
        if (!root) return;
        var scope = root.querySelectorAll ? root : document;
        var nodes = scope.querySelectorAll ? scope.querySelectorAll('.voice-player') : [];
        // root'un KENDİSİ de bir .voice-player olabilir (appendMessage tek bir
        // mesaj div'i döndürür, .voice-player onun içinde bir alt eleman) —
        // querySelectorAll yeterli, root'un kendisini kontrol etmeye gerek yok
        // çünkü .msg div'i asla .voice-player class'ı taşımıyor.
        nodes.forEach(initVoicePlayer);
    };
})();
