// WebRTC 1:1 Sesli/Görüntülü Arama — Supabase Realtime sinyalleşmesi
// Yazıyor göstergesi desenine paralel: aktif kanalı paylaşır, broadcast event'lerle çalışır.
// Sinyalleşme: offer/answer/ice/hangup/reject event'leri messages:<conversation_id> kanalında

(function () {
    var state = {
        conversationId: null,
        meId: null,
        otherId: null,
        isVideoCall: false, // sesli vs. görüntülü
        peerConnection: null,
        localStream: null,
        remoteStream: null,
        signalingChannel: null, // aktif channel (chat.js'den paylaşılan)
        callsChannel: null, // calls:<meId> kanalı (global arama dinlemesi)
        callState: 'idle', // idle, ringing, active, ended
        callStartedAt: null,
        iceCandidateQueue: [], // answer set edilmeden önce gelen ICE adayları
        callDurationInterval: null,
        noAnswerTimeout: null, // arayan tarafında 30sn sonra timeout
        isCaller: false,       // arama sonucu mesajını yalnızca ARAYAN gönderir (çift kayıt olmasın)
        callAnswered: false,   // answer alındı mı (süre mesajı için)
        otherUsername: '',    // karşı tarafın adı (tam ekran arama ekranında gösterilir)
        otherAvatar: '',       // karşı tarafın avatar URL'i (boşsa 👤 fallback)
    };

    // --- Yardımcı Fonksiyonlar ---

    function log(msg) {
        console.log('[WebRTC Arama] ' + msg);
    }

    function showAlert(msg) {
        alert(msg);
    }

    // Format mm:ss
    function formatDuration(ms) {
        var totalSec = Math.floor(ms / 1000);
        var m = Math.floor(totalSec / 60);
        var s = totalSec % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    // Gelen arama modal ve arama overlay DOM'unu oluştur (panel yoksa)
    function ensureCallDOM() {
        if (!document.getElementById('call-modal-incoming')) {
            var modalHtml = '<div class="modal-overlay" id="call-modal-incoming" role="dialog" aria-modal="true" aria-labelledby="call-modal-incoming-title" hidden>\n' +
                '    <div class="modal">\n' +
                '        <div class="modal-header">\n' +
                '            <h2 id="call-modal-incoming-title">Gelen Arama</h2>\n' +
                '        </div>\n' +
                '        <div class="modal-body call-modal-body">\n' +
                '            <div class="call-avatar-circle call-avatar-circle--md" id="incoming-call-avatar" style="margin-bottom: 12px;">\n' +
                '                <img id="incoming-call-avatar-img" alt="" hidden>\n' +
                '                <span id="incoming-call-avatar-fallback" aria-hidden="true">👤</span>\n' +
                '            </div>\n' +
                '            <p id="incoming-call-name" style="text-align: center; font-weight: 600; margin-bottom: 16px;"></p>\n' +
                '            <div style="display: flex; gap: 12px; justify-content: center;">\n' +
                '                <button type="button" class="btn btn-primary" id="incoming-call-accept-btn">✓ Kabul Et</button>\n' +
                '                <button type="button" class="btn btn-ghost danger" id="incoming-call-reject-btn">✗ Reddet</button>\n' +
                '            </div>\n' +
                '        </div>\n' +
                '    </div>\n' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', modalHtml);
        }

        // Giden arama kartı: karşı taraf kabul edene kadar tam ekran arayüz
        // YERİNE gösterilen, siteye uygun küçük kart (kullanıcı isteği)
        if (!document.getElementById('call-outgoing-panel')) {
            var outgoingHtml = '<div class="call-outgoing-panel" id="call-outgoing-panel" role="status" hidden>\n' +
                '    <div class="call-outgoing-pulse" aria-hidden="true">📞</div>\n' +
                '    <div class="call-outgoing-text">\n' +
                '        <strong id="call-outgoing-name"></strong>\n' +
                '        <span class="muted">aranıyor<span class="call-dots"><span>.</span><span>.</span><span>.</span></span></span>\n' +
                '    </div>\n' +
                '    <button type="button" class="btn btn-danger small" id="call-outgoing-cancel">Vazgeç</button>\n' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', outgoingHtml);
            document.getElementById('call-outgoing-cancel').addEventListener('click', function () {
                endCall();
            });
        }

        if (!document.getElementById('call-overlay')) {
            var overlayHtml = '<div class="call-overlay" id="call-overlay" hidden>\n' +
                '    <div class="call-remote-avatar" id="call-remote-avatar" hidden>\n' +
                '        <div class="call-avatar-circle call-avatar-circle--lg" id="call-remote-avatar-circle">\n' +
                '            <img id="call-remote-avatar-img" alt="" hidden>\n' +
                '            <span id="call-remote-avatar-fallback" aria-hidden="true">👤</span>\n' +
                '        </div>\n' +
                '        <p id="call-remote-name" class="call-remote-name"></p>\n' +
                '        <p id="call-voice-info" style="margin-top: 4px; color: var(--card); font-size: 16px;">Sesli Arama</p>\n' +
                '    </div>\n' +
                '    <video id="call-remote-video" class="call-remote-video" autoplay playsinline></video>\n' +
                '    <video id="call-local-video" class="call-local-video" autoplay playsinline muted></video>\n' +
                '    <div class="call-controls-bar">\n' +
                '        <span id="call-duration" class="call-duration"></span>\n' +
                '        <div class="call-controls">\n' +
                '            <button type="button" class="call-btn" id="call-controls-mic" aria-label="Mikrofonu aç/kapat" title="Mikrofon">🎙</button>\n' +
                '            <button type="button" class="call-btn" id="call-controls-camera" aria-label="Kamerayı aç/kapat" title="Kamera">📷</button>\n' +
                '            <button type="button" class="call-btn call-btn-danger" id="call-controls-hangup" aria-label="Aramayı sonlandır" title="Kapat">📵</button>\n' +
                '        </div>\n' +
                '    </div>\n' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', overlayHtml);
        }
    }

    // UI elemanları
    function getElements() {
        return {
            callOverlay: document.getElementById('call-overlay'),
            callModalIncoming: document.getElementById('call-modal-incoming'),
            callRemoteVideo: document.getElementById('call-remote-video'),
            callLocalVideo: document.getElementById('call-local-video'),
            callRemoteAvatar: document.getElementById('call-remote-avatar'),
            callRemoteAvatarImg: document.getElementById('call-remote-avatar-img'),
            callRemoteAvatarFallback: document.getElementById('call-remote-avatar-fallback'),
            callRemoteName: document.getElementById('call-remote-name'),
            callDuration: document.getElementById('call-duration'),
            callControlsMic: document.getElementById('call-controls-mic'),
            callControlsCamera: document.getElementById('call-controls-camera'),
            callControlsHangup: document.getElementById('call-controls-hangup'),
            incomingCallName: document.getElementById('incoming-call-name'),
            incomingCallAvatarImg: document.getElementById('incoming-call-avatar-img'),
            incomingCallAvatarFallback: document.getElementById('incoming-call-avatar-fallback'),
            incomingCallAcceptBtn: document.getElementById('incoming-call-accept-btn'),
            incomingCallRejectBtn: document.getElementById('incoming-call-reject-btn'),
            callVoiceInfo: document.getElementById('call-voice-info'),
        };
    }

    // Gerçek avatar varsa <img> gösterir, yoksa emoji fallback'e döner —
    // hem tam ekran arama hem gelen arama modalı bu deseni paylaşır.
    function setAvatarVisual(imgEl, fallbackEl, url) {
        if (url) {
            if (imgEl) { imgEl.src = url; imgEl.removeAttribute('hidden'); }
            if (fallbackEl) fallbackEl.setAttribute('hidden', '');
        } else {
            if (imgEl) imgEl.setAttribute('hidden', '');
            if (fallbackEl) fallbackEl.removeAttribute('hidden');
        }
    }

    function getUserMediaConfig(isVideo) {
        return {
            audio: true,
            video: isVideo ? {
                width: { ideal: 1280 }, height: { ideal: 720 },
                frameRate: { ideal: 30 }, facingMode: 'user'
            } : false
        };
    }

    // Görüntülü arama piksel piksel/kalitesiz görünüyordu (kullanıcı raporu)
    // — tarayıcı varsayılan encoder ayarlarıyla bant genişliği çok düşük
    // seçebiliyor. Video gönderici için hedef bitrate + kare hızı belirtip
    // "kaliteyi koru" tercihini bildiriyoruz (tarayıcı desteklemezse sessizce
    // geç — try/catch, kritik değil). Ücretsiz TURN relay yine de bir tavan
    // koyabilir; bu SADECE kod tarafındaki iyileştirmedir.
    function tuneVideoSender(pc) {
        if (!pc || !state.isVideoCall) return;
        try {
            var sender = pc.getSenders().find(function (s) { return s.track && s.track.kind === 'video'; });
            if (!sender) return;
            var params = sender.getParameters();
            if (!params.encodings || !params.encodings.length) params.encodings = [{}];
            params.encodings[0].maxBitrate = 2500000; // ~2.5 Mbps
            params.encodings[0].maxFramerate = 30;
            params.degradationPreference = 'maintain-framerate';
            sender.setParameters(params).catch(function () { /* tarayıcı desteklemiyor olabilir */ });
        } catch (err) {
            log('Video bitrate ayarı uygulanamadı: ' + err.message);
        }
    }

    // TURN + STUN sunucuları ICE candidate bulma için (internet üzerinden çalışmasını sağlar)
    function getIceServers() {
        return [
            { urls: 'stun:stun.l.google.com:19302' },
            { urls: 'stun:stun.relay.metered.ca:80' },
            { urls: 'turn:standard.relay.metered.ca:80', username: 'openrelayproject', credential: 'openrelayproject' },
            { urls: 'turn:standard.relay.metered.ca:443', username: 'openrelayproject', credential: 'openrelayproject' },
            { urls: 'turns:standard.relay.metered.ca:443?transport=tcp', username: 'openrelayproject', credential: 'openrelayproject' }
        ];
    }

    // --- Arama Durumu Yönetimi ---

    async function startCall(isVideo) {
        if (state.callState !== 'idle') {
            showAlert('Zaten aktif bir arama var.');
            return;
        }

        state.isVideoCall = isVideo;
        state.callState = 'ringing';
        state.callStartedAt = Date.now();
        state.isCaller = true;
        state.callAnswered = false;

        // Karşı tarafın adı/avatarı — hem "aranıyor" kartında hem (kabul
        // edilince) tam ekran arama ekranında gösterilir (kullanıcı isteği:
        // sesli aramada bomboş siyah ekran yerine gerçek kişi görünsün)
        var callPanel = document.querySelector('[data-my-username]');
        state.otherUsername = callPanel ? (callPanel.dataset.otherUsername || '') : '';
        state.otherAvatar = callPanel ? (callPanel.dataset.otherAvatar || '') : '';

        try {
            // Medya izni al
            var config = getUserMediaConfig(isVideo);
            state.localStream = await navigator.mediaDevices.getUserMedia(config);
        } catch (err) {
            log('getUserMedia hatası: ' + err.message);
            showAlert('Kamera/mikrofon izni verilmedi veya cihaz bulunamadı.');
            state.callState = 'idle';
            return;
        }

        try {
            // RTCPeerConnection kur (TURN + STUN sunucularıyla)
            state.peerConnection = new RTCPeerConnection({
                iceServers: getIceServers()
            });

            // Local stream'i bağla — video track'e 'motion' ipucu (contentHint)
            // encoder'ın hareketli görüntü için daha iyi kalite/bant genişliği
            // dengesi seçmesine yardımcı olur (piksel piksel görünme şikayeti)
            state.localStream.getTracks().forEach(function (track) {
                if (track.kind === 'video') {
                    try { track.contentHint = 'motion'; } catch (err) { /* desteklenmiyorsa yok say */ }
                }
                state.peerConnection.addTrack(track, state.localStream);
            });

            // Remote stream'i dinle
            state.peerConnection.ontrack = function (event) {
                log('Remote track alındı: ' + event.track.kind);
                if (!state.remoteStream) {
                    state.remoteStream = new MediaStream();
                }
                state.remoteStream.addTrack(event.track);
                var elem = getElements();
                if (elem.callRemoteVideo) {
                    elem.callRemoteVideo.srcObject = state.remoteStream;
                }
            };

            // ICE adayları gönder
            state.peerConnection.onicecandidate = function (event) {
                if (event.candidate) {
                    sendSignal({
                        type: 'ice',
                        candidate: event.candidate.candidate,
                        sdpMLineIndex: event.candidate.sdpMLineIndex,
                        sdpMid: event.candidate.sdpMid
                    });
                }
            };

            // ICE bağlantı durumunu dinle — başarısız olursa aramayı sonlandır
            state.peerConnection.oniceconnectionstatechange = function () {
                log('ICE bağlantı durumu: ' + state.peerConnection.iceConnectionState);
                if (state.peerConnection && state.peerConnection.iceConnectionState === 'failed') {
                    log('ICE bağlantı başarısız');
                    showAlert('Bağlantı kurulamadı. TURN sunucusu yardımcı olamadı.');
                    endCall('failed');
                }
            };

            // Offer oluştur ve gönder
            var offer = await state.peerConnection.createOffer();
            await state.peerConnection.setLocalDescription(offer);
            tuneVideoSender(state.peerConnection);

            sendSignal({
                type: 'offer',
                sdp: offer.sdp,
                video: isVideo,
                callerName: callPanel ? callPanel.dataset.myUsername : '',
                callerAvatar: callPanel ? (callPanel.dataset.myAvatar || '') : '',
                conversation_id: state.conversationId,
                to: state.otherId
            });

            // Karşı taraf KABUL EDENE KADAR tam ekran arayüze girilmez —
            // küçük "aranıyor" kartı gösterilir (kullanıcı isteği). Tam ekran
            // + süre sayacı answer gelince başlar (handleAnswerSignal).
            showOutgoingRinging(state.otherUsername);

            state.callState = 'ringing';

            // 30 saniye sonra cevap yoksa aramayı sonlandır
            if (state.noAnswerTimeout) clearTimeout(state.noAnswerTimeout);
            state.noAnswerTimeout = setTimeout(function () {
                if (state.callState === 'ringing') {
                    log('Cevap yok, arama sonlandırılıyor');
                    showAlert('Cevap yok.');
                    endCall('no-answer');
                }
            }, 30000);

        } catch (err) {
            log('Arama başlatma hatası: ' + err.message);
            showAlert('Arama başlatılamadı.');
            endCall();
        }
    }

    async function acceptCall(offerSdp) {
        // Timeout'u temizle (karşı taraf cevap verdi)
        if (state.noAnswerTimeout) {
            clearTimeout(state.noAnswerTimeout);
            state.noAnswerTimeout = null;
        }

        state.callState = 'active';

        // Medya iznini AYRI ele al: görüntülü aramada kamera izni/aygıt hatası
        // en sık kabul-sonrası-red sebebiydi (kullanıcı raporu) — net mesaj ver,
        // görüntülüde kamera açılamazsa SES ile devam etmeyi dene
        try {
            var config = getUserMediaConfig(state.isVideoCall);
            state.localStream = await navigator.mediaDevices.getUserMedia(config);
        } catch (mediaErr) {
            log('getUserMedia hatası (kabul): ' + mediaErr.name + ' ' + mediaErr.message);
            if (state.isVideoCall) {
                try {
                    state.localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
                    state.isVideoCall = false; // kameras覺z devam — sesli aramaya düş
                    showAlert('Kamera açılamadı (' + mediaErr.name + ') — arama SESLİ olarak devam ediyor.');
                } catch (audioErr) {
                    showAlert('Mikrofon/kamera izni alınamadı: ' + audioErr.name + '. Tarayıcı site izinlerini kontrol et.');
                    rejectCall();
                    return;
                }
            } else {
                showAlert('Mikrofon izni alınamadı: ' + mediaErr.name + '. Tarayıcı site izinlerini kontrol et.');
                rejectCall();
                return;
            }
        }

        try {
            // RTCPeerConnection kur (TURN + STUN sunucularıyla)
            state.peerConnection = new RTCPeerConnection({
                iceServers: getIceServers()
            });

            // Local stream'i bağla — video track'e 'motion' ipucu (contentHint)
            // encoder'ın hareketli görüntü için daha iyi kalite/bant genişliği
            // dengesi seçmesine yardımcı olur (piksel piksel görünme şikayeti)
            state.localStream.getTracks().forEach(function (track) {
                if (track.kind === 'video') {
                    try { track.contentHint = 'motion'; } catch (err) { /* desteklenmiyorsa yok say */ }
                }
                state.peerConnection.addTrack(track, state.localStream);
            });

            // Remote stream'i dinle
            state.peerConnection.ontrack = function (event) {
                log('Remote track alındı: ' + event.track.kind);
                if (!state.remoteStream) {
                    state.remoteStream = new MediaStream();
                }
                state.remoteStream.addTrack(event.track);
                var elem = getElements();
                if (elem.callRemoteVideo) {
                    elem.callRemoteVideo.srcObject = state.remoteStream;
                }
            };

            // ICE adayları gönder
            state.peerConnection.onicecandidate = function (event) {
                if (event.candidate) {
                    sendSignal({
                        type: 'ice',
                        candidate: event.candidate.candidate,
                        sdpMLineIndex: event.candidate.sdpMLineIndex,
                        sdpMid: event.candidate.sdpMid
                    });
                }
            };

            // ICE bağlantı durumunu dinle — başarısız olursa aramayı sonlandır
            state.peerConnection.oniceconnectionstatechange = function () {
                log('ICE bağlantı durumu: ' + state.peerConnection.iceConnectionState);
                if (state.peerConnection && state.peerConnection.iceConnectionState === 'failed') {
                    log('ICE bağlantı başarısız');
                    showAlert('Bağlantı kurulamadı. TURN sunucusu yardımcı olamadı.');
                    endCall('failed');
                }
            };

            // Offer'ı set et
            var offer = new RTCSessionDescription({ type: 'offer', sdp: offerSdp });
            await state.peerConnection.setRemoteDescription(offer);

            // Answer oluştur ve gönder
            var answer = await state.peerConnection.createAnswer();
            await state.peerConnection.setLocalDescription(answer);
            tuneVideoSender(state.peerConnection);

            sendSignal({
                type: 'answer',
                sdp: answer.sdp
            });

            // Daha önce gelen ICE adaylarını ekle
            state.iceCandidateQueue.forEach(function (cand) {
                if (state.peerConnection) {
                    state.peerConnection.addIceCandidate(cand).catch(function (err) {
                        log('ICE candidate ekleme hatası: ' + err.message);
                    });
                }
            });
            state.iceCandidateQueue = [];

            // Incoming modal'ı gizle
            var elem = getElements();
            if (elem.callModalIncoming) {
                elem.callModalIncoming.setAttribute('hidden', '');
            }

            // Arama overlay'i göster
            showCallUI();
            startDurationTimer();

        } catch (err) {
            log('Accept hatası: ' + err.message);
            showAlert('Arama kabul edilemedi.');
            rejectCall();
        }
    }

    async function handleAnswerSignal(answerSdp) {
        if (!state.peerConnection) {
            log('Answer alındı ama peer connection yok');
            return;
        }

        // Timeout'u temizle (karşı taraf cevap verdi)
        if (state.noAnswerTimeout) {
            clearTimeout(state.noAnswerTimeout);
            state.noAnswerTimeout = null;
        }

        try {
            var answer = new RTCSessionDescription({ type: 'answer', sdp: answerSdp });
            await state.peerConnection.setRemoteDescription(answer);
            state.callState = 'active';

            // Karşı taraf kabul etti — "aranıyor" kartını kapat, TAM EKRAN
            // arayüze şimdi geç, süre sayacı da kabul anından başlasın
            hideOutgoingRinging();
            state.callAnswered = true;
            state.callStartedAt = Date.now();
            showCallUI();
            startDurationTimer();

            // Daha önce gelen ICE adaylarını ekle
            state.iceCandidateQueue.forEach(function (cand) {
                if (state.peerConnection) {
                    state.peerConnection.addIceCandidate(cand).catch(function (err) {
                        log('ICE candidate ekleme hatası: ' + err.message);
                    });
                }
            });
            state.iceCandidateQueue = [];

        } catch (err) {
            log('Answer işleme hatası: ' + err.message);
        }
    }

    function handleIceCandidate(candidate, sdpMLineIndex, sdpMid) {
        if (!state.peerConnection) {
            log('ICE candidate alındı ama peer connection yok');
            return;
        }

        var iceCandidate = new RTCIceCandidate({
            candidate: candidate,
            sdpMLineIndex: sdpMLineIndex,
            sdpMid: sdpMid
        });

        // Eğer remote description henüz set edilmediyse kuyrukta beklet
        if (!state.peerConnection.remoteDescription) {
            state.iceCandidateQueue.push(iceCandidate);
        } else {
            state.peerConnection.addIceCandidate(iceCandidate).catch(function (err) {
                log('ICE candidate ekleme hatası: ' + err.message);
            });
        }
    }

    // Arama sonucunu konuşmaya normal mesaj olarak yazar (yalnızca ARAYAN
    // gönderir — iki taraf da gönderse çift kayıt olurdu). Kendi tarafımızda
    // balon, chat.js'in _chatAppendMine kancasıyla düşer; karşı tarafa
    // realtime INSERT ile gider.
    function sendCallLog(text) {
        if (!state.conversationId || !text) return;
        var csrf = document.querySelector('input[name="csrf_token"]');
        var fd = new FormData();
        fd.append('content', text);
        fd.append('csrf_token', csrf ? csrf.value : '');
        fetch('/messages/' + state.conversationId + '/send', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: fd
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (saved) {
              if (saved && window._chatAppendMine) window._chatAppendMine(saved);
          })
          .catch(function () { /* kayıt düşmezse arama zaten bitti, kritik değil */ });
    }

    function endCall(reason) {
        if (state.callState === 'idle') return;

        // Arama sonucu mesajı (temizlikten ÖNCE — state değerleri lazım)
        if (state.isCaller) {
            var logText;
            if (state.callAnswered && state.callStartedAt) {
                logText = '📞 ' + (state.isVideoCall ? 'Görüntülü' : 'Sesli') + ' arama • '
                    + formatDuration(Date.now() - state.callStartedAt) + ' sürdü';
            } else if (reason === 'rejected') {
                logText = '📞 Arama reddedildi';
            } else if (reason === 'failed') {
                logText = '📞 Arama bağlantı sorunu nedeniyle kurulamadı';
            } else {
                logText = '📞 Cevapsız arama';
            }
            sendCallLog(logText);
        }

        // Hangup sinyali gönder
        if (state.callState !== 'idle') {
            sendSignal({ type: 'hangup' });
        }

        // Kaynakları temizle
        if (state.localStream) {
            state.localStream.getTracks().forEach(function (track) { track.stop(); });
            state.localStream = null;
        }

        if (state.peerConnection) {
            state.peerConnection.close();
            state.peerConnection = null;
        }

        hideOutgoingRinging();
        state.remoteStream = null;
        state.callState = 'idle';
        state.isCaller = false;
        state.callAnswered = false;
        state.callStartedAt = null;
        state.otherUsername = '';
        state.otherAvatar = '';

        // UI'ı gizle
        var elem = getElements();
        if (elem.callOverlay) elem.callOverlay.setAttribute('hidden', '');
        if (elem.callModalIncoming) elem.callModalIncoming.setAttribute('hidden', '');

        // Timer'ı temizle
        if (state.callDurationInterval) {
            clearInterval(state.callDurationInterval);
            state.callDurationInterval = null;
        }
        if (elem.callDuration) elem.callDuration.textContent = '';

        // No-answer timeout'u temizle
        if (state.noAnswerTimeout) {
            clearTimeout(state.noAnswerTimeout);
            state.noAnswerTimeout = null;
        }
    }

    function rejectCall() {
        sendSignal({ type: 'reject' });

        if (state.localStream) {
            state.localStream.getTracks().forEach(function (track) { track.stop(); });
            state.localStream = null;
        }

        var elem = getElements();
        if (elem.callModalIncoming) {
            elem.callModalIncoming.setAttribute('hidden', '');
        }

        state.callState = 'idle';
    }

    // --- Sinyalleşme ---

    // TÜM çağrı sinyalleri kullanıcı-bazlı kanallardan akar: gönderen
    // calls:<hedefId> kanalına yazar, herkes kendi calls:<meId> kanalını
    // dinler (global listener). Konuşma kanalı kullanılmaz — karşı taraf
    // sohbeti açık tutmak zorunda değil (inbox'tayken de telefon çalar,
    // answer/ice inbox'tan da gidebilir).
    //
    // DİKKAT: supabase-js'te abone OLUNMAMIŞ kanala send() çalışmaz — kanal
    // hedef başına bir kez kurulup SUBSCRIBED olana dek beklenir (cache'li).
    var _outboundChannels = {};
    function getOutboundChannel(targetId) {
        if (_outboundChannels[targetId]) return _outboundChannels[targetId];
        _outboundChannels[targetId] = new Promise(function (resolve, reject) {
            if (!window.supabaseClient) { reject(new Error('supabase yok')); return; }
            // GEÇİCİ GERİ ALMA (2026-07-10): private:true burada canlıda
            // CHANNEL_ERROR'a yol açtı (Realtime authorization RLS ile bu
            // kanal tipi arasında teşhis edilmemiş bir uyumsuzluk — bkz.
            // sql/migration_realtime_broadcast_rls.sql'deki policy'ler DB'de
            // duruyor ama private:true olmadan devreye girmiyor). Kararlılık
            // önce: kök neden netleşene kadar public kanala dönüldü.
            var ch = window.supabaseClient.channel('calls:' + targetId, {
                config: { broadcast: { self: false } }
            });
            ch.subscribe(function (status) {
                if (status === 'SUBSCRIBED') resolve(ch);
                else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                    delete _outboundChannels[targetId]; // sonraki denemede yeniden kur
                    reject(new Error('kanal kurulamadı: ' + status));
                }
            });
        });
        return _outboundChannels[targetId];
    }

    function sendSignal(payload) {
        var targetId = payload.to || state.otherId;
        if (!targetId) {
            log('Sinyal hedefi yok (otherId boş)');
            return;
        }
        var signal = Object.assign({
            from: state.meId,
            conversation_id: state.conversationId
        }, payload);
        getOutboundChannel(targetId).then(function (ch) {
            ch.send({ type: 'broadcast', event: 'call-signal', payload: signal });
        }).catch(function (err) {
            log('Sinyal gönderilemedi: ' + err.message);
        });
    }

    function handleSignal(signal) {
        // Kendi sinyallerimizi işleme
        if (signal.from === state.meId) return;

        log('Signal alındı: ' + signal.type);

        if (signal.type === 'offer') {
            if (state.callState !== 'idle') {
                // Meşgulüm — reddi ARAYANA gönder (aktif görüşmedeki kişiye değil)
                sendSignal({ type: 'reject', to: signal.from });
                return;
            }
            // Cevap verirken hedefimiz arayan; konuşma bağlamı payload'dan gelir
            state.conversationId = signal.conversation_id || state.conversationId;

            state.otherId = signal.from;
            state.isVideoCall = signal.video || false;
            state.callState = 'ringing';
            state.isCaller = false;
            state.otherUsername = signal.callerName || 'Birisi';
            state.otherAvatar = signal.callerAvatar || '';

            // Gelen arama modalı göster (gerçek ad + avatar ile)
            var elem = getElements();
            if (elem.incomingCallName && elem.callModalIncoming) {
                elem.incomingCallName.textContent = state.otherUsername;
                setAvatarVisual(elem.incomingCallAvatarImg, elem.incomingCallAvatarFallback, state.otherAvatar);
                elem.callModalIncoming.removeAttribute('hidden');
            }

            // Modalda "Kabul Et" onClick store offer
            window.pendingOffer = signal.sdp;

        } else if (signal.type === 'answer') {
            handleAnswerSignal(signal.sdp);

        } else if (signal.type === 'ice') {
            handleIceCandidate(signal.candidate, signal.sdpMLineIndex, signal.sdpMid);

        } else if (signal.type === 'hangup') {
            endCall();

        } else if (signal.type === 'reject') {
            // SADECE ben ararken (ringing) anlamlı — görüşme kurulduktan sonra
            // gelen bayat bir reject (başka sekmedeki modal, geç ulaşan sinyal)
            // aktif aramayı ÖLDÜRMESİN (kullanıcı raporu: kabul edilen arama
            // 'reddedildi' diye kapanıyordu)
            if (state.callState !== 'ringing') {
                log('reject yok sayıldı (durum: ' + state.callState + ')');
                return;
            }
            log('Arama reddedildi');
            showAlert('Arama reddedildi.');
            endCall('rejected');
        }
    }

    // --- UI Gösterileri ---

    // Giden arama kartı (kabul edilene kadarki bekleme durumu)
    function showOutgoingRinging(name) {
        ensureCallDOM();
        var p = document.getElementById('call-outgoing-panel');
        var n = document.getElementById('call-outgoing-name');
        if (n) n.textContent = name || 'Kullanıcı';
        if (p) p.removeAttribute('hidden');
    }

    function hideOutgoingRinging() {
        var p = document.getElementById('call-outgoing-panel');
        if (p) p.setAttribute('hidden', '');
    }

    function showCallUI() {
        var elem = getElements();
        if (elem.callOverlay) {
            elem.callOverlay.removeAttribute('hidden');
        }
        if (elem.callLocalVideo) {
            elem.callLocalVideo.srcObject = state.localStream;
        }

        // Sesli aramada avatar göster (gerçek görsel + ad — önceden jenerik
        // ikonla bomboş görünüyordu, kullanıcı isteğiyle düzeltildi)
        if (!state.isVideoCall && elem.callRemoteAvatar) {
            elem.callRemoteAvatar.removeAttribute('hidden');
            setAvatarVisual(elem.callRemoteAvatarImg, elem.callRemoteAvatarFallback, state.otherAvatar);
            if (elem.callRemoteName) elem.callRemoteName.textContent = state.otherUsername || '';
        }

        // Sesli aramada video gizle
        if (!state.isVideoCall && elem.callRemoteVideo) {
            elem.callRemoteVideo.setAttribute('hidden', '');
        }

        // Görüntülü aramada video göster, avatar gizle
        if (state.isVideoCall && elem.callRemoteVideo) {
            elem.callRemoteVideo.removeAttribute('hidden');
        }
        if (state.isVideoCall && elem.callRemoteAvatar) {
            elem.callRemoteAvatar.setAttribute('hidden', '');
        }

        // Sesli aramada kamera butonu anlamsız — gizle
        if (elem.callControlsCamera) {
            if (state.isVideoCall) elem.callControlsCamera.removeAttribute('hidden');
            else elem.callControlsCamera.setAttribute('hidden', '');
        }

        // Voice info metni güncelle
        if (elem.callVoiceInfo) {
            if (state.isVideoCall) {
                elem.callVoiceInfo.textContent = 'Görüntülü Arama';
            } else {
                elem.callVoiceInfo.textContent = 'Sesli Arama';
            }
        }
    }

    function startDurationTimer() {
        if (!state.callStartedAt) return;
        var elem = getElements();
        if (!elem.callDuration) return;

        if (state.callDurationInterval) clearInterval(state.callDurationInterval);

        state.callDurationInterval = setInterval(function () {
            var elapsed = Date.now() - state.callStartedAt;
            elem.callDuration.textContent = formatDuration(elapsed);
        }, 1000);
    }

    // --- Kontrol Butonları ---

    function toggleMic() {
        if (!state.localStream) return;
        var elem = getElements();
        var enabled = state.localStream.getAudioTracks()[0].enabled;
        state.localStream.getAudioTracks()[0].enabled = !enabled;
        if (elem.callControlsMic) {
            elem.callControlsMic.classList.toggle('muted');
        }
    }

    function toggleCamera() {
        if (!state.localStream || !state.isVideoCall) return;
        var elem = getElements();
        var enabled = state.localStream.getVideoTracks()[0].enabled;
        state.localStream.getVideoTracks()[0].enabled = !enabled;
        if (elem.callControlsCamera) {
            elem.callControlsCamera.classList.toggle('disabled');
        }
    }

    // --- Global Arama Dinleyicisi (inbox'ta da çalışsın) ---

    window.initGlobalCallListener = function (meId) {
        if (!meId || window._globalCallListenerInitialized) return;
        window._globalCallListenerInitialized = true;

        // Inbox'ta (aktif konuşma yokken) gelen aramayı cevaplayabilmek için
        // meId burada da set edilir — initCallSystem hiç çalışmamış olabilir
        state.meId = meId;

        ensureCallDOM();

        if (!window.supabaseClient) {
            log('Supabase client mevcut değil, global call listener yapılamıyor');
            return;
        }

        try {
            // GEÇİCİ GERİ ALMA (2026-07-10) — bkz. getOutboundChannel'daki not
            state.callsChannel = window.supabaseClient.channel('calls:' + meId, {
                config: { broadcast: { self: false } }
            });

            state.callsChannel.on('broadcast', { event: 'call-signal' }, function (msg) {
                // TÜM sinyal türleri artık kullanıcı kanalından akar
                // (offer/answer/ice/hangup/reject) — bkz. sendSignal
                handleSignal(msg.payload || {});
            }).subscribe(function (status) {
                if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
                    log('Global call channel bağlantı sorunu, durum: ' + status);
                }
            });

            log('Global call listener başlatıldı: calls:' + meId);
        } catch (err) {
            log('Global call listener başlatılamadı: ' + err.message);
        }
    };

    // --- Global Başlatma (chat.js konuşma geçişlerinde çağrılır) ---

    window.initCallSystem = function (conversationId, meId, signalingChannel, otherUserId) {
        state.conversationId = conversationId;
        state.meId = meId;
        state.signalingChannel = signalingChannel;
        state.otherId = otherUserId;

        ensureCallDOM();

        // Global call listener'ı ilk seferinde başlat (inbox'ta da gelen aramalar çalışsın)
        if (meId && !window._globalCallListenerInitialized) {
            window.initGlobalCallListener(meId);
        }

        var elem = getElements();

        // Başlatma butonları: 📞 sesli, 🎥 görüntülü
        var voiceBtn = document.getElementById('call-voice-btn');
        var videoBtn = document.getElementById('call-video-btn');

        if (voiceBtn) {
            voiceBtn.onclick = function () { startCall(false); };
        }
        if (videoBtn) {
            videoBtn.onclick = function () { startCall(true); };
        }

        // Gelen arama kabul/reddet
        if (elem.incomingCallAcceptBtn) {
            elem.incomingCallAcceptBtn.onclick = function () {
                if (window.pendingOffer) {
                    acceptCall(window.pendingOffer);
                }
            };
        }
        if (elem.incomingCallRejectBtn) {
            elem.incomingCallRejectBtn.onclick = function () { rejectCall(); };
        }

        // Kontrol butonları
        if (elem.callControlsMic) {
            elem.callControlsMic.onclick = function () { toggleMic(); };
        }
        if (elem.callControlsCamera) {
            elem.callControlsCamera.onclick = function () { toggleCamera(); };
        }
        if (elem.callControlsHangup) {
            elem.callControlsHangup.onclick = function () { endCall(); };
        }

        // NOT: konuşma kanalında 'call-signal' aboneliği BİLEREK yok — tüm
        // çağrı sinyalleri kullanıcı-bazlı calls:<id> kanallarından akar
        // (bkz. sendSignal). Konuşma kanalına da bağlanmak aynı sinyalin iki
        // kez işlenmesine (çift setRemoteDescription hatası) yol açardı.

        log('Başlatıldı: ' + conversationId);
    };

    // Sayfa kapatılırken hangup gönder (tek sefer, initCallSystem dışında —
    // her konuşma geçişinde tekrar tekrar bağlanmasın)
    window.addEventListener('beforeunload', function () {
        if (state.callState !== 'idle') {
            sendSignal({ type: 'hangup' });
        }
    });

    // Sayfa yüklenir yüklenmez global dinleyici: inbox'ta da telefon çalar
    if (window.ME_ID) {
        window.initGlobalCallListener(window.ME_ID);
    }

    // beforeunload geri dönüş değeri (browser uyarı gösterir)
    window.addEventListener('beforeunload', function (e) {
        if (state.callState !== 'idle') {
            e.preventDefault();
            return '';
        }
    });

})();
