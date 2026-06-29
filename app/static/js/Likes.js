// Beğen butonuna tıklanınca:
// 1) Arayüzü ANINDA güncelle (sayı +1/-1, kalp dolu/boş)
// 2) Arka planda sunucuya fetch ile istek gönder
// 3) Sunucudan gelen gerçek değerle senkronize et
// 4) İstek başarısız olursa eski haline geri al

document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".like-btn");
    if (!btn) return;

    e.preventDefault();
    if (btn.dataset.busy === "1") return; // hızlı çift tıklamayı engelle

    const countEl = btn.querySelector(".like-count");
    const wasLiked = btn.dataset.liked === "1";
    const prevCount = parseInt(countEl.textContent, 10) || 0;

    // --- 1) Optimistic update ---
    const nextLiked = !wasLiked;
    btn.dataset.liked = nextLiked ? "1" : "0";
    btn.classList.toggle("liked", nextLiked);
    countEl.textContent = prevCount + (nextLiked ? 1 : -1);
    btn.dataset.busy = "1";

    try {
        // --- 2) Sunucuya gönder ---
        const res = await fetch(btn.dataset.likeUrl, {
            method: "POST",
            headers: { "X-Requested-With": "fetch" },
        });
        if (!res.ok) throw new Error("İstek başarısız: " + res.status);
        const data = await res.json();

        // --- 3) Gerçek değerle senkronize et ---
        btn.dataset.liked = data.liked ? "1" : "0";
        btn.classList.toggle("liked", data.liked);
        countEl.textContent = data.count;
    } catch (err) {
        // --- 4) Hata varsa geri al ---
        btn.dataset.liked = wasLiked ? "1" : "0";
        btn.classList.toggle("liked", wasLiked);
        countEl.textContent = prevCount;
        console.error("Beğeni güncellenemedi:", err);
    } finally {
        btn.dataset.busy = "0";
    }
});