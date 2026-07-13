// Takip butonu — AJAX (sayfa yenilenmesiz)
// Optimistic UI: arayüz ANINDA döner, ağ istekleri kullanıcı başına sıraya
// girer (ard arda tıklamada donma/bekletme olmaz — kullanıcı raporu),
// UI'ya yalnızca en son eylemin sonucu uygulanır.

const _followChains = {};
function _enqueueFollow(key, task) {
    _followChains[key] = (_followChains[key] || Promise.resolve()).then(task).catch(() => {});
}

function _followHeaders() {
    return {
        "X-Requested-With": "fetch",
        "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
    };
}

function _updateFollowerStat(data) {
    const statsEl = document.querySelectorAll(".profile-stats span");
    if (statsEl.length >= 2) {
        const strongEl = statsEl[1].querySelector("strong");
        if (strongEl && data.followers_count !== undefined) {
            strongEl.textContent = data.followers_count;
        }
    }
}

// Feed/sidebar takip butonu (.follow-btn, menü yok)
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".follow-btn");
    if (!btn) return;

    e.preventDefault();
    const wasFollowing = btn.dataset.following === "1";

    // Optimistic update — anında, tıklama düşürülmez
    const nextFollowing = !wasFollowing;
    btn.dataset.following = nextFollowing ? "1" : "0";
    btn.textContent = nextFollowing ? "Takipten çık" : "Takip et";
    btn.classList.toggle("btn-primary", !nextFollowing);
    btn.classList.toggle("btn-ghost", nextFollowing);

    btn._seq = (btn._seq || 0) + 1;
    const mySeq = btn._seq;
    _enqueueFollow(btn.dataset.username || btn.dataset.followUrl, async () => {
        try {
            const res = await fetch(btn.dataset.followUrl, { method: "POST", headers: _followHeaders() });
            if (!res.ok) throw new Error("İstek başarısız: " + res.status);
            const data = await res.json();
            if (btn._seq !== mySeq) return; // daha yeni tıklama var
            _updateFollowerStat(data);
        } catch (err) {
            console.error("Takip güncellenemedi:", err);
            if (btn._seq !== mySeq) return;
            btn.dataset.following = wasFollowing ? "1" : "0";
            btn.textContent = wasFollowing ? "Takipten çık" : "Takip et";
            btn.classList.toggle("btn-primary", !wasFollowing);
            btn.classList.toggle("btn-ghost", wasFollowing);
        }
    });
});

// Profil sayfası: takip butonu (takip/pending/unfollow durumu)
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".profile-follow-btn");
    if (!btn) return;

    e.preventDefault();
    const ds = Object.assign({}, btn.dataset);
    const isPending = btn.dataset.pending === "1";

    if (isPending) {
        // Pending → isteği geri çek (iptal et)
        // Optimistic: hemen normal button'a dön
        btn.textContent = window._isPrivate ? "Takip İsteği Gönder" : "Takip et";
        btn.classList.add("btn-primary");
        btn.classList.remove("btn-ghost");
        btn.dataset.pending = "0";

        _enqueueFollow(ds.username, async () => {
            try {
                const res = await fetch(ds.followUrl, { method: "POST", headers: _followHeaders() });
                if (!res.ok) throw new Error("İstek başarısız");
                const data = await res.json();
                _updateFollowerStat(data);
            } catch (err) {
                console.error("İstek geri alınamadı:", err);
                // Geri al: pending durumuna dön
                if (btn.isConnected) {
                    btn.textContent = "✓ İstek Gönderildi";
                    btn.classList.remove("btn-primary");
                    btn.classList.add("btn-ghost");
                    btn.dataset.pending = "1";
                }
            }
        });
    } else {
        // Gizli profil: pending istek yolla, public: takip et (menü)
        if (window._isPrivate) {
            // Gizli profil: baştan pending düğmesi göster (optimistic)
            const pendingBtn = createPendingButton(ds);
            btn.replaceWith(pendingBtn);

            _enqueueFollow(ds.username, async () => {
                try {
                    const res = await fetch(ds.followUrl, { method: "POST", headers: _followHeaders() });
                    if (!res.ok) throw new Error("İstek başarısız: " + res.status);
                    const data = await res.json();
                    _updateFollowerStat(data);

                    // Beklenmedik: is_pending false dönerse normal butona dön
                    if (!data.is_pending && pendingBtn.isConnected) {
                        pendingBtn.replaceWith(createFollowButton(ds));
                    }
                } catch (err) {
                    console.error("Takip isteği başarısız:", err);
                    if (pendingBtn.isConnected) pendingBtn.replaceWith(createFollowButton(ds));
                }
            });
        } else {
            // Public profil: ANINDA menüye dönüştür (mevcut davranış)
            const wrap = createFollowMenu(ds);
            btn.replaceWith(wrap);

            _enqueueFollow(ds.username, async () => {
                try {
                    const res = await fetch(ds.followUrl, { method: "POST", headers: _followHeaders() });
                    if (!res.ok) throw new Error("İstek başarısız: " + res.status);
                    const data = await res.json();
                    _updateFollowerStat(data);
                } catch (err) {
                    console.error("Takip başarısız:", err);
                    // Geri al: menü hâlâ yerindeyse butona çevir
                    if (wrap.isConnected) wrap.replaceWith(createFollowButton(ds));
                }
            });
        }
    }
});

// Profil sayfası: takip menüsü
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".profile-follow-menu-btn");
    if (!btn) return;

    e.preventDefault();
    const menu = btn.nextElementSibling;
    if (!menu || !menu.classList.contains("profile-follow-menu")) return;

    const isOpen = menu.hasAttribute("hidden") === false;
    if (isOpen) {
        menu.setAttribute("hidden", "");
        btn.setAttribute("aria-expanded", "false");
    } else {
        menu.removeAttribute("hidden");
        btn.setAttribute("aria-expanded", "true");
    }
});

// Menü kapatma: dışarı tıkla veya Escape
document.addEventListener("click", (e) => {
    if (e.target.closest(".profile-follow-menu-wrap")) return;
    document.querySelectorAll(".profile-follow-menu:not([hidden])").forEach((menu) => {
        menu.setAttribute("hidden", "");
        const btn = menu.previousElementSibling;
        if (btn) btn.setAttribute("aria-expanded", "false");
    });
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        document.querySelectorAll(".profile-follow-menu:not([hidden])").forEach((menu) => {
            menu.setAttribute("hidden", "");
            const btn = menu.previousElementSibling;
            if (btn) btn.setAttribute("aria-expanded", "false");
        });
    }
});

// Takip menüsü öğeleri: Takipten çık
document.addEventListener("click", async (e) => {
    const item = e.target.closest(".profile-follow-menu-item[data-action='unfollow']");
    if (!item) return;

    e.preventDefault();
    const menu = item.closest(".profile-follow-menu");
    const btn = menu?.previousElementSibling;
    if (!btn) return;

    const wasFollowing = btn.dataset.following === "1";
    if (!wasFollowing) return;

    // ANINDA butona dönüş (optimistic) — istek arka planda sıraya girer
    const ds = Object.assign({}, btn.dataset);
    const wrap = btn.closest(".profile-follow-menu-wrap");
    const newBtn = createFollowButton(ds);
    if (wrap) wrap.replaceWith(newBtn);

    _enqueueFollow(ds.username, async () => {
        try {
            const res = await fetch(ds.followUrl, { method: "POST", headers: _followHeaders() });
            if (!res.ok) throw new Error("İstek başarısız");
            const data = await res.json();
            _updateFollowerStat(data);
        } catch (err) {
            console.error("Takip kaldırılamadı:", err);
            // Geri al: buton hâlâ yerindeyse menüye çevir
            if (newBtn.isConnected) newBtn.replaceWith(createFollowMenu(ds));
        }
    });
});

// Takip menüsü öğeleri: Yakın arkadaş toggle
document.addEventListener("click", async (e) => {
    const item = e.target.closest(".profile-follow-menu-item[data-action='close-friend']");
    if (!item) return;

    e.preventDefault();
    const menu = item.closest(".profile-follow-menu");
    const btn = menu?.previousElementSibling;
    if (!btn || btn.dataset.busy === "1") return;

    const isCloseFriend = btn.dataset.isCloseFriend === "1";
    const url = isCloseFriend
        ? btn.dataset.removeCloseFriendUrl
        : btn.dataset.addCloseFriendUrl;

    if (!url) return;

    btn.dataset.busy = "1";
    try {
        const method = isCloseFriend ? "POST" : "POST";
        const headers = {
            "X-Requested-With": "fetch",
            "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
        };

        let res;
        if (!isCloseFriend) {
            // /close-friends/add expects JSON body
            headers["Content-Type"] = "application/json";
            res = await fetch(url, {
                method,
                headers,
                body: JSON.stringify({ user_id: btn.dataset.userId }),
            });
        } else {
            // /close-friends/<user_id>/remove
            res = await fetch(url, { method, headers });
        }

        if (!res.ok) throw new Error("İstek başarısız");

        // Butonu güncelle
        btn.dataset.isCloseFriend = isCloseFriend ? "0" : "1";
        const textEl = item.querySelector(".close-friend-text");
        if (textEl) {
            textEl.textContent = isCloseFriend
                ? "Yakın arkadaşlara ekle"
                : "Yakın arkadaşlardan çıkar";
        }
    } catch (err) {
        console.error("Yakın arkadaş güncellenemedi:", err);
    } finally {
        btn.dataset.busy = "0";
    }
});

// Profil sayfası: ⋯ menüsü (Engelle + Şikayet Et)
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".profile-actions-menu-btn");
    if (!btn) return;

    e.preventDefault();
    const menu = btn.nextElementSibling;
    if (!menu || !menu.classList.contains("profile-actions-menu")) return;

    // Diğer açık menüleri kapat
    document.querySelectorAll(".profile-actions-menu:not([hidden])").forEach((m) => {
        if (m !== menu) {
            m.setAttribute("hidden", "");
            const otherBtn = m.previousElementSibling;
            if (otherBtn) otherBtn.setAttribute("aria-expanded", "false");
        }
    });

    const isOpen = menu.hasAttribute("hidden") === false;
    if (isOpen) {
        menu.setAttribute("hidden", "");
        btn.setAttribute("aria-expanded", "false");
    } else {
        menu.removeAttribute("hidden");
        btn.setAttribute("aria-expanded", "true");
    }
});

// Menü kapatma: ⋯ menüsü için dışarı tıkla
document.addEventListener("click", (e) => {
    if (e.target.closest(".profile-actions-menu-wrap")) return;
    document.querySelectorAll(".profile-actions-menu:not([hidden])").forEach((menu) => {
        menu.setAttribute("hidden", "");
        const btn = menu.previousElementSibling;
        if (btn) btn.setAttribute("aria-expanded", "false");
    });
});

// Menü kapatma: ⋯ menüsü için Escape
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        document.querySelectorAll(".profile-actions-menu:not([hidden])").forEach((menu) => {
            menu.setAttribute("hidden", "");
            const btn = menu.previousElementSibling;
            if (btn) btn.setAttribute("aria-expanded", "false");
        });
    }
});

// Menü → "Takip et"/"Takip İsteği Gönder" butonuna dönüştürücü helper (unfollow + hata geri alma)
function createFollowButton(data) {
    const newBtn = document.createElement("button");
    newBtn.type = "button";
    newBtn.className = "btn btn-primary profile-follow-btn";
    newBtn.dataset.following = "0";
    newBtn.dataset.pending = "0";
    newBtn.dataset.username = data.username;
    newBtn.dataset.userId = data.userId;
    newBtn.dataset.followUrl = data.followUrl;
    newBtn.dataset.addCloseFriendUrl = data.addCloseFriendUrl;
    newBtn.dataset.removeCloseFriendUrl = data.removeCloseFriendUrl;
    const text = window._isPrivate ? "Takip İsteği Gönder" : "Takip et";
    newBtn.setAttribute("aria-label", text);
    newBtn.textContent = text;
    return newBtn;
}

// "Takip et" → menüye dönüştürücü helper
function createFollowMenu(data) {
    const wrap = document.createElement("div");
    wrap.className = "profile-follow-menu-wrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-ghost profile-follow-menu-btn";
    btn.dataset.username = data.username;
    btn.dataset.userId = data.userId;
    btn.dataset.following = "1";
    btn.dataset.followUrl = data.followUrl;
    btn.dataset.addCloseFriendUrl = data.addCloseFriendUrl;
    btn.dataset.removeCloseFriendUrl = data.removeCloseFriendUrl;
    btn.dataset.isCloseFriend = "0";
    btn.setAttribute("aria-label", "Takip seçenekleri");
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "✓ Takip ▾";

    const menu = document.createElement("div");
    menu.className = "profile-follow-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("hidden", "");

    const unfollowItem = document.createElement("button");
    unfollowItem.type = "button";
    unfollowItem.className = "profile-follow-menu-item";
    unfollowItem.dataset.action = "unfollow";
    unfollowItem.textContent = "❌ Takipten çık";

    const closeFriendItem = document.createElement("button");
    closeFriendItem.type = "button";
    closeFriendItem.className = "profile-follow-menu-item";
    closeFriendItem.dataset.action = "close-friend";
    closeFriendItem.innerHTML = '💚 <span class="close-friend-text">Yakın arkadaşlara ekle</span>';

    menu.appendChild(unfollowItem);
    menu.appendChild(closeFriendItem);

    wrap.appendChild(btn);
    wrap.appendChild(menu);

    return wrap;
}

// Pending button — "İstek Gönderildi" durumu
function createPendingButton(data) {
    const newBtn = document.createElement("button");
    newBtn.type = "button";
    newBtn.className = "btn btn-ghost profile-follow-btn";
    newBtn.dataset.following = "0";
    newBtn.dataset.pending = "1";
    newBtn.dataset.username = data.username;
    newBtn.dataset.userId = data.userId;
    newBtn.dataset.followUrl = data.followUrl;
    newBtn.dataset.addCloseFriendUrl = data.addCloseFriendUrl;
    newBtn.dataset.removeCloseFriendUrl = data.removeCloseFriendUrl;
    newBtn.setAttribute("aria-label", "İstek gönderildi");
    newBtn.textContent = "✓ İstek Gönderildi";
    return newBtn;
}
