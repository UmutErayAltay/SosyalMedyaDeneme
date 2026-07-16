import { test, expect } from '@playwright/test';

// Not: profile.html (sekmeler burada — .profile-tabs, role="tab") ile
// profile_edit.html (sessions.js burada) FARKLI sayfalar/farklı script
// setleri yükler. Global setup E2E_ADMIN_EMAIL/PASSWORD (.env) ile giriş
// yapan hesabın kullanıcı adı "admin" — profil görünümü /u/admin'de.

test.describe('Profile Page (view — /u/admin)', () => {
  test('should load without JS errors', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/u/admin');
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });

  test('should switch profile tabs (profileTabs.js functionality)', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/u/admin');
    await page.waitForLoadState('networkidle');

    // Gerçek markup: profile.html satır 177-190, <div class="profile-tabs">
    // içinde role="tab" butonlar (#tab-posts, #tab-media, #tab-liked, ...)
    const mediaTab = page.locator('#tab-media');
    await expect(mediaTab).toBeVisible({ timeout: 5000 });

    await mediaTab.click();
    // profileTabs.js aktif sekmeyi aria-selected="true" yapar — script
    // eksik kalsaydı bu değişmezdi
    await expect(mediaTab).toHaveAttribute('aria-selected', 'true', { timeout: 2000 });

    expect(errors).toHaveLength(0);
  });

  test('should render profile info without errors', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/u/admin');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('.profile-tabs')).toBeVisible();

    expect(errors).toHaveLength(0);
  });
});

test.describe('Profile Edit Page (/profile/edit)', () => {
  test('should load without JS errors (sessions.js)', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/profile/edit');
    await page.waitForLoadState('networkidle');

    expect(errors).toHaveLength(0);
  });
});
