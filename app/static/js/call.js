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
        callState: 'idle', // idle, ringing, active, ended
        callStartedAt: null,
        iceCandidateQueue: [], // answer set edilmeden önce gelen ICE adayları
        callDurationInterval: null,
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

    // UI elemanları
    function getElements() {
        return {
            callOverlay: document.getElementById('call-overlay'),
            callModalIncoming: document.getElementById('call-modal-incoming'),
            callRemoteVideo: document.getElementById('call-remote-video'),
            callLocalVideo: document.getElementById('call-local-video'),
            callRemoteAvatar: document.getElementById('call-remote-avatar'),
            callDuration: document.getElementById('call-duration'),
            callControlsMic: document.getElementById('call-controls-mic'),
            callControlsCamera: document.getElementById('call-controls-camera'),
            callControlsHangup: document.getElementById('call-controls-hangup'),
            incomingCallName: document.getElementById('incoming-call-name'),
            incomingCallAcceptBtn: document.getElementById('incoming-call-accept-btn'),
            incomingCallRejectBtn: document.getElementById('incoming-call-reject-btn'),
            callVoiceInfo: document.getElementById('call-voice-info'),
        };
    }

    function getUserMediaConfig(isVideo) {
        return {
            audio: true,
            video: isVideo ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false
        };
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
            // RTCPeerConnection kur
            state.peerConnection = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });

            // Local stream'i bağla
            state.localStream.getTracks().forEach(function (track) {
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

            // Offer oluştur ve gönder
            var offer = await state.peerConnection.createOffer();
            await state.peerConnection.setLocalDescription(offer);

            var panel = document.querySelector('[data-my-username]');
            sendSignal({
                type: 'offer',
                sdp: offer.sdp,
                video: isVideo,
                callerName: panel ? panel.dataset.myUsername : ''
            });

            // Overlay'i göster
            showCallUI();

            state.callState = 'ringing';
            startDurationTimer();

        } catch (err) {
            log('Arama başlatma hatası: ' + err.message);
            showAlert('Arama başlatılamadı.');
            endCall();
        }
    }

    async function acceptCall(offerSdp) {
        state.callState = 'active';

        try {
            // Medya izni al
            var config = getUserMediaConfig(state.isVideoCall);
            state.localStream = await navigator.mediaDevices.getUserMedia(config);

            // RTCPeerConnection kur
            state.peerConnection = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });

            // Local stream'i bağla
            state.localStream.getTracks().forEach(function (track) {
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

            // Offer'ı set et
            var offer = new RTCSessionDescription({ type: 'offer', sdp: offerSdp });
            await state.peerConnection.setRemoteDescription(offer);

            // Answer oluştur ve gönder
            var answer = await state.peerConnection.createAnswer();
            await state.peerConnection.setLocalDescription(answer);

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

        try {
            var answer = new RTCSessionDescription({ type: 'answer', sdp: answerSdp });
            await state.peerConnection.setRemoteDescription(answer);
            state.callState = 'active';

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

    function endCall() {
        if (state.callState === 'idle') return;

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

        state.remoteStream = null;
        state.callState = 'idle';
        state.callStartedAt = null;

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

    function sendSignal(payload) {
        if (!state.signalingChannel) {
            log('Sinyalleşme kanalı hazır değil');
            return;
        }

        var signal = Object.assign({ from: state.meId }, payload);
        state.signalingChannel.send({
            type: 'broadcast',
            event: 'call-signal',
            payload: signal
        });
    }

    function handleSignal(signal) {
        // Kendi sinyallerimizi işleme
        if (signal.from === state.meId) return;

        log('Signal alındı: ' + signal.type);

        if (signal.type === 'offer') {
            if (state.callState !== 'idle') {
                sendSignal({ type: 'reject' });
                return;
            }

            state.otherId = signal.from;
            state.isVideoCall = signal.video || false;
            state.callState = 'ringing';

            // Gelen arama modalı göster
            var elem = getElements();
            if (elem.incomingCallName && elem.callModalIncoming) {
                elem.incomingCallName.textContent = signal.callerName || 'Birisi';
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
            log('Arama reddedildi');
            if (state.callState === 'ringing') {
                showAlert('Arama reddedildi.');
            }
            endCall();
        }
    }

    // --- UI Gösterileri ---

    function showCallUI() {
        var elem = getElements();
        if (elem.callOverlay) {
            elem.callOverlay.removeAttribute('hidden');
        }
        if (elem.callLocalVideo) {
            elem.callLocalVideo.srcObject = state.localStream;
        }

        // Sesli aramada avatar göster
        if (!state.isVideoCall && elem.callRemoteAvatar) {
            elem.callRemoteAvatar.removeAttribute('hidden');
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

    // --- Global Başlatma (chat.js konuşma geçişlerinde çağrılır) ---

    window.initCallSystem = function (conversationId, meId, signalingChannel, otherUserId) {
        state.conversationId = conversationId;
        state.meId = meId;
        state.signalingChannel = signalingChannel;
        state.otherId = otherUserId;

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

        // Sinyalleşme: broadcast event'lerini dinle
        if (signalingChannel) {
            signalingChannel.on('broadcast', { event: 'call-signal' }, function (msg) {
                var payload = msg.payload || {};
                handleSignal(payload);
            });
        }

        // Sayfa kapatılırken hangup gönder
        window.addEventListener('beforeunload', function () {
            if (state.callState !== 'idle') {
                sendSignal({ type: 'hangup' });
            }
        });

        log('Başlatıldı: ' + conversationId);
    };

    // beforeunload geri dönüş değeri (browser uyarı gösterir)
    window.addEventListener('beforeunload', function (e) {
        if (state.callState !== 'idle') {
            e.preventDefault();
            return '';
        }
    });

})();
