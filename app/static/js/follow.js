// Takip butonu — AJAX (sayfa yenilenmesiz)
// Optimistic UI: beğenideki gibi anlık güncelle + geri al

// Feed/sidebar takip butonu (.follow-btn, menü yok)
document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".follow-btn");
    if (!btn) return;

    e.preventDefault();
    if (btn.dataset.busy === "1") return;

    const wasFollowing = btn.dataset.following === "1";

    // Optimistic update
    const nextFollowing = !wasFollowing;
    btn.dataset.following = nextFollowing ? "1" : "0";
    btn.textContent = nextFollowing ? "Takipten çık" : "Takip et";
    btn.classList.toggle("btn-primary", !nextFollowing);
    btn.classList.toggle("btn-ghost", nextFollowing);
    btn.dataset.busy = "1";

    try {
        const res = await fetch(btn.dataset.followUrl, {
            method: "POST",
            headers: {
                "X-Requested-With": "fetch",
                "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
            },
        });
        if (!res.ok) throw new Error("İstek başarısız: " + res.status);

        // JSON yanıtı (followers_count ile)
        const data = await res.json();
        // Takipçi sayısını güncelle (profil stats alanında)
        const statsEl = document.querySelectorAll(".profile-stats span");
        if (statsEl.length >= 2) {
            // 2. span = Takipçi
            const strongEl = statsEl[1].querySelector("strong");
            if (strongEl && data.followers_count !== undefined) {
                strongEl.textContent = data.followers_count;
            }
        }
    } catch (err) {
        // Hata: geri al
        btn.dataset.following = wasFollowing ? "1" : "0";
        btn.textContent = wasFollowing ? "Takipten çık" : "Takip et";
        btn.classList.toggle("btn-primary", !wasFollowing);
        btn.classList.toggle("btn-ghost", wasFollowing);
        console.error("Takip güncellenemedi:", err);
    } finally {
        btn.dataset.busy = "0";
    }
});

// Profil sayfası: takip butonu (takip değil, menü yok)
document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".profile-follow-btn");
    if (!btn) return;

    e.preventDefault();
    if (btn.dataset.busy === "1") return;

    btn.dataset.busy = "1";

    try {
        const res = await fetch(btn.dataset.followUrl, {
            method: "POST",
            headers: {
                "X-Requested-With": "fetch",
                "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
            },
        });
        if (!res.ok) throw new Error("İstek başarısız: " + res.status);

        const data = await res.json();

        // Takip başarılı: butonu menüye dönüştür (sayfa yenilenmesiz)
        btn.replaceWith(createFollowMenu(btn.dataset));

        // Takipçi sayısını güncelle
        const statsEl = document.querySelectorAll(".profile-stats span");
        if (statsEl.length >= 2) {
            const strongEl = statsEl[1].querySelector("strong");
            if (strongEl && data.followers_count !== undefined) {
                strongEl.textContent = data.followers_count;
            }
        }
    } catch (err) {
        console.error("Takip başarısız:", err);
    } finally {
        btn.dataset.busy = "0";
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

    btn.dataset.busy = "1";
    try {
        const res = await fetch(btn.dataset.followUrl, {
            method: "POST",
            headers: {
                "X-Requested-With": "fetch",
                "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
            },
        });
        if (!res.ok) throw new Error("İstek başarısız");

        const data = await res.json();

        // Takip kaldırıldı: menüyü tekrar takip butonuna dönüştür
        const wrap = btn.closest(".profile-follow-menu-wrap");
        if (wrap) {
            const newBtn = document.createElement("button");
            newBtn.type = "button";
            newBtn.className = "btn btn-primary profile-follow-btn";
            newBtn.dataset.following = "0";
            newBtn.dataset.username = btn.dataset.username;
            newBtn.dataset.followUrl = btn.dataset.followUrl;
            newBtn.setAttribute("aria-label", "Takip et");
            newBtn.textContent = "Takip et";
            wrap.replaceWith(newBtn);
        }

        // Takipçi sayısını güncelle
        const statsEl = document.querySelectorAll(".profile-stats span");
        if (statsEl.length >= 2) {
            const strongEl = statsEl[1].querySelector("strong");
            if (strongEl && data.followers_count !== undefined) {
                strongEl.textContent = data.followers_count;
            }
        }
    } catch (err) {
        console.error("Takip kaldırılamadı:", err);
    } finally {
        btn.dataset.busy = "0";
    }
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
