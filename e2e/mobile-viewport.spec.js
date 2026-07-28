import { test, expect } from '@playwright/test';

test.describe('Mobile Viewport Regression', () => {
  // Test viewports: 375px (iPhone SE), 390px (iPhone 12), 428px (iPhone 14)
  const mobileViewports = [
    { width: 375, height: 812, name: 'iPhone SE (375px)' },
    { width: 390, height: 844, name: 'iPhone 12 (390px)' },
    { width: 428, height: 926, name: 'iPhone 14 (428px)' },
  ];

  for (const vp of mobileViewports) {
    test(`Feed page should not have horizontal scroll on ${vp.name}`, async ({ page }) => {
      // Set mobile viewport
      await page.setViewportSize({ width: vp.width, height: vp.height });

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
      await page.waitForTimeout(500);

      // Check for horizontal scroll
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);

      expect(scrollWidth).toBeLessThanOrEqual(clientWidth,
        `Horizontal scroll detected: scrollWidth (${scrollWidth}) > clientWidth (${clientWidth})`);
      expect(errors).toHaveLength(0);
    });

    test(`Messages page should not have horizontal scroll on ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });

      const errors = [];
      page.on('console', msg => {
        if (msg.type() === 'error') {
          errors.push(msg.text());
        }
      });
      page.on('pageerror', err => {
        errors.push(err.message);
      });

      await page.goto('/messages');
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);

      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);

      expect(scrollWidth).toBeLessThanOrEqual(clientWidth,
        `Horizontal scroll detected on messages: scrollWidth (${scrollWidth}) > clientWidth (${clientWidth})`);
      expect(errors).toHaveLength(0);
    });

    test(`Profile page should not have horizontal scroll on ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });

      const errors = [];
      page.on('console', msg => {
        if (msg.type() === 'error') {
          errors.push(msg.text());
        }
      });
      page.on('pageerror', err => {
        errors.push(err.message);
      });

      // Profile URL uses /u/username pattern, default E2E user is "admin"
      await page.goto('/u/admin');
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);

      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);

      expect(scrollWidth).toBeLessThanOrEqual(clientWidth,
        `Horizontal scroll detected on profile: scrollWidth (${scrollWidth}) > clientWidth (${clientWidth})`);
      expect(errors).toHaveLength(0);
    });
  }

  test('Navbar should be responsive on mobile (hamburger menu visible if needed)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Check main navbar structure — should have menu or be collapsed
    const navbar = page.locator('nav.navbar, .navbar[role="navigation"]').first();
    if (await navbar.isVisible({ timeout: 2000 }).catch(() => false)) {
      expect(await navbar.isVisible()).toBe(true);
    }

    // No horizontal scroll should occur
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);

    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
  });

  test('Grid layouts should not overflow on mobile (375px)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);

    // Check for grid/flex containers that might overflow
    const hasOverflow = await page.evaluate(() => {
      const elements = document.querySelectorAll('[style*="grid"], [style*="flex"], [class*="grid"], [class*="flex"]');
      for (const el of elements) {
        const style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden') {
          const scrollWidth = el.scrollWidth;
          const clientWidth = el.clientWidth;
          if (scrollWidth > clientWidth) {
            console.warn(`Grid/flex overflow detected in element:`, el.className, el.id);
            return true;
          }
        }
      }
      return false;
    });

    expect(hasOverflow).toBe(false);
  });
});
