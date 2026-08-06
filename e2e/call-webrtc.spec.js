import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Sahte kamera/mikrofon — gerçek cihaz gerekmeden getUserMedia() başarılı
// döner (siyah kare + sessiz ton), izin prompt'u da otomatik "izin ver"
// olur — bkz. call.js startCall()'ın ilk adımı navigator.mediaDevices.
// getUserMedia, bu olmadan headless Chromium'da hemen NotAllowedError verir.
// channel:'chromium' ZORUNLU — Playwright'ın VARSAYILAN headless çalıştırdığı
// "chromium_headless_shell" binary'si getUserMedia'yı HİÇ desteklemiyor
// (NotSupportedError, izole test'le doğrulandı) — sessizce sonsuza kadar
// asılı kalan bir Promise değil, DOĞRUDAN senkron hata veriyordu; tam
// Chromium binary'sinde ('channel: chromium') sorunsuz çalışıyor.
test.use({
  channel: 'chromium',
  launchOptions: {
    args: [
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-permissions',
    ],
  },
});

test.describe('1:1 WebRTC Arama (Cross-User)', () => {
  let testData;

  // AYNI desen realtime-broadcast.spec.js'deki gibi — bu suite SADECE
  // "npm run test:e2e:call" ile (önce pytest setup) çalıştırılmalı, veri
  // yoksa SKIP (spurious kırmızı vermesin diye), bkz. tests/test_call_webrtc.py.
  test.beforeAll(() => {
    const dataFile = path.join(__dirname, 'test-data', 'call-users.json');
    if (!fs.existsSync(dataFile)) {
      test.skip(true,
        `Test data file not found: ${dataFile}. ` +
        'Bu suite "npm run test:e2e:call" ile çalıştırılmalı (önce pytest setup adımını koşar).');
      return;
    }
    testData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    console.log(`Loaded call test users: ${testData.user1.username}, ${testData.user2.username}`);
  });

  test('voice call should connect end-to-end between two real users', async ({ browser }) => {
    // Varsayılan Playwright test timeout'u (30sn) login+navigation+2x2.5sn
    // bekleme+15sn modal+15sn overlay+20sn ICE toplamına YETMİYORDU — ilk
    // denemede click GERÇEKLEŞTİ ama sonraki adımların hiçbiri loglanmadan
    // testin KENDİSİ zaman aşımına uğradı (yanlış negatif, gerçek arama
    // davranışı hakkında hiçbir şey KANITLAMAMIŞTI).
    test.setTimeout(90000);
    const context1 = await browser.newContext({ permissions: ['camera', 'microphone'] });
    const context2 = await browser.newContext({ permissions: ['camera', 'microphone'] });
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    // call.js her satırı '[WebRTC Arama] ' önekiyle console.log'a yazıyor —
    // TÜM akışı (signaling + ICE state) buradan teşhis edebiliriz, testin
    // PASS/FAIL'inden bağımsız olarak sonda tam log dökümü basılır.
    const logs1 = [];
    const logs2 = [];
    page1.on('console', msg => logs1.push(`[U1 ${msg.type()}] ${msg.text()}`));
    page2.on('console', msg => logs2.push(`[U2 ${msg.type()}] ${msg.text()}`));
    page1.on('pageerror', err => logs1.push(`[U1 pageerror] ${err.message}`));
    page2.on('pageerror', err => logs2.push(`[U2 pageerror] ${err.message}`));

    try {
      async function realLogin(page, email, password) {
        await page.goto('http://127.0.0.1:5000/login');
        await page.fill('input[name="email"]', email);
        await page.fill('input[name="password"]', password);
        await page.click('button[type="submit"]');
        await page.waitForLoadState('networkidle');
      }

      console.log('Setting up User1 auth (real UI login)...');
      await realLogin(page1, testData.user1.email, testData.user1.password);
      console.log('Setting up User2 auth (real UI login)...');
      await realLogin(page2, testData.user2.email, testData.user2.password);

      const conversationUrl = `http://127.0.0.1:5000/messages/${testData.conversation_id}`;
      await page1.goto(conversationUrl);
      await page2.goto(conversationUrl);
      await page1.waitForLoadState('networkidle');
      await page2.waitForLoadState('networkidle');
      expect(page1.url()).toContain(testData.conversation_id);
      expect(page2.url()).toContain(testData.conversation_id);

      // Global call listener'ın (calls:<meId>) SUBSCRIBED olmasını bekle —
      // yoksa User1 arama başlatır başlatmaz henüz dinlemeyen User2'ye gider.
      await page1.waitForTimeout(2500);
      await page2.waitForTimeout(2500);

      console.log('User1: sesli arama başlatıyor (#call-voice-btn)...');
      const callVoiceBtn = page1.locator('#call-voice-btn');
      await expect(callVoiceBtn).toBeVisible({ timeout: 5000 });
      await callVoiceBtn.click();

      // User2'de gelen arama modalı görünmeli (call-modal-incoming hidden kalkmalı).
      const incomingModal = page2.locator('#call-modal-incoming');
      await expect(incomingModal).toBeVisible({ timeout: 15000 });
      console.log('User2: gelen arama modalı göründü, kabul ediliyor...');

      const acceptBtn = page2.locator('#incoming-call-accept-btn');
      await acceptBtn.click();

      // Her iki tarafta da call-overlay (aktif arama ekranı) hidden kalkmalı
      // — bu, signaling'in (offer->answer) uçtan uca ULAŞTIĞININ kanıtı.
      const overlay1 = page1.locator('#call-overlay');
      const overlay2 = page2.locator('#call-overlay');
      await expect(overlay1).toBeVisible({ timeout: 15000 });
      await expect(overlay2).toBeVisible({ timeout: 15000 });
      console.log('✓ Her iki tarafta da call-overlay aktif (signaling uçtan uca ulaştı)');

      // ICE bağlantısının GERÇEKTEN kurulup kurulmadığını (medya akışı) süre
      // sayacının artmasından anlıyoruz — call.js sadece answer alınca değil,
      // ICE 'connected'/'completed' olunca süreyi başlatır (bkz. state.callAnswered).
      const duration1 = page1.locator('#call-duration');
      await expect(duration1).not.toHaveText('', { timeout: 20000 });
      const durationText = await duration1.textContent();
      console.log(`✓ Arama süresi sayacı çalışıyor: "${durationText}" (medya bağlantısı kuruldu)`);

      // Temiz kapat.
      const endBtn1 = page1.locator('#call-controls-hangup');
      if (await endBtn1.isVisible().catch(() => false)) {
        await endBtn1.click();
      }
    } finally {
      console.log('\n===== USER1 CONSOLE LOG =====');
      console.log(logs1.join('\n'));
      console.log('\n===== USER2 CONSOLE LOG =====');
      console.log(logs2.join('\n'));

      await page1.close();
      await page2.close();
      await context1.close();
      await context2.close();
    }
  });
});
