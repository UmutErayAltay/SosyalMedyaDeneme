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
        // Tarayıcının kendi alert kutusu yerine sitenin özel modalı
        // (confirmModal.js — kullanıcı isteği); yüklenmemişse alert'e düş
        if (window.appAlert) window.appAlert(msg);
        else alert(msg);
    }

    // Kontrol butonlarındaki inline SVG ikonlar — konuşma başlığındaki
    // .conv-call-btn ikonlarıyla aynı görsel dil (currentColor, feather stili)
    var SVG_PHONE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
    var SVG_PHONE_DOWN = SVG_PHONE.replace('<svg ', '<svg class="call-svg-flip" ');
    var SVG_MIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
    var SVG_VIDEO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>';

    // Format mm:ss
    function formatDuration(ms) {
        var totalSec = Math.floor(ms / 1000);
        var m = Math.floor(totalSec / 60);
        var s = totalSec % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    // Gelen arama modal ve arama overlay DOM'unu oluştur (panel yoksa).
    // Buton bağlamaları da BURADA yapılır (initCallSystem'de değil) — arama
    // artık her sayfada çalıyor (akış/profil), o sayfalarda initCallSystem
    // hiç çalışmaz; kabul/reddet yine de çalışmalı.
    function ensureCallDOM() {
        if (!document.getElementById('call-modal-incoming')) {
            var modalHtml = '<div class="modal-overlay" id="call-modal-incoming" role="dialog" aria-modal="true" aria-labelledby="incoming-call-name" hidden>\n' +
                '    <div class="modal call-incoming-modal">\n' +
                '        <div class="modal-body call-modal-body">\n' +
                '            <div class="call-avatar-circle call-avatar-circle--md">\n' +
                '                <img id="incoming-call-avatar-img" alt="" hidden>\n' +
                '                <span id="incoming-call-avatar-fallback" aria-hidden="true">👤</span>\n' +
                '            </div>\n' +
                '            <p id="incoming-call-name" class="call-incoming-name"></p>\n' +
                '            <p id="incoming-call-type" class="call-incoming-type"></p>\n' +
                '            <div class="call-incoming-actions">\n' +
                '                <div class="call-incoming-action">\n' +
                '                    <button type="button" class="call-round-btn call-round-accept" id="incoming-call-accept-btn" aria-label="Aramayı kabul et">' + SVG_PHONE + '</button>\n' +
                '                    <span>Kabul et</span>\n' +
                '                </div>\n' +
                '                <div class="call-incoming-action">\n' +
                '                    <button type="button" class="call-round-btn call-round-reject" id="incoming-call-reject-btn" aria-label="Aramayı reddet">' + SVG_PHONE_DOWN + '</button>\n' +
                '                    <span>Reddet</span>\n' +
                '                </div>\n' +
                '            </div>\n' +
                '        </div>\n' +
                '    </div>\n' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            document.getElementById('incoming-call-accept-btn').onclick = function () {
                if (window.pendingOffer) acceptCall(window.pendingOffer);
            };
            document.getElementById('incoming-call-reject-btn').onclick = function () { rejectCall(); };
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
                '    <div class="call-topbar">\n' +
                '        <strong id="call-top-name" hidden></strong>\n' +
                '        <span id="call-duration" class="call-duration" role="timer"></span>\n' +
                '    </div>\n' +
                '    <div class="call-remote-avatar" id="call-remote-avatar" hidden>\n' +
                '        <div class="call-avatar-circle call-avatar-circle--lg" id="call-remote-avatar-circle">\n' +
                '            <img id="call-remote-avatar-img" alt="" hidden>\n' +
                '            <span id="call-remote-avatar-fallback" aria-hidden="true">👤</span>\n' +
                '        </div>\n' +
                '        <p id="call-remote-name" class="call-remote-name"></p>\n' +
                '        <p id="call-voice-info" class="call-type-label">Sesli Arama</p>\n' +
                '    </div>\n' +
                '    <video id="call-remote-video" class="call-remote-video" autoplay playsinline hidden></video>\n' +
                '    <video id="call-local-video" class="call-local-video" autoplay playsinline muted hidden></video>\n' +
                '    <div class="call-controls-bar">\n' +
                '        <div class="call-controls">\n' +
                '            <button type="button" class="call-btn" id="call-controls-mic" aria-label="Mikrofonu aç/kapat" title="Mikrofon">' + SVG_MIC + '</button>\n' +
                '            <button type="button" class="call-btn" id="call-controls-camera" aria-label="Kamera" title="Kamera">' + SVG_VIDEO + '</button>\n' +
                '            <button type="button" class="call-btn call-btn-danger" id="call-controls-hangup" aria-label="Aramayı sonlandır" title="Kapat">' + SVG_PHONE_DOWN + '</button>\n' +
                '        </div>\n' +
                '    </div>\n' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', overlayHtml);
            document.getElementById('call-controls-mic').onclick = function () { toggleMic(); };
            document.getElementById('call-controls-camera').onclick = function () { onCameraButton(); };
            document.getElementById('call-controls-hangup').onclick = function () { endCall(); };
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
            callTopName: document.getElementById('call-top-name'),
            callDuration: document.getElementById('call-duration'),
            callControlsMic: document.getElementById('call-controls-mic'),
            callControlsCamera: document.getElementById('call-controls-camera'),
            callControlsHangup: document.getElementById('call-controls-hangup'),
            incomingCallName: document.getElementById('incoming-call-name'),
            incomingCallType: document.getElementById('incoming-call-type'),
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
        // Arayan mesajlar sayfasındadır (form input'u var); yine de her
        // sayfada çalışan meta fallback'i kullan (base.html csrf-token meta'sı)
        var csrf = document.querySelector('input[name="csrf_token"]');
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var fd = new FormData();
        fd.append('content', text);
        fd.append('csrf_token', csrf ? csrf.value : (csrfMeta ? csrfMeta.content : ''));
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

            // Gelen arama modalı göster (gerçek ad + avatar + arama türü ile)
            ensureCallDOM();
            var elem = getElements();
            if (elem.incomingCallName && elem.callModalIncoming) {
                elem.incomingCallName.textContent = state.otherUsername;
                if (elem.incomingCallType) {
                    elem.incomingCallType.textContent = state.isVideoCall ? 'Görüntülü arama...' : 'Sesli arama...';
                }
                setAvatarVisual(elem.incomingCallAvatarImg, elem.incomingCallAvatarFallback, state.otherAvatar);
                elem.callModalIncoming.removeAttribute('hidden');
            }

            // Modalda "Kabul Et" onClick store offer
            window.pendingOffer = signal.sdp;

        } else if (signal.type === 'answer') {
            handleAnswerSignal(signal.sdp);

        } else if (signal.type === 'upgrade-offer') {
            // Karşı taraf görüşme SIRASINDA kamera açtı (sesliden görüntülüye
            // geçiş) — yeniden müzakere: yeni offer'ı kabul et, answer dön,
            // arayüzü görüntülü moda geçir (bkz. enableLocalCamera)
            if (!state.peerConnection || state.callState !== 'active') return;
            (async function () {
                try {
                    await state.peerConnection.setRemoteDescription(
                        new RTCSessionDescription({ type: 'offer', sdp: signal.sdp }));
                    var upAnswer = await state.peerConnection.createAnswer();
                    await state.peerConnection.setLocalDescription(upAnswer);
                    sendSignal({ type: 'upgrade-answer', sdp: upAnswer.sdp });
                    if (signal.video) {
                        state.isVideoCall = true;
                        showCallUI();
                    }
                } catch (err) {
                    log('Görüntülüye geçiş işlenemedi: ' + err.message);
                }
            })();

        } else if (signal.type === 'upgrade-answer') {
            if (!state.peerConnection) return;
            state.peerConnection.setRemoteDescription(
                new RTCSessionDescription({ type: 'answer', sdp: signal.sdp })
            ).catch(function (err) {
                log('Görüntülüye geçiş cevabı işlenemedi: ' + err.message);
            });

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
        ensureCallDOM();
        var elem = getElements();
        if (elem.callOverlay) {
            elem.callOverlay.removeAttribute('hidden');
        }

        // Yerel video yalnızca GERÇEKTEN bir kamera track'i varsa gösterilir —
        // sesli aramada / kamerasız tarafta köşede siyah kutu durmasın
        var hasLocalVideo = !!(state.localStream && state.localStream.getVideoTracks().length > 0);
        if (elem.callLocalVideo) {
            elem.callLocalVideo.srcObject = state.localStream;
            if (hasLocalVideo) elem.callLocalVideo.removeAttribute('hidden');
            else elem.callLocalVideo.setAttribute('hidden', '');
        }

        if (!state.isVideoCall) {
            // Sesli: ortada gerçek avatar + ad + tür etiketi, videolar gizli
            if (elem.callRemoteAvatar) {
                elem.callRemoteAvatar.removeAttribute('hidden');
                setAvatarVisual(elem.callRemoteAvatarImg, elem.callRemoteAvatarFallback, state.otherAvatar);
                if (elem.callRemoteName) elem.callRemoteName.textContent = state.otherUsername || '';
            }
            if (elem.callRemoteVideo) elem.callRemoteVideo.setAttribute('hidden', '');
            if (elem.callTopName) elem.callTopName.setAttribute('hidden', '');
        } else {
            // Görüntülü: tam ekran karşı video; ad üst bardaki çipte
            if (elem.callRemoteAvatar) elem.callRemoteAvatar.setAttribute('hidden', '');
            if (elem.callRemoteVideo) elem.callRemoteVideo.removeAttribute('hidden');
            if (elem.callTopName) {
                elem.callTopName.textContent = state.otherUsername || '';
                elem.callTopName.removeAttribute('hidden');
            }
        }

        // Kamera butonu HER İKİ modda görünür (kullanıcı isteği): seslide
        // "görüntülü aramaya geç", görüntülüde klasik aç/kapat. Kırmızı
        // (disabled) hâli = kameram şu an kapalı.
        if (elem.callControlsCamera) {
            var camOn = hasLocalVideo && state.localStream.getVideoTracks()[0].enabled;
            elem.callControlsCamera.classList.toggle('disabled', !camOn);
            var camTitle = hasLocalVideo ? 'Kamerayı aç/kapat' : 'Görüntülü aramaya geç';
            elem.callControlsCamera.title = camTitle;
            elem.callControlsCamera.setAttribute('aria-label', camTitle);
        }

        if (elem.callVoiceInfo) {
            elem.callVoiceInfo.textContent = state.isVideoCall ? 'Görüntülü Arama' : 'Sesli Arama';
        }
    }

    function startDurationTimer() {
        if (!state.callStartedAt) return;
        var elem = getElements();
        if (!elem.callDuration) return;

        if (state.callDurationInterval) clearInterval(state.callDurationInterval);

        elem.callDuration.textContent = '0:00'; // ilk saniye boş görünmesin
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

    // Kamera butonu iki işlevli (kullanıcı isteği): yerel kamera track'i
    // yoksa (sesli arama veya görüntülüde kamerasız taraf) kamerayı AÇIP
    // görüntülüye geçirir; varsa klasik aç/kapat.
    function onCameraButton() {
        var hasLocalVideo = !!(state.localStream && state.localStream.getVideoTracks().length > 0);
        if (hasLocalVideo) toggleCamera();
        else enableLocalCamera();
    }

    // Görüşme SIRASINDA kamera açma = sesliden görüntülüye geçiş. WebRTC'de
    // aktif bağlantıya track eklemek yeniden müzakere (renegotiation)
    // gerektirir: yeni offer üretilip 'upgrade-offer' sinyaliyle gönderilir,
    // karşı taraf 'upgrade-answer' döner. İlk offer/answer'dan (arama kurma)
    // AYRI sinyal türleri kullanılır — handleSignal'daki 'offer' dalı aktif
    // aramada gelen offer'ı "meşgul" sayıp reddediyor, çakışmasın.
    async function enableLocalCamera() {
        if (!state.peerConnection || state.callState !== 'active' || !state.localStream) return;
        var vs;
        try {
            vs = await navigator.mediaDevices.getUserMedia({ video: getUserMediaConfig(true).video });
        } catch (err) {
            showAlert('Kamera açılamadı (' + err.name + '). Tarayıcı site izinlerini kontrol et.');
            return;
        }
        var track = vs.getVideoTracks()[0];
        if (!track) return;
        try { track.contentHint = 'motion'; } catch (e) { /* desteklenmiyorsa yok say */ }
        state.localStream.addTrack(track);
        state.peerConnection.addTrack(track, state.localStream);
        state.isVideoCall = true;
        showCallUI();
        try {
            var offer = await state.peerConnection.createOffer();
            await state.peerConnection.setLocalDescription(offer);
            tuneVideoSender(state.peerConnection);
            sendSignal({ type: 'upgrade-offer', sdp: offer.sdp, video: true });
        } catch (err) {
            log('Görüntülüye geçiş teklifi gönderilemedi: ' + err.message);
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

        // Başlatma butonları: sesli / görüntülü (yalnızca konuşma sayfasında var).
        // Modal + kontrol butonu bağlamaları ensureCallDOM'da yapılır — arama
        // her sayfada çalıştığı için oraya taşındı (bkz. ensureCallDOM yorumu).
        var voiceBtn = document.getElementById('call-voice-btn');
        var videoBtn = document.getElementById('call-video-btn');

        if (voiceBtn) {
            voiceBtn.onclick = function () { startCall(false); };
        }
        if (videoBtn) {
            videoBtn.onclick = function () { startCall(true); };
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

    // Sayfa yüklenir yüklenmez global dinleyici: artık HER sayfada telefon
    // çalar (akış/profil dahil — _supabase_core.html base.html'de yüklüyor).
    // Mesajlaşma dışı sayfalarda gömülü token bayat olabileceğinden, core'un
    // taze token fetch'i (SB_TOKEN_READY) beklenip ÖYLE abone olunur — bayat
    // token'la subscribe CHANNEL_ERROR üretebiliyordu.
    if (window.ME_ID) {
        (window.SB_TOKEN_READY || Promise.resolve()).then(function () {
            window.initGlobalCallListener(window.ME_ID);
        });
    }

    // beforeunload geri dönüş değeri (browser uyarı gösterir)
    window.addEventListener('beforeunload', function (e) {
        if (state.callState !== 'idle') {
            e.preventDefault();
            return '';
        }
    });

})();
