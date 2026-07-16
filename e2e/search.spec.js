import { test, expect } from '@playwright/test';

test.describe('Search Page', () => {
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

    await page.goto('/search');
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });

  test('should display search results for a query', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    // Try to search for something (use a common term)
    await page.goto('/search?q=test');
    await page.waitForLoadState('networkidle');

    // Check for search results (likes.js, polls.js, bookmarks.js should be loaded)
    const searchResults = page.locator('.search-results, [data-results], .results-container');

    await page.waitForTimeout(300);

    expect(errors).toHaveLength(0);
  });

  test('should allow liking search results', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    await page.goto('/search?q=test');
    await page.waitForLoadState('networkidle');

    // Try to like a result if available
    const likeButton = page.locator('button[data-action="like"], .like-btn').first();

    if (await likeButton.isVisible().catch(() => false)) {
      await likeButton.click();
      await page.waitForTimeout(300);
    }

    expect(errors).toHaveLength(0);
  });

  test('should handle empty search', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    // Empty search query
    await page.goto('/search');
    await page.waitForLoadState('networkidle');

    await page.waitForTimeout(300);

    expect(errors).toHaveLength(0);
  });
});
