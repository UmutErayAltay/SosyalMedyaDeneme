import { test, expect } from '@playwright/test';

// Kök neden (2026-07-29, kullanıcı raporu): discover/feed/follow_list/hashtag/
// post_detail/profile/search şablonlarının HEPSİ kendi bundle <script>
// etiketlerini {% block content %} İÇİNDE tutuyordu — bu, base.html'deki
// common.bundle.js (window.escapeHtml/window.ICONS'u tanımlayan, {% block
// scripts %} pattern'iyle SONRA yüklenmesi gereken) script'inden ÖNCE
// render/çalışıyordu. comments.js gibi dosyalar `var escapeHtml =
// window.escapeHtml` ile bunu SADECE bir kez (IIFE yüklenirken) yakaladığı
// için sonradan common.bundle.js gelse bile o değişken kalıcı olarak
// undefined kalıyordu — her yorum gönderiminde "TypeError: escapeHtml is
// not a function" fırlatıp sunucu işlemi BAŞARILI olsa bile istemcide
// "Yorum gönderilemedi" hatası gösteriyordu (F5'te yorum zaten oradaydı).
// py_compile/node --check bunu YAKALAMAZ (CLAUDE.md notu) — sadece bu tür
// bir e2e testi script SIRASINI/DOM'daki gerçek durumu doğrulayabilir.
//
// Fix: tüm bu şablonlar reels.html/messages/conversation.html'deki gibi
// {% block scripts %} kullanacak şekilde düzeltildi (block content'ten SONRA
// render edilir, base.html'in common.bundle.js'inden SONRA gelir).

const PAGES = [
  { name: 'feed', url: '/' },
  { name: 'discover', url: '/kesfet' },
  { name: 'profile', url: '/u/admin' },
  { name: 'follow_list', url: '/u/admin/followers' },
  { name: 'search', url: '/search?q=test' },
  { name: 'hashtag', url: '/hashtag/emir' },
];

test.describe('Script Load Order (common bundle önce yüklenmeli)', () => {
  for (const { name, url } of PAGES) {
    test(`${name} sayfasında common.bundle.js, sayfa-özel bundle'lardan ÖNCE yüklenir`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');

      const scriptSrcs = await page.evaluate(() =>
        Array.from(document.querySelectorAll('script[src]')).map(s => s.getAttribute('src'))
      );

      const commonIndex = scriptSrcs.findIndex(src => src.includes('common.bundle.js'));
      const pageSpecificIndex = scriptSrcs.findIndex(src =>
        src.includes('post-interactions.bundle.js') ||
        src.includes('feed-extra.bundle.js') ||
        src.includes('profile-extra.bundle.js') ||
        src.includes('post-detail-extra.bundle.js')
      );

      expect(commonIndex, `${name}: common.bundle.js DOM'da bulunamadı`).toBeGreaterThanOrEqual(0);
      if (pageSpecificIndex >= 0) {
        expect(commonIndex, `${name}: common.bundle.js sayfa-özel bundle'dan SONRA geliyor`).toBeLessThan(pageSpecificIndex);
      }

      // Asıl kanıt: window.escapeHtml/window.ICONS gerçekten fonksiyon/obje
      // olarak tanımlı mı (sadece script SIRASI değil, GERÇEK sonucu doğrular).
      const globals = await page.evaluate(() => ({
        escapeHtml: typeof window.escapeHtml,
        ICONS: typeof window.ICONS,
      }));
      expect(globals.escapeHtml, `${name}: window.escapeHtml tanımlı değil`).toBe('function');
      expect(globals.ICONS, `${name}: window.ICONS tanımlı değil`).toBe('object');
    });
  }

  test('post_detail sayfasında common.bundle.js post-detail-extra.bundle.js\'den ÖNCE yüklenir', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    const firstPost = page.locator('article.card.post').first();
    await expect(firstPost).toBeVisible({ timeout: 5000 });
    const expectedUrl = await firstPost.getAttribute('data-post-url');
    await firstPost.locator('.post-content').click();
    await page.waitForURL(`**${expectedUrl}`, { timeout: 5000 });
    await page.waitForLoadState('networkidle');

    const scriptSrcs = await page.evaluate(() =>
      Array.from(document.querySelectorAll('script[src]')).map(s => s.getAttribute('src'))
    );
    const commonIndex = scriptSrcs.findIndex(src => src.includes('common.bundle.js'));
    const extraIndex = scriptSrcs.findIndex(src => src.includes('post-detail-extra.bundle.js'));
    expect(commonIndex).toBeGreaterThanOrEqual(0);
    expect(extraIndex).toBeGreaterThanOrEqual(0);
    expect(commonIndex).toBeLessThan(extraIndex);

    const globals = await page.evaluate(() => ({
      escapeHtml: typeof window.escapeHtml,
      ICONS: typeof window.ICONS,
    }));
    expect(globals.escapeHtml).toBe('function');
    expect(globals.ICONS).toBe('object');
  });
});
