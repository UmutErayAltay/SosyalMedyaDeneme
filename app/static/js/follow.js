// Takip butonu — AJAX (sayfa yenilenmesiz)
// Optimistic UI: beğenideki gibi anlık güncelle + geri al

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
