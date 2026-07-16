import { test, expect } from '@playwright/test';

// Gerçek markup: _post_card.html'de <article class="card post" data-post-url="...">,
// tıklama postClick.js'in document-level delegation'ı ile yakalanıp
// window.location.href = card.dataset.postUrl ile navigate eder — AMA
// a/button/img/video üzerine tıklama HARİÇ tutulur (postClick.js satır 31-37).
// Bu yüzden kartın güvenli bir alt-elemanına (.post-content, resim/link
// İÇERMEYEN metin alanı) tıklanır, kartın kendisine değil.

async function goToFirstPostDetail(page) {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  const firstPost = page.locator('article.card.post').first();
  await expect(firstPost).toBeVisible({ timeout: 5000 });

  const expectedUrl = await firstPost.getAttribute('data-post-url');
  await firstPost.locator('.post-content').click();
  await page.waitForURL(`**${expectedUrl}`, { timeout: 5000 });
  return expectedUrl;
}

test.describe('Post Detail Page', () => {
  test('should load without JS errors', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await goToFirstPostDetail(page);
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });

  test('should display post content and comments section', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await goToFirstPostDetail(page);
    await page.waitForLoadState('networkidle');

    // Gerçek markup: post_detail.html'de <section class="comment-list" id="comment-list">
    // (bkz. mention-autocomplete özelliği için data-valid-usernames attribute'u da orada)
    await expect(page.locator('#comment-list')).toBeVisible();

    expect(errors).toHaveLength(0);
  });

  test('should navigate from feed to a distinct post detail URL', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    const expectedUrl = await goToFirstPostDetail(page);
    // postClick.js'in gerçekten çalışıp URL'i değiştirdiğini doğrula
    // (script eksik kalsaydı sayfa feed'de kalırdı, bu assert bunu yakalar)
    expect(page.url()).toContain(expectedUrl);

    expect(errors).toHaveLength(0);
  });
});
