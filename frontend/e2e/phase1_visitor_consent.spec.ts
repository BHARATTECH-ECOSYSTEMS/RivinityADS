/**
 * RivinityADS — Phase 1 Visitor Consent Gate E2E Specification
 *
 * Deterministic Playwright browser test suite using page.route() stubs
 * to verify front-end SDK behavior, DOM rendering, and browser storage invariants
 * without external backend network dependencies.
 *
 * Verifies all 8 mandatory E2E user and security flows:
 * 1. Initial visit: banner displayed, ad slot shows placeholder, 0 ad requests.
 * 2. Decline & reload: Decline clicked, banner hides, reload preserves decline, 0 ad requests.
 * 3. Accept analytics only: Manage dialog, analytics checked, advertising unchecked -> 0 ad requests.
 * 4. Accept advertising: Accept All clicked, banner hides, ad requested with consent_token, ad rendered.
 * 5. Withdraw consent: Privacy Choices reopen, uncheck advertising, save -> ads blocked.
 * 6. API failure fail-closed: 500 error on /consent -> banner renders visible error, ads blocked.
 * 7. Expired/outdated policy version in localStorage -> SDK purges storage, re-prompts banner.
 * 8. Non-consented tracking & conversion capture suppressed in browser.
 */

import { test, expect, Page, Route } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SITE_SLUG = 'e2e-consent-site';
const SLOT_ID = 'e2e_banner_slot';
const POLICY_VERSION = 'v1.0';

// Read the actual rivinity.js file to serve in the browser
const SDK_PATH = path.resolve(__dirname, '../../backend/liteads/static/rivinity.js');
const RIVINITY_JS_CONTENT = fs.readFileSync(SDK_PATH, 'utf-8');

function buildHtmlPage(options: { slotId?: string; siteSlug?: string } = {}): string {
  const slot = options.slotId || SLOT_ID;
  const site = options.siteSlug || SITE_SLUG;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Consent E2E Test Site</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 2rem; background: #f1f5f9; }
    .page-content { max-width: 800px; margin: 0 auto; background: #ffffff; padding: 2rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .btn { cursor: pointer; border-radius: 6px; padding: 0.5rem 1rem; border: none; font-weight: 500; }
    .btn-primary { background: #2563eb; color: #ffffff; }
    .btn-secondary { background: #64748b; color: #ffffff; }
    .btn-outline { background: transparent; color: #94a3b8; border: 1px solid #475569; }
  </style>
</head>
<body>
  <div class="page-content">
    <h1>RivinityADS Publisher Article</h1>
    <p>This is a monetized publisher site with privacy-first ad delivery gating.</p>

    <div id="ad-container" style="margin: 2rem 0;">
      <script
        src="/static/rivinity.js"
        data-slot="${slot}"
        data-site="${site}"
        data-format="1"
        data-num-ads="1"
      ></script>
    </div>

    <p>Additional article paragraph content...</p>
  </div>

  <!-- Consent Banner -->
  <aside id="rivinity-consent-banner" class="rivinity-consent-banner" role="dialog" aria-label="Privacy and Advertising Choices" data-site="${site}" data-policy-version="${POLICY_VERSION}" style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#f8fafc;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;z-index:999;box-shadow:0 -4px 12px rgba(0,0,0,0.15);font-size:0.875rem;">
    <div style="max-width:700px;">
      <strong>Privacy &amp; Advertising Choices:</strong> This website is monetized through RivinityADS. We and our advertising partners use cookies and device identifiers to personalize and measure ad performance. Non-essential categories default to denied.
    </div>
    <div style="display:flex;gap:0.75rem;flex-shrink:0;">
      <button id="rivinity-consent-accept" class="btn btn-primary">Accept All</button>
      <button id="rivinity-consent-decline" class="btn btn-secondary">Decline</button>
      <button id="rivinity-consent-manage" class="btn btn-outline">Manage</button>
    </div>
  </aside>

  <!-- Granular Consent Management Dialog -->
  <dialog id="rivinity-consent-modal" class="rivinity-consent-modal" role="dialog" aria-label="Manage Privacy Preferences" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#1e293b;color:#f8fafc;padding:1.5rem 2rem;border-radius:12px;border:1px solid #334155;z-index:1000;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);max-width:500px;width:90%;">
    <h3 style="margin-top:0;font-size:1.125rem;color:#f8fafc;">Privacy &amp; Consent Preferences</h3>
    <p style="font-size:0.8125rem;color:#94a3b8;line-height:1.4;">Configure how your data and identifiers are processed for advertising and analytics. Non-essential categories are disabled by default.</p>
    <div style="margin:1rem 0;display:flex;flex-direction:column;gap:0.75rem;">
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Essential Services:</strong> Required for site operation</span>
        <input type="checkbox" id="rivinity-consent-opt-necessary" checked disabled style="cursor:not-allowed;">
      </label>
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Advertising &amp; Delivery:</strong> Personalized ad delivery and measurement</span>
        <input type="checkbox" id="rivinity-consent-opt-ad" style="cursor:pointer;">
      </label>
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Analytics:</strong> Traffic and outcome measurement</span>
        <input type="checkbox" id="rivinity-consent-opt-analytics" style="cursor:pointer;">
      </label>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:0.75rem;margin-top:1.25rem;">
      <button id="rivinity-consent-modal-cancel" class="btn btn-secondary">Cancel</button>
      <button id="rivinity-consent-modal-save" class="btn btn-primary">Save Preferences</button>
    </div>
  </dialog>

  <!-- Persistent Reopen Trigger -->
  <button id="rivinity-consent-reopen" class="btn" style="display:none;position:fixed;bottom:12px;left:12px;z-index:998;background:#1e293b;color:#94a3b8;font-size:0.6875rem;padding:4px 10px;border-radius:9999px;border:1px solid #334155;cursor:pointer;">Privacy Choices</button>
</body>
</html>`;
}

test.describe('Phase 1: Real-Browser Visitor Consent Gating E2E', () => {
  let adRequestCalls: any[] = [];
  let consentApiCalls: any[] = [];
  let fakeTokenCounter = 1;

  test.beforeEach(async ({ page }) => {
    adRequestCalls = [];
    consentApiCalls = [];

    // Intercept /static/rivinity.js to serve the live SDK
    await page.route('**/static/rivinity.js', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/javascript',
        body: RIVINITY_JS_CONTENT,
      });
    });

    // Default API mocks
    await page.route(`**/api/v1/website/public/${SITE_SLUG}/consent`, async (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      consentApiCalls.push(body);
      const isGranted = !!(body.categories && body.categories.advertising);
      const token = `${SITE_SLUG}.101.${isGranted ? '1' : '0'}.${body.version || 'v1.0'}.${Math.floor(Date.now() / 1000)}.${Math.floor(Date.now() / 1000) + 604800}.mock_hmac_sig_${fakeTokenCounter++}`;

      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          message: isGranted ? 'Visitor consent recorded successfully' : 'Visitor consent declined',
          consent_id: 42,
          categories: {
            necessary: true,
            advertising: isGranted,
            analytics: !!(body.categories && body.categories.analytics),
          },
          is_granted: isGranted,
          consent_token: token,
          expires_at: Math.floor(Date.now() / 1000) + 604800,
        }),
      });
    });

    await page.route('**/api/v1/ad/request', async (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      adRequestCalls.push(body);

      // Verify token
      if (body.consent_token && body.consent_token.includes('.1.')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ads: [
              {
                ad_id: 'ad_101_201',
                campaign_id: 101,
                creative_id: 201,
                creative: {
                  title: 'E2E Verified Advertisement',
                  description: 'Self-hosted transparent digital ad',
                  landing_url: 'https://example.com/landing',
                  image_url: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="728" height="90"><rect width="728" height="90" fill="%232563eb"/><text x="20" y="50" fill="%23ffffff" font-size="20">Rivinity Verified Ad</text></svg>',
                  width: 728,
                  height: 90,
                  creative_type: 'banner',
                },
                tracking: {
                  impression_url: 'http://localhost/api/v1/event/track?type=impression&req=req_1&ad=ad_1&slot=s1&sig=1.sig&consent_token=' + encodeURIComponent(body.consent_token),
                  click_url: 'http://localhost/api/v1/event/track?type=click&req=req_1&ad=ad_1&slot=s1&sig=1.sig&consent_token=' + encodeURIComponent(body.consent_token),
                },
              },
            ],
            total: 1,
            count: 1,
          }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ads: [], total: 0, count: 0 }),
        });
      }
    });

    // Intercept tracking pixels
    await page.route('**/api/v1/event/track*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, message: 'Event tracked' }),
      });
    });
  });

  // Scenario 1: Initial visit
  test('1. Initial visit: consent banner displayed, ad slot shows placeholder, 0 ad requests', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Banner must be visible
    const banner = page.locator('#rivinity-consent-banner');
    await expect(banner).toBeVisible();

    // Reopen button must be hidden
    const reopen = page.locator('#rivinity-consent-reopen');
    await expect(reopen).not.toBeVisible();

    // Ad slot must show placeholder
    const placeholder = page.locator('.rivinity-ad-placeholder');
    await expect(placeholder).toBeVisible();
    await expect(placeholder.locator('.ad-status')).toHaveText('Consent Required');

    // No network requests to /api/v1/ad/request must have occurred
    expect(adRequestCalls.length).toBe(0);
  });

  // Scenario 2: Decline & reload
  test('2. Decline & reload: clicking Decline hides banner, sets non-billable, persists across reloads', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Click Decline
    const declineBtn = page.locator('#rivinity-consent-decline');
    await declineBtn.click();

    // Banner hides, reopen shows
    await expect(page.locator('#rivinity-consent-banner')).not.toBeVisible();
    await expect(page.locator('#rivinity-consent-reopen')).toBeVisible();

    // Placeholder indicates consent declined
    const placeholder = page.locator('.rivinity-ad-placeholder');
    await expect(placeholder.locator('.ad-status')).toHaveText('Consent Declined');
    expect(adRequestCalls.length).toBe(0);

    // Reload page: consent choice persists
    await page.reload();
    await expect(page.locator('#rivinity-consent-banner')).not.toBeVisible();
    await expect(page.locator('#rivinity-consent-reopen')).toBeVisible();
    await expect(page.locator('.rivinity-ad-placeholder .ad-status')).toHaveText('Consent Declined');
    expect(adRequestCalls.length).toBe(0);
  });

  // Scenario 3: Accept analytics only
  test('3. Accept analytics only: Manage dialog, analytics checked, advertising unchecked -> 0 ad requests', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Click Manage
    await page.locator('#rivinity-consent-manage').click();
    const modal = page.locator('#rivinity-consent-modal');
    await expect(modal).toBeVisible();

    // Check Analytics, ensure Advertising is unchecked
    await page.locator('#rivinity-consent-opt-analytics').check();
    await page.locator('#rivinity-consent-opt-ad').uncheck();

    // Save preferences
    await page.locator('#rivinity-consent-modal-save').click();

    await expect(modal).not.toBeVisible();
    await expect(page.locator('#rivinity-consent-banner')).not.toBeVisible();
    await expect(page.locator('#rivinity-consent-reopen')).toBeVisible();

    // Ads must remain suppressed
    await expect(page.locator('.rivinity-ad-placeholder .ad-status')).toHaveText('Consent Declined');
    expect(adRequestCalls.length).toBe(0);
  });

  // Scenario 4: Accept advertising
  test('4. Accept advertising: Accept All clicked, banner hides, ad requested with consent_token, ad rendered', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Click Accept All
    await page.locator('#rivinity-consent-accept').click();

    // Banner hides, reopen shows
    await expect(page.locator('#rivinity-consent-banner')).not.toBeVisible();
    await expect(page.locator('#rivinity-consent-reopen')).toBeVisible();

    // An ad request must have been dispatched
    await expect.poll(() => adRequestCalls.length).toBe(1);
    expect(adRequestCalls[0].slot_id).toBe(SLOT_ID);
    expect(adRequestCalls[0].consent_token).toBeTruthy();
    expect(adRequestCalls[0].consent_token).toContain('.1.');

    // Ad rendered in DOM
    const adContainer = page.locator('[data-ad-id="ad_101_201"]');
    await expect(adContainer).toBeVisible();
    await expect(adContainer.locator('img')).toBeVisible();
  });

  // Scenario 5: Withdraw consent
  test('5. Withdraw consent: reopen dialog, uncheck advertising, save -> ads blocked', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Step A: Accept advertising
    await page.locator('#rivinity-consent-accept').click();
    await expect(page.locator('[data-ad-id="ad_101_201"]')).toBeVisible();

    // Step B: Reopen via Privacy Choices button
    await page.locator('#rivinity-consent-reopen').click();
    const modal = page.locator('#rivinity-consent-modal');
    await expect(modal).toBeVisible();

    // Uncheck Advertising and save
    await page.locator('#rivinity-consent-opt-ad').uncheck();
    await page.locator('#rivinity-consent-modal-save').click();
    await expect(modal).not.toBeVisible();

    // Verify stored consent in localStorage is now revoked
    const stored = await page.evaluate((key) => localStorage.getItem(key), `rv_consent_${SITE_SLUG}`);
    const parsed = JSON.parse(stored || '{}');
    expect(parsed.is_granted).toBe(false);
    expect(parsed.categories.advertising).toBe(false);

    // Placeholder reverts to consent declined
    await expect(page.locator('.rivinity-ad-placeholder .ad-status')).toHaveText('Consent Declined');
  });

  // Scenario 6: API failure fail-closed
  test('6. API failure fail-closed: 500 error on /consent -> banner renders visible error, ads blocked', async ({ page }) => {
    // Override /consent route to return 500
    await page.route(`**/api/v1/website/public/${SITE_SLUG}/consent`, async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'InternalServerError', message: 'Database connection failed' }),
      });
    });

    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Click Accept All
    await page.locator('#rivinity-consent-accept').click();

    // Banner must NOT hide; must display error message
    const banner = page.locator('#rivinity-consent-banner');
    await expect(banner).toBeVisible();
    const errorEl = page.locator('.rivinity-consent-error');
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText('Unable to record consent');

    // Ads must remain strictly blocked
    expect(adRequestCalls.length).toBe(0);
    await expect(page.locator('.rivinity-ad-placeholder .ad-status')).toHaveText('Consent Required');
  });

  // Scenario 7: Expired / updated policy version
  test('7. Outdated policy version in localStorage: SDK purges storage and re-renders banner', async ({ page }) => {
    await page.route(`http://localhost:5173/sites/${SITE_SLUG}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    // Seed localStorage with outdated policy version 'v0.9'
    await page.addInitScript((key) => {
      localStorage.setItem(
        key,
        JSON.stringify({
          site_slug: 'e2e-consent-site',
          is_granted: true,
          version: 'v0.9', // outdated version
          categories: { necessary: true, advertising: true, analytics: true },
          consent_token: 'outdated_token_v0.9',
          expires_at: Math.floor(Date.now() / 1000) + 100000,
        })
      );
    }, `rv_consent_${SITE_SLUG}`);

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}`);

    // Banner must be visible because version was outdated
    await expect(page.locator('#rivinity-consent-banner')).toBeVisible();

    // LocalStorage must have been purged by SDK
    const stored = await page.evaluate((key) => localStorage.getItem(key), `rv_consent_${SITE_SLUG}`);
    expect(stored).toBeNull();

    // Ad slot must show Consent Required placeholder, 0 ad requests
    await expect(page.locator('.rivinity-ad-placeholder .ad-status')).toHaveText('Consent Required');
    expect(adRequestCalls.length).toBe(0);
  });

  // Scenario 8: Non-consented tracking & conversion capture suppressed
  test('8. Non-consented tracking & conversion capture suppressed in browser', async ({ page }) => {
    // Direct verification of client SDK behavior: no tracking tokens stored without consent
    await page.route(`**/sites/${SITE_SLUG}*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: buildHtmlPage(),
      });
    });

    await page.goto(`http://localhost:5173/sites/${SITE_SLUG}?rv_click_token=test_attribution_click_token_123`);
    await page.waitForFunction(() => typeof (window as any).Rivinity !== 'undefined');

    // In unconsented state, rv_click_token must NOT be stored in sessionStorage
    const storedToken = await page.evaluate(() => sessionStorage.getItem('rv_click_token'));
    expect(storedToken).toBeNull();

    // Invoking conversion tracking without consent must resolve with reason: consent_required
    const conversionResult = await page.evaluate(async () => {
      return (window as any).Rivinity.trackConversion({
        value: 100.0,
        orderId: 'order_test_999',
      });
    });
    expect(conversionResult.success).toBe(false);
    expect(conversionResult.reason).toBe('consent_required');
  });
});
