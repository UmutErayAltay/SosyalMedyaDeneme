import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test.describe('Realtime Broadcast Synchronization (Cross-User)', () => {
  let testData;

  // Bu suite `npm run test:e2e:realtime` composite script'iyle çalışır
  // (önce pytest::test_realtime_setup ile gerçek 2 kullanıcı+conversation
  // oluşturulur, testler koşar, sonra pytest::test_realtime_cleanup temizler
  // — bkz. tests/test_realtime_broadcast.py modül docstring'i, iki ayrı
  // pytest süreci arasında Playwright çalıştığı için normal fixture
  // yield-teardown deseni KULLANILAMIYOR). Varsayılan `npm run test:e2e`
  // (tüm *.spec.js) bu setup adımını atmadan çalıştığında dosya yoksa
  // testler FAIL değil SKIP olmalı — yoksa her düz `test:e2e` koşusu
  // spurious kırmızı verir.
  test.beforeAll(() => {
    const dataFile = path.join(__dirname, 'test-data', 'realtime-users.json');
    if (!fs.existsSync(dataFile)) {
      test.skip(true,
        `Test data file not found: ${dataFile}. ` +
        'Bu suite "npm run test:e2e:realtime" ile çalıştırılmalı (önce pytest setup adımını koşar).');
      return;
    }
    testData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    console.log(`Loaded test data for users: ${testData.user1.username}, ${testData.user2.username}`);
  });

  test('typing indicator should broadcast from User A to User B', async ({ browser }) => {
    console.log(`Testing typing broadcast between ${testData.user1.username} and ${testData.user2.username}`);
    console.log(`Conversation: ${testData.conversation_id}`);

    // Create two browser contexts (one per user)
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();

    // Create pages
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      // Bu Flask uygulaması SUNUCU TARAFI session cookie'siyle çalışıyor
      // (login_required -> session['user']) — Supabase client-side auth
      // KASITLI OLARAK persistSession:false (bkz. _supabase_core.html),
      // yani localStorage'a token enjekte etmenin Flask auth'una hiçbir
      // etkisi yok (önceki deneme buydu, sayfalar sessizce /login'e
      // düşüyordu). Doğru yol: global-setup.js'deki AYNI gerçek UI login
      // akışı — email/şifre formu doldur, submit et, gerçek session cookie
      // al. window.SB_ACCESS_TOKEN zaten sunucu tarafında (Jinja,
      // session.access_token) otomatik set ediliyor, elle enjeksiyon gerekmez.
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

      // Navigate both users to the shared conversation
      const conversationUrl = `http://127.0.0.1:5000/messages/${testData.conversation_id}`;
      console.log(`Navigating to conversation: ${conversationUrl}`);

      await page1.goto(conversationUrl);
      await page2.goto(conversationUrl);

      await page1.waitForLoadState('networkidle');
      await page2.waitForLoadState('networkidle');

      // Gerçekten conversation sayfasına indiğimizi (login'e geri düşmediğimizi)
      // doğrula — yoksa aşağıdaki typing testi sessizce anlamsız geçer/kalır.
      expect(page1.url()).toContain(testData.conversation_id);
      expect(page2.url()).toContain(testData.conversation_id);

      console.log('Both pages loaded, waiting for Realtime channels...');
      await page1.waitForTimeout(2000);
      await page2.waitForTimeout(2000);

      // Verify both pages are on the conversation
      const page1Url = page1.url();
      const page2Url = page2.url();
      console.log(`Page1 URL: ${page1Url}`);
      console.log(`Page2 URL: ${page2Url}`);

      // Get typing indicator elements from both pages
      const typingIndicator2 = page2.locator('#typing-indicator');

      // Gerçek selector: app/templates/messages/_conversation_panel.html'de
      // <textarea name="content" id="msg-input" placeholder="Mesaj yaz...">
      // (önceki deneme burada .filter({has:...}) ile kendi placeholder'ını
      // ARAYIP hiçbir zaman eşleşmeyen kırık bir locator kullanıyordu).
      const messageInput1 = page1.locator('#msg-input');
      await expect(messageInput1).toBeVisible({ timeout: 5000 });

      // User1: yazmaya başla — chat.js input event'inde sendTyping(true) tetikler
      // (typingChannel broadcast, en fazla 2sn'de bir gönderilir, ilk karakter
      // hemen gönderir — bkz. chat.js sendTyping()).
      console.log('User1 starting to type...');
      await messageInput1.focus();
      await messageInput1.type('T', { delay: 100 });

      // User2'de göstergenin GERÇEKTEN görünmesini bekle (sabit timeout yerine
      // Playwright'ın auto-retrying assertion'ı — daha az flaky).
      await expect(typingIndicator2).toBeVisible({ timeout: 5000 });
      const typingText = (await typingIndicator2.textContent()) || '';
      console.log(`User2 typing indicator text: "${typingText}"`);
      expect(typingText.toLowerCase()).toMatch(/yaz/i);
      // Grup sohbeti: gösterge kimin yazdığını da içermeli (chat.js:
      // (payload.username || otherUsername) + ' ' + verb)
      expect(typingText).toContain(testData.user1.username);

      // User1 durunca (blur -> sendTyping(false)) gösterge User2'de kaybolmalı.
      await messageInput1.blur();
      await expect(typingIndicator2).toBeHidden({ timeout: 3000 });
      console.log('✓ PASSED: Typing indicator correctly synced from User1 to User2 and cleared on blur');

      // Check for console errors
      const errors1 = [];
      const errors2 = [];

      page1.on('console', msg => {
        if (msg.type() === 'error') errors1.push(`Page1: ${msg.text()}`);
      });
      page2.on('console', msg => {
        if (msg.type() === 'error') errors2.push(`Page2: ${msg.text()}`);
      });

      // Wait for any async errors
      await page1.waitForTimeout(500);
      await page2.waitForTimeout(500);

      // Verify no critical errors
      const allErrors = [...errors1, ...errors2];
      const criticalErrors = allErrors.filter(e => !e.includes('404') && !e.includes('Failed to fetch'));

      if (criticalErrors.length > 0) {
        console.warn(`Errors found: ${criticalErrors.join('; ')}`);
      }
      expect(criticalErrors.length).toBe(0);

      console.log('✓ Test completed');
    } finally {
      await page1.close();
      await page2.close();
      await context1.close();
      await context2.close();
    }
  });

  test('WebSocket Realtime connection should be established', async ({ page }) => {
    const wsConnections = [];
    const errors = [];

    page.on('websocket', ws => {
      console.log('WebSocket connection:', ws.url().substring(0, 80) + '...');
      wsConnections.push(ws.url());
    });

    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    // Navigate to messages page
    await page.goto('http://127.0.0.1:5000/messages');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // Should have at least one WebSocket connection (Supabase Realtime)
    expect(wsConnections.length).toBeGreaterThan(0);
    console.log(`WebSocket connections: ${wsConnections.length}`);

    // At least one should be Realtime
    const hasRealtime = wsConnections.some(url => url.includes('supabase') && url.includes('realtime'));
    expect(hasRealtime).toBe(true);

    // No critical errors
    const criticalErrors = errors.filter(e => !e.includes('404'));
    expect(criticalErrors.length).toBe(0);

    console.log('✓ WebSocket test passed');
  });
});
