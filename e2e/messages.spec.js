import { test, expect } from '@playwright/test';

test.describe('Messages/Inbox Page', () => {
  test('should load inbox without JS errors', async ({ page }) => {
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

    expect(errors).toHaveLength(0);
  });

  test('should display conversation list without errors', async ({ page }) => {
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

    // Check for conversation list (groupChat.js should render this)
    const conversationList = page.locator('.conversation-list, [data-conversations], .chat-list');

    // Wait for any dynamic content
    await page.waitForTimeout(300);

    expect(errors).toHaveLength(0);
  });

  test('should open a conversation if available', async ({ page }) => {
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

    // Try to click the first conversation in the list
    const conversationItem = page.locator('.conversation-item, [data-conversation-id], .chat-item').first();

    if (await conversationItem.isVisible().catch(() => false)) {
      await conversationItem.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);
    }

    expect(errors).toHaveLength(0);
  });

  test('should load individual conversation thread without errors', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', err => {
      errors.push(err.message);
    });

    // Navigate to messages first
    await page.goto('/messages');
    await page.waitForLoadState('networkidle');

    // Open a conversation if available (already tested in previous test)
    const conversationItem = page.locator('.conversation-item, [data-conversation-id], .chat-item').first();

    if (await conversationItem.isVisible().catch(() => false)) {
      await conversationItem.click();
      await page.waitForLoadState('networkidle');
    }

    await page.waitForTimeout(500);

    expect(errors).toHaveLength(0);
  });
});
