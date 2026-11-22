/**
 * RivinityADS — Phase 1 Visitor Consent Gate Live Backend E2E Specification
 *
 * Real-browser Playwright test suite executing against the live running FastAPI backend
 * and database (no page.route() mocks/stubs for backend consent or ad APIs).
 *
 * Coverage:
 * 1. Live health and static SDK asset availability: /health returns 200, /static/rivinity.js served.
 * 2. Initial visit against live site: banner visible, placeholder shows Consent Required, 0 ad requests.
 * 3. Decline consent in live browser: POST /consent records decline, persists choice, 0 ad requests.
 * 4. Reopen modal & accept: real POST /consent issues HMAC token, ad request succeeds, ad rendered.
 * 5. Reopen modal & withdraw: revokes consent in live DB, suppresses ad delivery and prevents replay.
 */

import { test, expect } from '@playwright/test';

const BACKEND_URL = process.env.LITEADS_BACKEND_URL || 'http://127.0.0.1:8000';

async function isBackendReachable(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

test.describe('Phase 1 Live Backend Visitor Consent E2E', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await isBackendReachable();
  });

  test.beforeEach(async () => {
    test.skip(
      !backendAvailable,
      `FastAPI backend is not running on ${BACKEND_URL}; skipping live backend tests`
    );
  });

  test('1. Live backend health and static SDK asset availability', async ({ page }) => {
    // 1. Health endpoint returns 200 OK
    const healthRes = await page.goto(`${BACKEND_URL}/health`);
    expect(healthRes?.status()).toBe(200);

    // 2. Static SDK served at /static/rivinity.js with verified placeholder states
    const sdkRes = await page.goto(`${BACKEND_URL}/static/rivinity.js`);
    expect(sdkRes?.status()).toBe(200);
    const sdkText = await sdkRes?.text();
    expect(sdkText).toContain('Consent Required');
    expect(sdkText).toContain('Consent Declined');
    expect(sdkText).toContain('Monetisation Inactive');
    expect(sdkText).toContain('Preview / Sandbox Mode');
    expect(sdkText).toContain('No Fill');
  });

  test('2. Initial visit against live site: banner visible, placeholder shows Consent Required, 0 ad requests', async ({ page }) => {
    let adRequestCount = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/v1/ad/request')) {
        adRequestCount++;
      }
    });

    // Navigate to published page on live backend
    await page.goto(`${BACKEND_URL}/sites/live-news`);

    // Banner is visible in DOM
    const banner = page.locator('#rivinity-consent-banner');
    await expect(banner).toBeVisible();

    // Ad placeholder renders "Consent Required" status
    const placeholder = page.locator('.rivinity-ad-placeholder');
    await expect(placeholder).toBeVisible();
    await expect(placeholder).toContainText('Consent Required');

    // Fail-closed guarantee: zero ad requests before visitor choice
    expect(adRequestCount).toBe(0);
  });

  test('3. Decline consent in live browser: POST /consent records decline, persists choice, 0 ad requests', async ({ page }) => {
    let adRequestCount = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/v1/ad/request')) {
        adRequestCount++;
      }
    });

    await page.goto(`${BACKEND_URL}/sites/live-news`);

    // Click Decline
    const declineBtn = page.locator('#rivinity-consent-decline');
    await expect(declineBtn).toBeVisible();
    await declineBtn.click();

    // Banner should hide
    const banner = page.locator('#rivinity-consent-banner');
    await expect(banner).toBeHidden();

    // Placeholder updates to Consent Declined
    const placeholder = page.locator('.rivinity-ad-placeholder');
    await expect(placeholder).toContainText('Consent Declined');

    // Verify localStorage has persisted is_granted: false
    const consent = await page.evaluate(() => {
      const stored = localStorage.getItem('rivinity_consent_v1.0');
      return stored ? JSON.parse(stored) : null;
    });
    expect(consent).not.toBeNull();
    expect(consent.is_granted).toBe(false);

    // Ad request count remains 0
    expect(adRequestCount).toBe(0);
  });

  test('4. Reopen modal & accept: real POST /consent issues HMAC token, ad request succeeds, ad rendered', async ({ page }) => {
    await page.goto(`${BACKEND_URL}/sites/live-news`);

    // Ensure initial decline or unconsented state
    const banner = page.locator('#rivinity-consent-banner');
    if (await banner.isVisible()) {
      await page.locator('#rivinity-consent-decline').click();
      await expect(banner).toBeHidden();
    }

    // Click Privacy Choices trigger to reopen modal
    const reopenTrigger = page.locator('#rivinity-consent-reopen');
    await expect(reopenTrigger).toBeVisible();
    await reopenTrigger.click();

    // Modal dialog is shown
    const modal = page.locator('#rivinity-consent-modal');
    await expect(modal).toBeVisible();

    // Check advertising option
    const adCheckbox = page.locator('#rivinity-consent-opt-ad');
    await adCheckbox.check();

    // Track ad request
    const adRequestPromise = page.waitForRequest((req) => req.url().includes('/api/v1/ad/request'));

    // Save preferences
    await page.locator('#rivinity-consent-modal-save').click();
    await expect(modal).toBeHidden();

    // Wait for the ad request to be fired with consent token
    const adReq = await adRequestPromise;
    expect(adReq.postDataJSON()?.consent_token).toBeTruthy();

    // Verify placeholder renders ad
    const adContainer = page.locator('#rivinity-ad-container-live_banner_slot');
    await expect(adContainer).toBeVisible();
  });

  test('5. Reopen modal & withdraw: revokes consent in live DB, suppresses ad delivery and prevents replay', async ({ page, request }) => {
    await page.goto(`${BACKEND_URL}/sites/live-news`);

    // Ensure active consent is established so we have a valid token to withdraw and test for replay
    let initialConsent = await page.evaluate(() => {
      const stored = localStorage.getItem('rivinity_consent_v1.0');
      return stored ? JSON.parse(stored) : null;
    });

    if (!initialConsent?.consent_token) {
      const banner = page.locator('#rivinity-consent-banner');
      if (await banner.isVisible()) {
        const acceptBtn = page.locator('#rivinity-consent-accept');
        await acceptBtn.click();
        await expect(banner).toBeHidden();
      }
      initialConsent = await page.evaluate(() => {
        const stored = localStorage.getItem('rivinity_consent_v1.0');
        return stored ? JSON.parse(stored) : null;
      });
    }

    const oldToken = initialConsent?.consent_token;
    expect(oldToken).toBeTruthy();

    // Reopen modal and uncheck advertising
    const reopenTrigger = page.locator('#rivinity-consent-reopen');
    await expect(reopenTrigger).toBeVisible();
    await reopenTrigger.click();
    const modal = page.locator('#rivinity-consent-modal');
    await expect(modal).toBeVisible();

    const adCheckbox = page.locator('#rivinity-consent-opt-ad');
    await adCheckbox.uncheck();

    // Save withdrawal
    await page.locator('#rivinity-consent-modal-save').click();
    await expect(modal).toBeHidden();

    // Verify localStorage reflects withdrawal
    const updatedConsent = await page.evaluate(() => {
      const stored = localStorage.getItem('rivinity_consent_v1.0');
      return stored ? JSON.parse(stored) : null;
    });
    expect(updatedConsent.is_granted).toBe(false);

    // Replay attack prevention: Replaying oldToken against live /ad/request returns 0 ads
    if (oldToken) {
      const replayRes = await request.post(`${BACKEND_URL}/api/v1/ad/request`, {
        headers: { Origin: 'http://127.0.0.1:8000' },
        data: {
          slot_id: 'live_banner_slot',
          num_ads: 1,
          consent_token: oldToken,
        },
      });
      expect(replayRes.status()).toBe(200);
      const data = await replayRes.json();
      expect(data.ads.length).toBe(0);
    }
  });
});
