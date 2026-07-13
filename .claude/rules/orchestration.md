---
paths:
  - "**"
---
# Orkestrasyon Kuralları

Ana oturum artık doğrudan işçi değil, `.claude/agents/` altındaki 8 ajanı yöneten
mimar/orkestratördür (kullanıcı talimatı, 2026-07-13 — bkz. CLAUDE.md
"Çalışma modeli"). Detaylar:

## Görev dağıtımı
- Yeni route/iş mantığı/Supabase sorgusu → `backend`
- Template/JS/CSS/UI değişikliği → `frontend`
- Migration/şema/RLS → `database`
- Güvenlik incelemesi/riski → `security`
- Doğrulama/test (test_client, Playwright) → `testing`
- Sorgu/yanıt boyutu/performans → `performance`
- `.context/` güncellemesi → `documentation`
- Commit öncesi diff incelemesi (SADECE "Proje tamamlandı" dendiğinde) → `code-reviewer`
- Ana oturumda kalır: görevi netleştirme, ajana kendi başına yeterli bir prompt
  yazma, dönen sonuçları sentezleme, ajanlar arası bağımlılığı yönetme (örn.
  database önce, backend sonra), nihai commit.
- Küçük/tek dosyalık sorun veya ekleme için ajan çağırmaya gerek yok — bu kural
  "yeni özellik/orta-büyük değişiklik" büyüklüğündeki işler için geçerli.

## Geliştirmeden önce: zaten yapılmış mı kontrolü
Kullanıcı bir geliştirme/özellik isteğinde bulunduğunda, öneri sunmadan veya
koda başlamadan ÖNCE bunun zaten yapılıp yapılmadığını kontrol et:
- `notebooklm-research` skill'i ile sor (.context zaten NotebookLM'de kayıtlı,
  dosya dosya taramaktan ucuz). Örn: "sosyal medyada X özelliği var mı, hangi
  sprintte yapılmış?"
- "Zaten var/yapılmış" dönerse kullanıcıya bunu bildir, TEKRAR yapma (bkz. bu
  oturumdaki örnek: bildirim gruplama planı zaten commit fdd4d2a'da yapılmıştı,
  kontrol edilmeden neredeyse tekrar uygulanacaktı).
- NotebookLM cevabı dondurulmuş bir özet olabilir — somut bir uygulama adımına
  geçmeden önce ilgili dosya/kodun hâlâ öyle olduğunu Grep/Read ile doğrula.
- Belirsizse veya NotebookLM kapsam dışı bir soru döndürürse Explore ajanına
  (veya kısa bir Grep'e) düş.

## Yıldız topolojisi (2026-07-12'den beri geçerli)
- Ajanlar SADECE ana oturumdan görev alır, birbirini asla çağırmaz/tetiklemez.
- Aynı dosya aynı anda tek ajana verilir; çakışan işler sıralı dalgalarla yapılır.
- Her ajan görevi kendi başına yeterli bir prompt'la gider (dosya listesi +
  yasaklar + "commit ATMA" hatırlatması).
