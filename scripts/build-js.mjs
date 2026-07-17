// JS bundler: mevcut vanilla script'leri (global scope, sıraya bağımlı,
// document-level delegation deseni) ES MODULE'e ÇEVİRMEDEN, sadece mevcut
// <script src> sırasıyla BİRLEŞTİRİP minify eder — davranış (paylaşılan
// global scope) BİREBİR korunur, tek fark: N HTTP isteği yerine 1.
//
// Neden gerçek esbuild bundling (--bundle) DEĞİL: bu dosyalar import/export
// kullanmıyor, birbirlerine window.* global'lar üzerinden referans veriyor
// (örn. icons.js -> window.ICONS, diğerleri onu okuyor). Modül sistemine
// çevirmek her dosyayı tek tek yeniden yazmayı gerektirirdi (büyük, riskli
// bir refactor) — bunun yerine esbuild SADECE minifier olarak kullanılıyor,
// concatenation manuel yapılıyor (sıra = mevcut <script> sırası).
//
// Kullanım: node scripts/build-js.mjs        (tek seferlik build)
//           node scripts/build-js.mjs --watch (dosya değişiminde otomatik)
import { transform } from 'esbuild';
import { readFileSync, writeFileSync, mkdirSync, watch } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const JS_DIR = path.join(__dirname, '..', 'app', 'static', 'js');
const OUT_DIR = path.join(__dirname, '..', 'app', 'static', 'dist');

// Bundle adı -> kaynak dosyalar (SIRA ÖNEMLİ, mevcut template'lerdeki
// <script src> sırasıyla BİREBİR aynı olmalı).
const MANIFEST = {
  // base.html — HER sayfada (giriş yapılmamış dahil)
  'common': [
    'icons.js', 'mentionAutocomplete.js', 'pageProgress.js', 'theme.js',
    'navbar.js', 'confirmModal.js', 'shareModal.js', 'repost.js',
    'postMore.js', 'lightbox.js', 'postClick.js',
  ],
  // base.html — SADECE giriş yapılmışsa ({% if me %})
  'common-auth': ['notifications.js', 'messagesBadge.js'],

  // feed/profile/post_detail/discover/search/hashtag/follow_list ortak seti
  // (likes/polls/bookmarks/follow document-level delegation kullanıyor —
  // ilgili DOM elemanı olmayan sayfada sessizce hiçbir şey yapmaz, bu yüzden
  // TÜM bu sayfalarda güvenle aynı bundle kullanılabilir)
  'post-interactions': ['likes.js', 'polls.js', 'bookmarks.js', 'follow.js'],

  'feed-extra': ['postModal.js', 'stories.js', 'storyHighlights.js', 'infiniteScroll.js'],
  'profile-extra': ['collections.js', 'profileTabs.js', 'storyHighlights.js', 'stickers.js'],
  'post-detail-extra': ['stickers.js', 'comments.js'],

  // messages/_convo_list.html ve messages/_realtime_init.html AYRI partial'lar
  // (inbox.html + conversation.html ikisi de include ediyor, sıra: convo-list
  // ÖNCE, realtime SONRA) — modülerlik korunsun diye 2 ayrı bundle.
  'messages-convo-list': ['groupChat.js', 'groupAdmin.js', 'groupCall.js', 'msgMedia.js'],
  'messages-realtime': ['stickers.js', 'voiceWaveform.js', 'chat.js', 'messagesPanel.js'],

  // Reels: dikey kısa video akışı
  'reels': ['reels.js'],
};

async function buildBundle(name, files) {
  const combined = files.map((f) => {
    const p = path.join(JS_DIR, f);
    return `/* --- ${f} --- */\n${readFileSync(p, 'utf-8')}`;
  }).join('\n;\n');

  const rawBytes = Buffer.byteLength(combined, 'utf-8');
  const result = await transform(combined, { minify: true, loader: 'js', target: 'es2018' });
  const outPath = path.join(OUT_DIR, `${name}.bundle.js`);
  writeFileSync(outPath, result.code, 'utf-8');
  const minBytes = Buffer.byteLength(result.code, 'utf-8');
  console.log(`  ${name}.bundle.js: ${files.length} dosya, ${rawBytes}B -> ${minBytes}B (${outPath})`);
}

async function buildAll() {
  mkdirSync(OUT_DIR, { recursive: true });
  console.log('JS bundle build başlıyor...');
  for (const [name, files] of Object.entries(MANIFEST)) {
    await buildBundle(name, files);
  }
  console.log('Bitti.');
}

const isWatch = process.argv.includes('--watch');

await buildAll();

if (isWatch) {
  console.log(`\n${JS_DIR} izleniyor (Ctrl+C ile çık)...`);
  let pending = false;
  watch(JS_DIR, { recursive: false }, async () => {
    if (pending) return;
    pending = true;
    setTimeout(async () => {
      pending = false;
      try {
        await buildAll();
      } catch (e) {
        console.error('Build hatası:', e.message);
      }
    }, 150); // debounce — hızlı ardışık kaydetmelerde tek build
  });
}
