import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Phase 9 — Comprehensive 3-Role Browser E2E & Accessibility Quality Gate
 *
 * Covers:
 *  1. Advertiser Journey: Campaign detail readiness gates, blocker display,
 *     and activation prevention until approval.
 *  2. Publisher Journey: Property lifecycle, DNS TXT & HTML Meta tag instructions,
 *     and deep links to ad units and integrations.
 *  3. Admin Journey: Operations health telemetry, SLA tracking exception queues,
 *     creative moderation categorization, and double-entry financial reconciliation.
 *  4. Accessibility Smoke Checks: Semantic landmark / heading hierarchy, accessible
 *     button names, and dialog accessibility.
 */

// ── Helpers & Common Route Stubs ─────────────────────────────────────────────

async function setupBaseRoutes(page: Page, role: 'advertiser' | 'publisher' | 'admin') {
  const user = {
    id: role === 'admin' ? 99 : role === 'advertiser' ? 10 : 20,
    email: `${role}@example.com`,
    name: `${role.toUpperCase()} User`,
    role,
    is_active: true,
    email_verified: true,
    advertiser_id: role === 'advertiser' ? 10 : undefined,
    publisher_id: role === 'publisher' ? 20 : undefined,
  };

  await page.route('**/api/v1/auth/refresh', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user,
        access_token: 'mocked_jwt_token',
      }),
    });
  });

  await page.route('**/api/v1/auth/me', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    });
  });

  await page.route('**/health', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        version: '0.1.0',
        database: true,
        redis: true,
      }),
    });
  });

  await page.route('**/api/v1/notifications**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ notifications: [], total: 0, unread_count: 0 }),
    });
  });
}


// ── 1. Advertiser Journey ─────────────────────────────────────────────────────

test.describe('Phase 9 — Journey 1: Advertiser Campaign Readiness & Launch Blockers', () => {
  test('Advertiser detail page blocks activation on unapproved/unverified creatives and enables on readiness pass', async ({ page }) => {
    await setupBaseRoutes(page, 'advertiser');

    const campaignId = 101;
    let campaignStatus = 5; // DRAFT / REVIEW
    let readinessCanActivate = false;

    const mockCampaign = {
      id: campaignId,
      advertiser_id: 10,
      name: 'Summer Growth Campaign',
      status: campaignStatus,
      budget: 1000.0,
      daily_budget: 100.0,
      bid_type: 1,
      spent_total: 0.0,
      spent_today: 0.0,
      budget_daily: 100.0,
      impressions: 0,
      clicks: 0,
      conversions: 0,
      autopilot: false,
      start_date: '2026-06-01',
      end_date: '2026-06-30',
      created_at: '2026-05-15T10:00:00Z',
    };

    await page.route(`**/api/v1/campaign/${campaignId}/readiness`, async (route: Route) => {
      if (!readinessCanActivate) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            campaign_id: campaignId,
            campaign_name: 'Summer Growth Campaign',
            status: 5,
            status_name: 'REVIEW',
            is_ready: false,
            blockers: [
              {
                code: 'CREATIVE_UNAPPROVED',
                category: 'blocker',
                entity_type: 'creative',
                entity_name: 'Hero Banner',
                message: 'All creatives must be approved.',
              },
              {
                code: 'DOMAIN_UNVERIFIED',
                category: 'blocker',
                entity_type: 'creative',
                entity_name: 'Hero Banner',
                message: 'Destination domain is unverified.',
              },
            ],
            warnings: [],
            summary: {
              wallet_balance: 1000.0,
              budget_daily: 100.0,
              budget_total: 1000.0,
              bid_type: 1,
              bid_amount: 5.0,
              freq_cap_daily: 3,
            },
          }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            campaign_id: campaignId,
            campaign_name: 'Summer Growth Campaign',
            status: 5,
            status_name: 'REVIEW',
            is_ready: true,
            blockers: [],
            warnings: [],
            summary: {
              wallet_balance: 1000.0,
              budget_daily: 100.0,
              budget_total: 1000.0,
              bid_type: 1,
              bid_amount: 5.0,
              freq_cap_daily: 3,
            },
          }),
        });
      }
    });

    await page.route(`**/api/v1/campaign/${campaignId}/activate`, async (route: Route) => {
      campaignStatus = 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...mockCampaign, status: 1 }),
      });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/ad-sets`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 1, campaign_id: campaignId, name: 'Default AdSet', status: 1, bid_amount: 5.0, daily_budget: 100.0 },
        ]),
      });
    });

    await page.route(`**/api/v1/creative?**`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 201,
            campaign_id: campaignId,
            name: 'Hero Banner',
            creative_type: 1,
            status: 1,
            moderation_status: 1, // Pending
            landing_url: 'https://myshop.example.com',
          },
        ]),
      });
    });

    await page.route('**/api/v1/advertiser/domains', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 1, domain: 'myshop.example.com', verification_status: 'VERIFIED', is_active: true },
        ]),
      });
    });

    await page.route('**/api/v1/advertiser/reporting**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          advertiser_id: 10,
          start_date: '2026-05-15',
          end_date: '2026-05-22',
          impressions: 0,
          clicks: 0,
          conversions: 0,
          spend: 0,
          ctr: 0,
          daily_breakdown: [],
        }),
      });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/suggestions`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/optimization-state`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(null) });
    });

    await page.route(`**/api/v1/campaign/${campaignId}`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...mockCampaign, status: campaignStatus }),
      });
    });

    // 1. Navigate to campaign detail page
    await page.goto(`/advertiser/campaigns/${campaignId}`);

    // 2. Assert Campaign Title & Review Status rendered
    await expect(page.locator('h1')).toContainText('Summer Growth Campaign');

    // 3. Assert Launch Blockers Alert is displayed
    await expect(page.locator('text=Campaign Not Ready for Launch')).toBeVisible();
    await expect(page.locator('text=All creatives must be approved.')).toBeVisible();

    // 4. Assert Activate Campaign button is disabled while readiness blocked
    const launchBtn = page.getByRole('button', { name: /Activate Campaign/i });
    await expect(launchBtn).toBeDisabled();

    // 5. Flip readiness to pass and reload page
    readinessCanActivate = true;
    await page.reload();

    // 6. Assert Readiness passed: Alert disappears and button is enabled
    await expect(page.locator('text=Campaign Not Ready for Launch')).not.toBeVisible();
    const readyLaunchBtn = page.getByRole('button', { name: /Activate Campaign/i });
    await expect(readyLaunchBtn).toBeEnabled();

    // 7. Click Activate Campaign
    await readyLaunchBtn.click();
    await expect(page.locator('h1')).toContainText('(Active)');
    await expect(page.getByRole('button', { name: /Pause Campaign/i })).toBeVisible();
  });
});


// ── 2. External Publisher Journey ─────────────────────────────────────────────

test.describe('Phase 9 — Journey 2: External Publisher Property Lifecycle & Guidance', () => {
  test('Publisher Properties view renders separate status badges, challenge instructions, and deep links', async ({ page }) => {
    await setupBaseRoutes(page, 'publisher');

    const publisherId = 20;
    const mockProperties = [
      {
        id: 301,
        publisher_id: publisherId,
        name: 'Tech Blog Network',
        domain: 'techblog.example.com',
        status: 1, // Active
        verification_status: 'UNVERIFIED',
        verification_token: 'site_tok_tech_789',
        dns_txt_record: 'liteads-site-verification=site_tok_tech_789',
        html_meta_tag: '<meta name="liteads-site-verification" content="site_tok_tech_789">',
        created_at: '2026-10-01T10:00:00Z',
      },
      {
        id: 302,
        publisher_id: publisherId,
        name: 'Gaming Daily',
        domain: 'gamingdaily.example.com',
        status: 1, // Active
        verification_status: 'VERIFIED',
        verification_token: 'site_tok_game_123',
        dns_txt_record: 'liteads-site-verification=site_tok_game_123',
        html_meta_tag: '<meta name="liteads-site-verification" content="site_tok_game_123">',
        created_at: '2026-09-15T12:00:00Z',
      },
    ];

    await page.route(`**/api/v1/publisher/${publisherId}/property**`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockProperties),
      });
    });

    await page.route(`**/api/v1/slot**`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 10, publisher_id: publisherId, property_id: 302, slot_id: 'gaming_sidebar', name: 'Sidebar', format: 1, status: 1 },
        ]),
      });
    });

    // 1. Navigate to Publisher Properties
    await page.goto('/publisher/properties');

    // 2. Assert page header and domain rows
    await expect(page.locator('h1')).toContainText('My Properties & Domain Verification');
    await expect(page.locator('text=techblog.example.com')).toBeVisible();
    await expect(page.locator('text=gamingdaily.example.com')).toBeVisible();

    // 3. Assert separate Status and Verification badges
    await expect(page.locator('text=UNVERIFIED').first()).toBeVisible();
    await expect(page.locator('text=VERIFIED').first()).toBeVisible();

    // 4. Open verification modal for unverified property
    const verifyButton = page.getByRole('button', { name: /Verify Domain/i }).first();
    await verifyButton.click();

    // 5. Assert verification modal opened and displays challenge token instructions
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('site_tok_tech_789').first()).toBeVisible();

    // 6. Test tab switching to HTML META tag
    const metaTab = dialog.getByRole('button', { name: /HTML Meta Tag|HTML Meta/i });
    if (await metaTab.count() > 0) {
      await metaTab.click();
      await expect(dialog.getByText('rivinity-site-verification').first()).toBeVisible();
    }

    // 7. Close verification modal
    const closeBtn = dialog.getByRole('button', { name: /Close dialog|Close/i }).first();
    await closeBtn.click();
    await expect(dialog).not.toBeVisible();

    // 8. Assert quick actions / guidance cards exist
    await expect(page.getByRole('link', { name: /Integration Guide/i })).toBeVisible();
  });
});


// ── 3. Admin Operations Journey ───────────────────────────────────────────────

test.describe('Phase 9 — Journey 3: Admin Operational Health & Governance Controls', () => {
  test('Admin dashboard renders infrastructure health, exception queues, and navigation links', async ({ page }) => {
    await setupBaseRoutes(page, 'admin');

    await page.route('**/api/v1/advertiser', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 1, name: 'Alpha Retailer', balance: 500.0 }]),
      });
    });

    await page.route('**/api/v1/campaign', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 1, name: 'Brand Promo', status: 1, impressions: 12000, spent_total: 450.0 }]),
      });
    });

    await page.route('**/api/v1/publisher', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 1, name: 'Metro News', email: 'editor@metronews.com' }]),
      });
    });

    await page.route('**/api/v1/admin/dashboard/operational-exceptions', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_exceptions_count: 3,
          moderation_queue: [
            {
              id: 501,
              title: 'Gambling Ad Creative',
              sla_status: 'AT_RISK',
              time_remaining_minutes: 45,
              owner: 'Trust & Safety',
            },
          ],
          finance_queue: [
            {
              id: 601,
              title: 'Payout Discrepancy #601',
              sla_status: 'ON_TRACK',
              time_remaining_minutes: 180,
              owner: 'Finance Ops',
            },
          ],
          spend_spikes: [],
          unusual_ctrs: [],
          webhook_payout_failures: [],
          stale_integrations: [
            {
              title: 'Stale Integration Slot',
              description: 'Slot #42 has had 0 impressions for > 7 days.',
              severity: 'medium',
              runbook_url: 'docs/runbooks/05_provider_outage.md',
            },
          ],
          no_fill_slots: [],
        }),
      });
    });

    // 1. Navigate to Admin Dashboard
    await page.goto('/admin');

    // 2. Assert page header & infrastructure status
    await expect(page.locator('h1')).toContainText('Platform Operations');
    await expect(page.locator('text=PostgreSQL Database')).toBeVisible();
    await expect(page.locator('text=Connected').first()).toBeVisible();

    // 3. Assert active warnings with runbook URL
    await expect(page.locator('text=Stale Integration Slot')).toBeVisible();
    await expect(page.locator('text=docs/runbooks/05_provider_outage.md')).toBeVisible();

    // 4. Assert SLA exception queues rendered with ownership
    await expect(page.locator('text=Creative Moderation Queue (1)')).toBeVisible();
    await expect(page.locator('text=Trust & Safety')).toBeVisible();
    await expect(page.locator('text=Payout Discrepancy #601')).toBeVisible();
    await expect(page.locator('text=Finance Ops')).toBeVisible();
  });

  test('Creative moderation page enforces categorized rejection with mandatory reason', async ({ page }) => {
    await setupBaseRoutes(page, 'admin');

    await page.route('**/api/v1/moderation/creatives/pending', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 88,
            campaign_id: 1,
            advertiser_id: 1,
            name: 'Deceptive Crypto Creative',
            title: 'Deceptive Crypto Creative',
            creative_type: 1,
            width: 300,
            height: 250,
            moderation_status: 1, // Pending
            landing_url: 'https://free-crypto-giveaway.xyz',
            image_url: 'https://images.example.com/crypto.png',
            description: 'Double your funds overnight!',
            created_at: '2026-11-20T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/campaign', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });
    await page.route('**/api/v1/advertiser', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    // 1. Navigate to Creative Moderation
    await page.goto('/admin/creatives');
    await expect(page.locator('h1')).toContainText('Creative Moderation');
    await expect(page.locator('text=Deceptive Crypto Creative')).toBeVisible();

    // 2. Click Reject button
    const rejectBtn = page.getByRole('button', { name: /Reject/i }).first();
    await rejectBtn.click();

    // 3. Assert Rejection Modal is displayed with Category and Reason
    await expect(page.locator('#rejection-category')).toBeVisible();
    await expect(page.locator('#rejection-reason')).toBeVisible();

    // 4. Confirm rejection button is initially disabled without reason text
    const confirmRejectBtn = page.getByRole('button', { name: /Confirm Rejection/i });
    await expect(confirmRejectBtn).toBeDisabled();

    // 5. Fill reason text and select category
    await page.selectOption('#rejection-category', 'MALICIOUS_DESTINATION');
    await page.fill('#rejection-reason', 'Phishing destination pattern and unsubstantiated financial promise.');
    await expect(confirmRejectBtn).toBeEnabled();
  });

  test('Finance reconciliation renders permanent unresolved discrepancies and estimated vs finalised status', async ({ page }) => {
    await setupBaseRoutes(page, 'admin');

    await page.route('**/api/v1/advertiser', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 1, name: 'Acme Corp', balance: 1000.0 }]),
      });
    });
    await page.route('**/api/v1/publisher', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route('**/api/v1/finance/advertiser/1/ledger**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          entity_id: 1,
          entity_type: 'advertiser',
          wallet_balance: 1000.0,
          ledger_balance: 950.0,
          drift: 50.0,
          transactions: [
            {
              id: 901,
              type: 1, // DEPOSIT
              amount: 1000.0,
              is_cleared: true,
              reference_id: 'PAY-ORD-901',
              description: 'Initial Razorpay wallet top-up',
              created_at: '2026-11-01T10:00:00Z',
            },
            {
              id: 902,
              type: 2, // SPEND
              amount: -50.0,
              is_cleared: false, // In hold window
              reference_id: 'CLK-8821',
              description: 'Pending click batch reconciliation',
              created_at: '2026-11-20T15:30:00Z',
            },
          ],
        }),
      });
    });

    await page.route('**/api/v1/finance/reconcile', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'COMPLETED',
          start_date: '2026-11-01',
          end_date: '2026-11-20',
          total_event_spend: 50.0,
          total_ledger_spend: 50.0,
          spend_delta: 0.0,
          total_ivt_events: 12,
          unresolved_queue: [
            {
              queue_id: 'DISC-ACME-50',
              severity: 'CRITICAL',
              name: 'Acme Corp',
              metric: 'WALLET_DRIFT',
              expected_value: 950.0,
              actual_value: 1000.0,
              delta: 50.0,
              reason: 'Wallet balance exceeds ledger sum by $50.00.',
            },
          ],
        }),
      });
    });

    // 1. Navigate to Finance Reconciliation
    await page.goto('/admin/finance');
    await expect(page.locator('h1')).toContainText('Financial & Payment Reconciliation');

    // 2. Run reconciliation audit
    const runAuditBtn = page.getByRole('button', { name: /Audit System Telemetry vs Ledger/i });
    await runAuditBtn.click();

    // 3. Assert Unresolved Queue is visible with anti-autobalancing notice
    await expect(page.locator('text=Unresolved Discrepancies Queue (1)')).toBeVisible();
    await expect(page.locator('text=The platform never silently auto-balances ledgers')).toBeVisible();
    await expect(page.locator('text=DISC-ACME-50')).toBeVisible();

    // 4. Assert Settlement Status distinctions (Estimated vs Finalised)
    await expect(page.locator('text=Finalised / Settled')).toBeVisible();
    await expect(page.locator('text=Estimated (Hold Window)')).toBeVisible();
  });
});


// ── 4. Browser Accessibility Smoke Checks ─────────────────────────────────────

test.describe('Phase 9 — Journey 4: Browser Accessibility & Semantic Invariants', () => {
  test('Accessibility smoke check on Advertiser Campaign Detail', async ({ page }) => {
    await setupBaseRoutes(page, 'advertiser');

    const campaignId = 101;
    await page.route(`**/api/v1/campaign/${campaignId}`, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: campaignId,
          advertiser_id: 10,
          name: 'Accessibility Test Campaign',
          status: 1,
          budget: 500,
          daily_budget: 50,
          spent_total: 20,
          spent_today: 5,
          budget_daily: 50,
          impressions: 100,
          clicks: 10,
          conversions: 1,
          autopilot: false,
          start_date: '2026-06-01',
          end_date: '2026-06-30',
          created_at: '2026-05-15T10:00:00Z',
        }),
      });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/ad-sets`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route(`**/api/v1/creative?**`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route('**/api/v1/advertiser/reporting**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          advertiser_id: 10,
          start_date: '2026-05-15',
          end_date: '2026-05-22',
          impressions: 100,
          clicks: 10,
          conversions: 1,
          spend: 20,
          ctr: 0.1,
          daily_breakdown: [],
        }),
      });
    });

    await page.route('**/api/v1/advertiser/domains', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/suggestions`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route(`**/api/v1/campaign/${campaignId}/optimization-state`, async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(null) });
    });

    await page.goto(`/advertiser/campaigns/${campaignId}`);

    // 1. Heading hierarchy: single h1 present
    const h1Count = await page.locator('h1').count();
    expect(h1Count).toBe(1);

    // 2. All interactive buttons have non-empty text or aria-label
    const buttons = page.locator('button');
    const buttonCount = await buttons.count();
    for (let i = 0; i < buttonCount; i++) {
      const btn = buttons.nth(i);
      if (await btn.isVisible()) {
        const text = await btn.textContent();
        const ariaLabel = await btn.getAttribute('aria-label');
        const title = await btn.getAttribute('title');
        const accessibleName = (text?.trim() || ariaLabel?.trim() || title?.trim() || '');
        expect(accessibleName.length).toBeGreaterThan(0);
      }
    }
  });

  test('Accessibility smoke check on Publisher Properties modal', async ({ page }) => {
    await setupBaseRoutes(page, 'publisher');

    await page.route('**/api/v1/publisher/20/property**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 1,
            publisher_id: 20,
            name: 'A11y Test Domain',
            domain: 'a11y.example.com',
            status: 1,
            verification_status: 'UNVERIFIED',
            verification_token: 'token_123',
            dns_txt_record: 'liteads-site-verification=token_123',
            html_meta_tag: '<meta name="liteads-site-verification" content="token_123">',
            created_at: '2026-10-01T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/slot**', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.goto('/publisher/properties');

    // Open modal
    await page.getByRole('button', { name: /Verify Domain/i }).first().click();

    // Verify dialog accessibility semantics
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    // Dialog must have accessible close button
    const closeBtn = dialog.getByRole('button', { name: /Close dialog|Close/i }).first();
    await expect(closeBtn).toBeVisible();
    await expect(closeBtn).toBeEnabled();
  });
});
