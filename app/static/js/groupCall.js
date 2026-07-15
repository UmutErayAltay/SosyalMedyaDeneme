// Grup sesli/görüntülü arama (LiveKit) — SADECE grup sohbetleri; 1:1 aramalar
// call.js'teki WebRTC sisteminde kalır, bu dosya ona hiç dokunmaz.
// Panel `_conversation_panel.html` içinde yaşıyor ve messagesPanel.js AJAX ile
// yeniden oluşturduğu için TÜM dinleyiciler document-level delegation kullanır
// (groupAdmin.js deseni). LiveKit istemcisi LAZY yüklenir (ilk katılımda CDN
// script'i enjekte edilir) — aramaya hiç girmeyen kullanıcı bedel ödemez.
//
// livekit-client v2 API notları (v1'den farklı, karıştırma):
//   room.remoteParticipants (v1: room.participants)
//   participant.videoTrackPublications / audioTrackPublications (v1: videoTracks)
//   publication.track (abone olunana dek undefined olabilir) -> track.attach(el)

(function () {
    var activeRoom = null;
    var activeConvId = null;    // aktif aramanın konuşması (panel değişimi tespiti)
    var currentCallMode = null; // 'audio' | 'video' | null

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function getPanel() { return document.getElementById('conversation-panel'); }

    function getConversationId() {
        var panel = getPanel();
        return panel ? panel.dataset.conversationId : null;
    }

    function isGroupConversation() {
        var panel = getPanel();
        return panel ? panel.dataset.isGroup === '1' : false;
    }

    function loadLiveKit() {
        return new Promise(function (resolve, reject) {
            if (window.LivekitClient) { resolve(); return; }
            var script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.min.js';
            script.onload = function () { resolve(); };
            script.onerror = function () { reject(new Error('LiveKit istemcisi yüklenemedi')); };
            document.head.appendChild(script);
        });
    }

    function getCallBar() { return document.getElementById('grp-call-bar'); }

    function showAlert(message) {
        var bar = getCallBar();
        if (!bar) return;
        bar.hidden = false;
        var alertDiv = document.createElement('div');
        alertDiv.className = 'grp-call-alert';
        alertDiv.textContent = message;
        bar.insertBefore(alertDiv, bar.firstChild);
        setTimeout(function () {
            alertDiv.remove();
            // Çubukta arama yoksa tekrar gizle
            if (!activeRoom && bar.children.length === 0) bar.hidden = true;
        }, 4000);
    }

    // --- Katılımcı kutucukları ---
    // Her render'da sıfırdan kurulur (olay başına ufak maliyet, durum takibi
    // basit kalır). Yerel katılımcı HER ZAMAN ilk kutucuk.
    function firstLiveTrack(publications) {
        var pubs = Array.from(publications.values());
        for (var i = 0; i < pubs.length; i++) {
            if (pubs[i].track && !pubs[i].isMuted) return pubs[i].track;
        }
        return null;
    }

    function buildTile(participant, isLocal) {
        var tile = document.createElement('div');
        tile.className = 'grp-call-tile';
        tile.dataset.identity = participant.identity;

        var nameDisplay = (participant.name || participant.identity || 'Bilinmeyen');
        if (isLocal) nameDisplay += ' (sen)';

        var videoTrack = firstLiveTrack(participant.videoTrackPublications);
        if (videoTrack) {
            var video = document.createElement('video');
            video.autoplay = true;
            video.muted = true; // video elementinden ses ÇALINMAZ (ses ayrı <audio>'da)
            video.playsInline = true;
            video.className = 'grp-call-video';
            videoTrack.attach(video);
            tile.appendChild(video);
            tile.classList.add('has-video');
        } else {
            var avatarDiv = document.createElement('div');
            avatarDiv.className = 'grp-call-avatar';
            avatarDiv.textContent = nameDisplay.charAt(0).toUpperCase();
            tile.appendChild(avatarDiv);
        }

        // Ses: SADECE uzak katılımcıların sesi attach edilir (yerelinki
        // eklenirse kullanıcı kendi yankısını duyar). Bu olmadan arama SESSİZ.
        if (!isLocal) {
            var audioTrack = firstLiveTrack(participant.audioTrackPublications);
            if (audioTrack) {
                var audio = audioTrack.attach();
                audio.hidden = true;
                tile.appendChild(audio);
            }
        }

        var nameOverlay = document.createElement('div');
        nameOverlay.className = 'grp-call-name';
        nameOverlay.textContent = nameDisplay;
        tile.appendChild(nameOverlay);
        return tile;
    }

    function renderParticipants(room) {
        var bar = getCallBar();
        if (!bar || !room) return;
        var participantsDiv = bar.querySelector('.grp-call-participants');
        if (!participantsDiv) {
            participantsDiv = document.createElement('div');
            participantsDiv.className = 'grp-call-participants';
            bar.appendChild(participantsDiv);
        }
        participantsDiv.innerHTML = '';
        participantsDiv.appendChild(buildTile(room.localParticipant, true));
        room.remoteParticipants.forEach(function (p) {
            participantsDiv.appendChild(buildTile(p, false));
        });
    }

    function renderControls() {
        var bar = getCallBar();
        if (!bar) return;
        var controlsDiv = bar.querySelector('.grp-call-controls');
        if (!controlsDiv) {
            controlsDiv = document.createElement('div');
            controlsDiv.className = 'grp-call-controls';
            bar.appendChild(controlsDiv);
        }
        controlsDiv.innerHTML = '';

        var micBtn = document.createElement('button');
        micBtn.type = 'button';
        micBtn.className = 'grp-call-control-btn grp-call-mic-btn';
        micBtn.title = 'Mikrofon';
        micBtn.innerHTML = window.ICONS ? window.ICONS.get('mic', { size: 18 }) : '🎤';
        micBtn.dataset.micOn = '1';
        micBtn.addEventListener('click', function () {
            if (!activeRoom) return;
            var isOn = micBtn.dataset.micOn === '1';
            activeRoom.localParticipant.setMicrophoneEnabled(!isOn).catch(function () {});
            micBtn.dataset.micOn = isOn ? '0' : '1';
            micBtn.classList.toggle('off', isOn);
        });
        controlsDiv.appendChild(micBtn);

        if (currentCallMode === 'video') {
            var camBtn = document.createElement('button');
            camBtn.type = 'button';
            camBtn.className = 'grp-call-control-btn grp-call-cam-btn';
            camBtn.title = 'Kamera';
            camBtn.innerHTML = window.ICONS ? window.ICONS.get('video', { size: 18 }) : '📹';
            camBtn.dataset.camOn = '1';
            camBtn.addEventListener('click', function () {
                if (!activeRoom) return;
                var isOn = camBtn.dataset.camOn === '1';
                activeRoom.localParticipant.setCameraEnabled(!isOn).catch(function () {});
                camBtn.dataset.camOn = isOn ? '0' : '1';
                camBtn.classList.toggle('off', isOn);
            });
            controlsDiv.appendChild(camBtn);
        }

        var leaveBtn = document.createElement('button');
        leaveBtn.type = 'button';
        leaveBtn.className = 'grp-call-control-btn grp-call-leave-btn';
        leaveBtn.title = 'Aramadan ayrıl';
        // call.js'teki hang-up ikonuyla aynı desen: telefon ikonu 135° döndürülmüş
        leaveBtn.innerHTML = window.ICONS ? window.ICONS.get('phone', { size: 18, cls: 'call-svg-flip' }) : '☎️';
        leaveBtn.addEventListener('click', function () { disconnectCall(); });
        controlsDiv.appendChild(leaveBtn);
    }

    function connectCall(token, url, callMode) {
        if (!window.LivekitClient) { showAlert('LiveKit yükleme başarısız.'); return; }
        var LK = window.LivekitClient;
        currentCallMode = callMode;
        activeConvId = getConversationId();

        var room = new LK.Room();
        activeRoom = room;

        function rerender() {
            // Kopma sırasında geç gelen track olayları temizlenmiş çubuğa
            // yeniden render yapmasın (canlı test bulgusu)
            if (activeRoom !== room) return;
            renderParticipants(room);
        }
        room.on(LK.RoomEvent.ParticipantConnected, rerender);
        room.on(LK.RoomEvent.ParticipantDisconnected, rerender);
        room.on(LK.RoomEvent.TrackSubscribed, rerender);
        room.on(LK.RoomEvent.TrackUnsubscribed, rerender);
        room.on(LK.RoomEvent.TrackMuted, rerender);
        room.on(LK.RoomEvent.TrackUnmuted, rerender);
        // Yerel kamera/mikrofon yayına girince kendi kutucuğun güncellensin
        room.on(LK.RoomEvent.LocalTrackPublished, rerender);
        room.on(LK.RoomEvent.LocalTrackUnpublished, rerender);
        // Konuşma göstergesi: aktif konuşanların kutucuğu parlar
        room.on(LK.RoomEvent.ActiveSpeakersChanged, function (speakers) {
            var bar = getCallBar();
            if (!bar) return;
            var speaking = {};
            speakers.forEach(function (s) { speaking[s.identity] = true; });
            bar.querySelectorAll('.grp-call-tile').forEach(function (tile) {
                tile.classList.toggle('speaking', !!speaking[tile.dataset.identity]);
            });
        });
        room.on(LK.RoomEvent.Disconnected, function () {
            // disconnect() bizden geldiyse activeRoom zaten null — çift temizlik zararsız
            if (activeRoom === room) disconnectCall();
        });

        room.connect(url, token).then(function () {
            var bar = getCallBar();
            if (bar) {
                bar.hidden = false;
                // İşaret: panel swap'ta şablondan gelen TAZE (işaretsiz) çubuk,
                // yaşayan bir bağlantının UI'sız kaldığını gösterir (aşağıdaki observer)
                bar.dataset.callActive = '1';
            }
            renderParticipants(room);
            renderControls();
            // Cihaz izinleri bağlantıdan SONRA istenir: izin reddedilse bile
            // arama dinleyici olarak sürer (bağlantıyı düşürme)
            room.localParticipant.setMicrophoneEnabled(true).catch(function () {
                showAlert('Mikrofon izni alınamadı — dinleyici olarak bağlısın.');
            });
            if (callMode === 'video') {
                room.localParticipant.setCameraEnabled(true).catch(function () {
                    showAlert('Kamera izni alınamadı.');
                });
            }
        }).catch(function (err) {
            showAlert('Arama bağlantısı kurulamadı: ' + err.message);
            activeRoom = null;
            currentCallMode = null;
            activeConvId = null;
        });
    }

    function disconnectCall() {
        var room = activeRoom;
        activeRoom = null;
        currentCallMode = null;
        activeConvId = null;
        if (room) {
            try { room.disconnect(); } catch (e) { /* zaten kopmuş olabilir */ }
        }
        var bar = getCallBar();
        if (bar) { bar.innerHTML = ''; bar.hidden = true; }
    }

    // Buton tıklamaları — document-level delegation
    document.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-group-call]') : null;
        if (!btn) return;
        if (!isGroupConversation()) return;
        if (activeRoom) return; // zaten aramadasın

        var callMode = btn.dataset.groupCall; // 'audio' | 'video'
        var conversationId = getConversationId();
        if (!callMode || !conversationId) return;

        loadLiveKit()
            .then(function () {
                return fetch('/messages/' + conversationId + '/call-token', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfToken() }
                });
            })
            .then(function (res) {
                if (res.status === 503) { showAlert('Grup aramaları şu anda kullanılamıyor.'); return null; }
                if (!res.ok) { showAlert('Arama kurulumu başarısız.'); return null; }
                return res.json();
            })
            .then(function (data) {
                if (data) connectCall(data.token, data.url, callMode);
            })
            .catch(function (err) { showAlert('Hata: ' + err.message); });
    });

    // Panel AJAX ile değişince (messagesPanel.js innerHTML'i yeniler) eski
    // #grp-call-bar DOM'dan gider ama LiveKit bağlantısı YAŞAMAYA devam ederdi
    // — kontrolsüz, ayrılamazsın. V1 kararı: sohbet değişince arama biter.
    var observer = new MutationObserver(function () {
        if (!activeRoom) return;
        var bar = getCallBar();
        // Farklı sohbete geçildi VEYA aynı sohbet yeniden yüklendi (taze,
        // işaretsiz çubuk) — iki durumda da UI gitti, bağlantıyı kapat
        if (getConversationId() !== activeConvId ||
            !bar || bar.dataset.callActive !== '1') {
            disconnectCall();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
})();
