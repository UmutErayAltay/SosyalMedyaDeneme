import { test, expect } from '@playwright/test';

test.describe('Discover Page', () => {
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

    await page.goto('/kesfet');
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });

  test('should display posts and allow interactions', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/kesfet');
    await page.waitForLoadState('networkidle');

    // Check for post elements (gerçek markup: _post_card.html'de <article class="card post">)
    const posts = page.locator('article.card.post');
    const postCount = await posts.count();

    // Try to like the first post if available
    if (postCount > 0) {
      const firstPost = posts.first();
      const likeButton = firstPost.locator('.like-btn').first();

      if (await likeButton.isVisible().catch(() => false)) {
        await likeButton.click();
        await page.waitForTimeout(300);
      }
    }

    expect(errors).toHaveLength(0);
  });

  test('should handle follow button clicks', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/kesfet');
    await page.waitForLoadState('networkidle');

    // Look for follow buttons (follow.js should handle these)
    const followButton = page.locator('button[data-action="follow"], .follow-btn').first();

    if (await followButton.isVisible().catch(() => false)) {
      await followButton.click();
      await page.waitForTimeout(300);
    }

    expect(errors).toHaveLength(0);
  });
});
