---
name: notebooklm-research
description: Bu projede "böyle bir özellik var mı", "bunu nerede/nasıl yapmıştık", "mimari neden böyle" gibi PROJE BAĞLAMI/GEÇMİŞİ sorularında dosyaları tek tek grep'lemeden/taramadan ÖNCE kullan. NotebookLM notebook'u .context/ (architecture.md, active_context.md) ile zaten dolu ve bedava — geniş kapsamlı kod taramasından çok daha az token harcar. Kesin bir sembol/fonksiyon konumu aranıyorsa (Serena/Grep işi) bu skill YERİNE geçmez, sadece "ne yapılmış/neden yapılmış" tarzı geniş sorularda öncelik.
---

# NotebookLM ile proje geçmişi araştırması

Kullanıcının hafıza notu (`feedback-notebooklm-research.md`): proje bağlamı/özellik-var-mı sorularını dosya taramak yerine NotebookLM'e sor.

## Ne zaman kullan

- "X özelliği var mı / eklendi mi?" — kod tabanını uçtan uca taramak yerine önce sor.
- "Bu mimari kararın nedeni neydi?" / "Sprint N'de ne yapılmıştı?"
- Genel/geniş kapsamlı "neredeyiz" soruları — `.context/active_context.md` çok büyükse (dosyayı tam okumak yerine).

## Ne zaman KULLANMA (Explore/Grep/Serena kullan)

- Kesin bir fonksiyon/sembol/satır arıyorsan — NotebookLM konum vermez, sadece özet/bağlam verir.
- Kod hâlâ öyle mi diye DOĞRULAMA gerekiyorsa — NotebookLM'in cevabı dondurulmuş bir özet olabilir, "before recommending" ilkesi gereği önce gerçek dosyayı/kodu kontrol et.

## Akış

1. `mcp__notebooklm__get_health` — `authenticated=false` ise `setup_auth` çalıştır (tarayıcı açılır, kullanıcı bir kere giriş yapar).
2. Notebook zaten kayıtlıysa `select_notebook` gerekmeyebilir; emin değilsen `list_notebooks`.
3. `ask_question` ile sor, dönen `session_id`'yi takip sorularında tekrar kullan (bağlam korunur, session-RAG daha keskin cevap verir).
4. Cevabı ALDIKTAN sonra, kullanıcıya somut bir öneri/aksiyon sunacaksan (sadece bilgi değil), iddia edilen dosya/fonksiyonun hâlâ var olduğunu Grep/Read ile doğrula — NotebookLM cevabı geçmişte dondurulmuş olabilir.
