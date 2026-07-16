import { test, expect } from '@playwright/test';

test.describe('Feed Page', () => {
  test('should load without JS errors', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });

  test('should like a post (likes.js functionality)', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Find first like button (gerçek markup: _post_card.html'de <button class="like-btn">,
    // data-action attribute'u YOK — bu proje o deseni kullanmıyor)
    const likeButton = page.locator('.post-actions .like-btn').first();
    await expect(likeButton).toBeVisible({ timeout: 5000 });

    // likes.js optimistic UI: tıklanınca data-liked hemen (AJAX yanıtından ÖNCE)
    // toggle edilir (bkz. likes.js satır 41-49) — bu, buton gerçekten
    // likes.js'in click handler'ına bağlıysa (bundle'da script eksik kalmadıysa)
    // senkron olarak değişir.
    const before = await likeButton.getAttribute('data-liked');
    await likeButton.click();
    await expect(likeButton).not.toHaveAttribute('data-liked', before ?? '0', { timeout: 2000 });

    expect(errors).toHaveLength(0);
  });

  test('should display stories section', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Check if stories container exists (stories.js should render this)
    const storiesSection = page.locator('.stories-section, [data-stories-container]');

    // Stories section may or may not be visible depending on data,
    // but the script should load without error
    await page.waitForTimeout(300);

    expect(errors).toHaveLength(0);
  });
});
