# CLAUDE.md

Bu dosya, bu repoda çalışırken Claude Code için geçerli olan kalıcı kurallar ve
çalışma şeklini tanımlar. "Ne inşa edildi / şu an nerede kaldık" detayları için
buraya değil, `.context/architecture.md` (mimari + şema + geliştirme sırası)
ve `.context/active_context.md`'ye (güncel durum, sprint günlüğü, bugfix
geçmişi) bak — burada onlar TEKRAR EDİLMEZ, sadece referans verilir.

## Proje Özeti

Arkadaş grubu için geliştirilen küçük ölçekli bir sosyal medya web uygulaması:
Flask (backend) + Supabase/Postgres (DB + Auth + Storage + Realtime) + Jinja2
+ vanilla JS (frontend, framework yok). Canlı kullanımda — güvenlik ve
kararlılık, yeni özellik hızından önce gelir.

Detaylar: [`.context/architecture.md`](.context/architecture.md) → Amaç,
Teknoloji Yığını, Veritabanı Şeması bölümleri.

## Mimari Özet

- **Backend:** Flask blueprint'leri (`auth`, `routes`, `social`, `messaging`,
  `notifications`, `hashtags`, ...), çoğu `app/*.py` altında tek dosya; büyüyen
  ikisi (`routes`, `messaging`) `app/routes/` ve `app/messaging/` PAKETİNE
  bölündü (bkz. architecture.md "Backend Mimarisi" — aynı `bp` nesnesi
  paylaşıldığı için endpoint/URL değişmedi, sadece dosya organizasyonu).
- **DB erişimi:** Her zaman Supabase **service-role** client (`get_sb()`) —
  RLS backend tarafından bypass edilir, ama RLS politikaları yine de aktif
  tutulur (defense-in-depth). Bkz. Güvenlik Kuralları altında.
- **Frontend:** Sayfa başına ayrı Jinja template + ayrı `.js` dosyası
  (framework/bundler yok). Paylaşılan UI parçaları (`post_card` gibi) SADECE
  makro içeren, `{% extends %}` OLMAYAN ayrı partial dosyalarda yaşar (bkz.
  `app/templates/_post_card.html` ve architecture.md'deki ilgili not — bir
  sayfa template'inden makro import etmeye çalışmak Jinja'yı çökertir).
- **Migration disiplini (2026-07-05'te değişti):** Şema değişiklikleri hâlâ
  `sql/migration_*.sql` dosyaları olarak yazılır, idempotent olur — ama artık
  Supabase MCP **yazma yetkili** (read_only kaldırıldı) ve Claude migration'ları
  doğrudan MCP üzerinden çalıştırır. Bu, önceki "kullanıcı elle SQL Editor'de
  çalıştırır" kuralının kullanıcının AÇIKÇA, riski anlayarak verdiği bir
  kararla DEĞİŞTİRİLMESİdir (bkz. hafıza kaydı `mcp-servers` ve
  active_context.md — insan-inceleme adımı bilerek kaldırıldı, "önceden
  böyleydi" diye geri alınmamalı).

Tam şema, dizin ağacı ve dosya sorumlulukları için:
[`.context/architecture.md`](.context/architecture.md).

## Klasör Sorumlulukları

| Yol | Sorumluluk |
|---|---|
| `app/*.py` | Blueprint'ler + iş mantığı (bkz. yukarı) |
| `app/templates/` | Jinja şablonları; `_` ile başlayanlar (`_post_card.html`) paylaşılan partial/makro, tek başına render edilmez |
| `app/static/js/` | Sayfa/özellik başına ayrı dosya (`likes.js`, `bookmarks.js`, ...), bundler yok |
| `app/static/css/style.css` | Tek global stylesheet, CSS custom properties ile açık/koyu tema |
| `sql/` | Idempotent migration dosyaları — kullanıcı elle çalıştırır |
| `.context/` | Aktif bakımlı proje dokümantasyonu (mimari + günlük). `.gitignore`'da, asla commit edilmez, ama İÇERİĞİ serbestçe güncellenir — "dokunulmaz/salt-okunur" DEĞİLDİR |
| `.env` | Sırlar. Asla okunup paylaşılmaz, asla commit edilmez |

## Kodlama Standartları

- Kod içi yorumlar ve commit mesajları **Türkçe** (proje genelinde tutarlı).
- Yorum yazma eşiği yüksek: sadece WHY açıklanır (gizli bir kısıt, subtle bir
  invariant, şaşırtıcı bir davranış) — WHAT zaten okunabilir kod ve isimlerle
  anlaşılmalı.
- Yeni bir view eklerken `render_template()`'e `me=session.get("user")`
  eklemeyi unutma — `base.html`'deki navbar `{% if me %}` ile korunur,
  unutulursa sayfa hata VERMEZ, navbar sessizce kaybolur (bkz.
  architecture.md, bu hata Sprint 5'te iki kez yakalandı).
- Kritik olmayan/yeni eklenen özellikler (henüz migration'ı çalıştırılmamış
  olabilecek tablolar) `try/except` ile korunur — sayfa render'ı kırılmamalı,
  özellik sessizce "henüz aktif değil" davranışına düşmeli. Çekirdek/eski
  özellikler (like, auth) için bu tolerans YOK — bkz. Hata Ayıklama altında.
- CSS: bir elemente `hidden` özniteliğiyle gizleme uygulanacaksa VE o eleman
  `.btn`/`.voice-recording-status` gibi explicit `display` değeri olan bir
  class taşıyorsa, o class'ın `[hidden]` varyantını da EKLE (`.btn[hidden] {
  display: none; }`) — explicit `display` tarayıcının varsayılan `[hidden] {
  display:none}` kuralını ezer, eleman `hidden` olsa da GÖRÜNÜR kalır. Bu
  hata projede `.modal-overlay`/`.notif-panel`/`.reaction-picker`'da zaten
  vardı, Sprint 30'da `.btn` ve `.voice-recording-status`'ta tekrar bulundu
  (Playwright ile gerçek tarayıcıda computed style kontrolüyle yakalandı,
  kod okuyarak fark edilmedi) — yeni bir `hidden` kullanımı eklerken bunu
  kontrol et.

## Arama Stratejisi

- Belirli bir sembol/string biliniyorsa doğrudan Grep/Glob kullan.
- Açık uçlu, çok adımlı keşif gerekiyorsa (3+ sorgu beklentisi) Explore
  ajanını kullan, ana bağlamı şişirme.
- `.context/architecture.md`'deki "Dizin Yapısı" ve "Geliştirme Sırası"
  bölümleri genellikle "bu özellik nerede uygulanmış" sorusunu Grep'ten önce
  yanıtlar — önce oraya bak.

## Düzenleme Kuralları

- Mevcut dosyayı düzenlemeyi yeni dosya oluşturmaya tercih et.
- İstenenin ötesine geçen refactor/soyutlama ekleme — İSTİSNA: bir bug'ı
  çözmek için mimari bir sorunu çözmek gerekiyorsa (örn. paylaşılan makronun
  yanlış yerde tanımlı olması), bunu YAP ama neden gerektiğini net belirt
  (bkz. Sprint 12'deki `_post_card.html` çıkarımı — hashtag sayfası eklerken
  bulunan gerçek bir mimari kısıt, kozmetik bir tercih değil).
- CSRF: her POST formu `{{ csrf_token() }}` hidden input içermeli, her fetch
  POST'u `X-CSRF-Token` header'ı göndermeli.

## Refactoring Kuralları

- Sadece dokunduğun kodu refactor et; ilgisiz temizlik ayrı bir istekte kalsın.
- Paylaşılan bir UI parçasını birden fazla template'te kullanacaksan, onu
  SADECE makro/partial içeren ayrı bir dosyaya çıkar (`{% extends %}` veya
  sayfa-özel değişken referansı OLMASIN) — aksi halde Jinja'nın `{% from %}`
  import mekanizması tüm sayfa template'ini "modül" olarak çalıştırmaya
  çalışır ve tanımsız değişkenlerle çöker.

## Hata Ayıklama İş Akışı

1. Önce Flask `test_client()` ile izole, ucuz bir Python scriptiyle backend
   mantığını doğrula (DB'ye gerçekten bağlan, ama tarayıcı/sunucu gerekmez).
   Bu, "backend mi frontend mi" ayrımını dakikalar içinde netleştirir.
2. Sunucu testi gerekiyorsa: **her seferinde** önce eski `run.py` süreçlerini
   kapat, sonra taze başlat — `debug=True` + Werkzeug reloader nedeniyle 2
   `python.exe` süreci NORMAL olabilir ama STALE süreçler eski template'i
   sessizce servis etmeye devam eder (bkz.
   `feedback-flask-server-testing` hafıza kaydı, PowerShell komutu orada).
3. Tarayıcı davranışı (event modeli, drag/click sırası gibi) genellikle kod
   okuyarak mantık yürütmeyle çözülebilir — her şüphe için Playwright'a
   gerek yok, ama karmaşık/emin olunamayan durumlarda Playwright MCP (varsa)
   kullan.
4. Yeni, kritik olmayan bir tablo/kolon eklerken: migration çalıştırılmadan
   ÖNCE de sayfaların kırılmadığını (graceful degradation) test et — kullanıcı
   migration'ları hemen çalıştırmayabilir.

## Test Etme İş Akışı

- Bu projede otomatik test suite'i yok; doğrulama Flask `test_client()`
  script'leri + gerekirse gerçek sunucuya karşı `curl`/PowerShell istekleriyle
  yapılır.
- Statik kontrol ucuzdur, HER değişiklikten sonra çalıştır:
  `python -m py_compile app/*.py app/routes/*.py app/messaging/*.py`
  (paket haline gelen blueprint'ler için glob'u unutma, bkz. Sprint 28 —
  düz `app/*.py` bu alt klasörleri KAPSAMAZ) ve değişen her `.js` dosyası
  için `node --check <dosya>`.
- Kullanıcı bazen "testi sen yapma, ben test edip bildiririm" diyebilir —
  bu söylendiğinde SADECE o tur için geçerlidir, kalıcı bir kural değil;
  aksi belirtilmedikçe varsayılan davranış yukarıdaki gibi doğrulamaktır.
- UI/UX değişikliklerinde mümkünse gerçek sunucu + gerçek isteklerle
  (test_client veya Playwright) uçtan uca doğrula, sadece "syntax doğru"
  demekle yetinme.

## Git İş Akışı

- Her iş/özellik/bugfix tamamlandığında SORMADAN otomatik commit at
  (2026-07-05'te kullanıcı isteğiyle değişti — "commit atayım mı" diye sorma).
- Commit mesajları Türkçe, `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
  satırıyla biter (bu repodaki tüm önceki commit'lerle tutarlı).
- `.env` ve `.context/` asla stage/commit edilmez (`.gitignore` bunu zaten
  engeller — `git add` sonrası `git status` ile kontrol et).
- Her özellik/bugfix sonrası: kod commit'inden ÖNCE `.context/active_context.md`
  ve gerekirse `.context/architecture.md` güncellenir (bu dosyalar commit'e
  girmez ama proje hafızasının parçasıdır).
- **Commit SADECE ana oturum (orchestrator) atar, subagent'lar ASLA değil**
  (2026-07-06'da 2 kez ihlal edildi — bir subagent Bash erişimini kullanıp
  kendi kendine commit attı; içerik doğru olduğu için geri alınmadı ama bu
  bir istisna değil, kural ihlaliydi). Tüm subagent tanımlarının (`.claude/
  agents/*.md`) "Rol" bölümünün hemen altına bu kuralı vurgulayan bir not
  eklendi — yeni bir subagent türü eklenirse aynı notu ekle.

## Performans Rehberi

- Liste sayfalarında (feed/profil/arama) N+1 sorgudan kaçın — `_attach_post_metrics()`
  deseni: sayaçlar embedded count ile tek sorguda, kullanıcıya özel alanlar
  (`liked_by_me`, `bookmarked_by_me` vb.) tüm post ID'leri üzerinden TEK bir
  `IN` sorgusuyla çekilir.
- `count="exact", head=True` kullan satır çekmeden sayı almak için (profil
  istatistikleri gibi).

## Token Optimizasyonu Kuralları

- Uzun/tekrarlayan doğrulama çıktısını (curl/test scripti) doğrudan konuşmaya
  dökmek yerine özetle; sadece anahtar sonucu raporla.
- Bilinen bir sembol/dosya için Explore ajanı yerine doğrudan Grep/Read kullan.
- Zaten `.context/architecture.md` veya `.context/active_context.md`'de
  yanıtı olan bir soru için kod tabanını yeniden taramadan önce önce oraya
  bak.
- Context7 MCP (varsa) kütüphane API'lerini ezbere/halüsinasyonla değil,
  güncel dokümandan doğrulamak için kullanılabilir — özellikle `supabase-py`
  gibi hızlı değişen kütüphanelerde.

## MCP Sunucuları (bu projede)

Tam liste, kullanım amaçları ve gerekçeler için `.context/active_context.md`
→ "MCP Server Kullanımı" bölümü ve hafıza kaydı `mcp-servers`. Özetle 8
sunucu: notebooklm (araştırma), sequential-thinking, playwright (resmi, UI
testi), context7 (güncel kütüphane dokümantasyonu), supabase (**YAZMA
YETKİLİ**, 2026-07-05'te read_only kaldırıldı — Claude migration'ları
doğrudan çalıştırır, bkz. yukarı "Migration disiplini"), serena (sembol
bazlı kod navigasyonu), git ve filesystem (Bash/Read/Write/Glob/Grep'e
paralel MCP alternatifleri — kullanıcının açık isteğiyle eklendi). Memory/
Fetch/GitHub MCP kurulu değil (auto-memory/WebFetch ile çakışır, `gh` CLI
zaten kimlik doğrulanmamış durumda ve talep gelmedi).
