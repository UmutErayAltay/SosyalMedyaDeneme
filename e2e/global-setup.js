import { chromium } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const authDir = path.join(__dirname, '.auth');
const authFile = path.join(authDir, 'state.json');

// .env proje kökünden yüklenir (Python tarafının load_dotenv'iyle aynı dosya) —
// gerçek kimlik bilgileri KODA GÖMÜLMEZ, sadece ortam değişkeninden okunur
// (bu repo public, hardcoded şifre commit edilirse herkese açık sızardı).
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const email = process.env.E2E_ADMIN_EMAIL;
const password = process.env.E2E_ADMIN_PASSWORD;

async function globalSetup() {
  if (!email || !password) {
    throw new Error(
      'E2E_ADMIN_EMAIL ve E2E_ADMIN_PASSWORD ortam değişkenleri gerekli. ' +
      '.env dosyanıza ekleyin (test için kullanılan bir hesabın email/şifresi — ' +
      'gerçek/üretim hesabı KULLANMAYIN, ayrı bir test hesabı tercih edin).'
    );
  }

  // Ensure auth directory exists
  if (!fs.existsSync(authDir)) {
    fs.mkdirSync(authDir, { recursive: true });
  }

  const browser = await chromium.launch();
  const page = await browser.newPage();

  try {
    // Navigate to login page
    await page.goto('http://127.0.0.1:5000/login');

    // Fill in credentials
    await page.fill('input[name="email"]', email);
    await page.fill('input[name="password"]', password);

    // Submit form
    await page.click('button[type="submit"]');

    // Wait for navigation to complete (usually redirects to feed or dashboard)
    await page.waitForURL('**/feed|**/home|**/profile', { timeout: 10000 }).catch(() => {
      // If no URL match, just wait for page to stabilize
      return page.waitForLoadState('networkidle');
    });

    // Save storage state (cookies, session storage, etc.)
    await page.context().storageState({ path: authFile });
    console.log(`✓ Login successful, state saved to ${authFile}`);
  } catch (error) {
    console.error('✗ Login failed during global setup:', error.message);
    throw error;
  } finally {
    await browser.close();
  }
}

export default globalSetup;
