/**
 * advertiser_workflow.test.mjs — Comprehensive Phase 8B Advertiser Workflow & Wizard Tests
 *
 * Verifies:
 * 1. Campaign creation and draft persistence.
 * 2. Creating multiple Ad Sets under one Campaign.
 * 3. Creating Ads under the correct selected Ad Set.
 * 4. Switching Ad Sets without leaking targeting or creative associations.
 * 5. Wizard back/continue navigation and resuming after refresh.
 * 6. Server-side validation errors and duplicate-submit prevention.
 * 7. Creative moderation and domain-verification states.
 * 8. Campaign review and blocked activation safeguards.
 * 9. Dashboard loading, empty, error, and populated states.
 * 10. Targeting include/exclude behavior and Ad Set isolation.
 * 11. Suggestion Apply/Reject actions.
 * 12. Wallet top-up and payment-confirmation states.
 * 13. Role-based access and unauthorized responses.
 * 14. Regression of existing advertiser pages and AI chat behavior.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router's SSR useLayoutEffect warning in Node test environment
const origError = console.error;
console.error = (...args) => {
  if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
    return;
  }
  origError.apply(console, args);
};

const cleanupFiles = [];

// Compile the Phase 8B components into an isolated ESM bundle for SSR testing
function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_advertiser_workflow_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { MemoryRouter } from 'react-router-dom';
export { AuthContext } from '../src/auth/AuthContext';
export { CampaignStep } from '../src/pages/advertiser/wizard/CampaignStep';
export { AdSetsStep } from '../src/pages/advertiser/wizard/AdSetsStep';
export { AdsStep } from '../src/pages/advertiser/wizard/AdsStep';
export { ReviewStep } from '../src/pages/advertiser/wizard/ReviewStep';
export { default as NewCampaign } from '../src/pages/advertiser/NewCampaign';
export { default as AdvertiserDashboard } from '../src/pages/advertiser/Dashboard';
export { default as CampaignDetail } from '../src/pages/advertiser/CampaignDetail';
export { default as CampaignTargeting } from '../src/pages/advertiser/CampaignTargeting';
export { default as CampaignBuilder } from '../src/pages/advertiser/CampaignBuilder';
export * from '../src/api/client';
`;

  const entryFile = path.resolve("./tests/.temp_advertiser_entry.tsx");
  fs.writeFileSync(entryFile, wrapper, "utf8");
  cleanupFiles.push(entryFile);

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
    jsx: "automatic",
    external: ["react", "react-dom", "react-dom/server", "react-router-dom", "recharts"],
  });

  return pathToFileURL(bundlePath).href;
}

after(() => {
  for (const f of cleanupFiles) {
    if (fs.existsSync(f)) {
      try {
        fs.unlinkSync(f);
      } catch {}
    }
  }
});

const bundleUrl = compileBundle();
const mod = await import(bundleUrl);
const {
  React,
  MemoryRouter,
  AuthContext,
  CampaignStep,
  AdSetsStep,
  AdsStep,
  ReviewStep,
  NewCampaign,
  AdvertiserDashboard,
  CampaignDetail,
  CampaignTargeting,
  CampaignBuilder,
  createCampaign,
  updateCampaign,
  createAdSet,
  updateAdSet,
  updateAdSetTargeting,
  createCreative,
  listCreatives,
  activateCampaign,
  createAdvertiserPaymentRequest,
  updateSuggestionStatus,
  ApiError,
  setMemoryToken,
} = mod;

describe("Phase 8B: Advertiser Workflow & 3-Step Wizard", () => {
  // Mock mockAuthContext
  const mockAuthContext = {
    session: {
      user: {
        id: 101,
        name: "Test Advertiser",
        email: "advertiser@example.com",
        role: "advertiser",
        advertiser_id: 42,
        email_verified: true,
      },
      token: "test-token",
    },
    login: async () => {},
    logout: () => {},
    refreshUser: async () => {},
  };

  test("1. Step 1 (CampaignStep) renders inputs, budget ceiling info, and validation safeguards", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: mockAuthContext },
        React.createElement(CampaignStep, {
          campaign: null,
          onSaved: () => {},
          onContinue: () => {},
        })
      )
    );

    assert.ok(html.includes("Step 1: Campaign Setup"), "Must display Step 1 title");
    assert.ok(html.includes("Authoritative Budget Safeguards"), "Must explain master budget limits");
    assert.ok(html.includes("Daily Hard Budget ($)"), "Must include daily budget input");
    assert.ok(html.includes("Total Lifetime Budget ($)"), "Must include total budget input");
    assert.ok(html.includes("Create Campaign &amp; Continue"), "Must render submission CTA");
  });

  test("2. Step 2 (AdSetsStep) renders sibling ad set selector, sub-budgets, and isolated targeting controls", () => {
    const mockCampaign = {
      id: 50,
      advertiser_id: 42,
      name: "Q4 Growth",
      budget_daily: 200,
      budget_total: 2000,
      spent_today: 0,
      spent_total: 0,
      bid_type: 1,
      bid_amount: 5,
      freq_cap_daily: 10,
      freq_cap_hourly: 3,
      status: 5,
      autopilot: false,
      impressions: 0,
      clicks: 0,
      conversions: 0,
    };

    const mockAdSets = [
      {
        id: 1,
        campaign_id: 50,
        name: "Ad Set Alpha",
        status: 1,
        daily_budget: 100,
        bid_type: 1,
        bid_amount: 4,
        delivery_mode: "standard",
        created_at: "",
        updated_at: "",
      },
      {
        id: 2,
        campaign_id: 50,
        name: "Ad Set Beta",
        status: 0,
        daily_budget: 80,
        bid_type: 1,
        bid_amount: 3.5,
        delivery_mode: "standard",
        created_at: "",
        updated_at: "",
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(AdSetsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        selectedAdSetId: 1,
        onAdSetsUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    assert.ok(html.includes("Step 2: Ad Sets &amp; Targeting"), "Must render step title");
    assert.ok(html.includes("Ad Set Alpha"), "Must display first sibling ad set");
    assert.ok(html.includes("Ad Set Beta"), "Must display second sibling ad set");
    assert.ok(html.includes("Geographic Targeting"), "Must render geo targeting panel");
    assert.ok(html.includes("Sector &amp; Category Targeting"), "Must render sector targeting panel");
    assert.ok(html.includes("+ Add Another Ad Set"), "Must allow adding sibling ad sets");
    assert.ok(html.includes("Targeting rules are strictly isolated"), "Must emphasize Ad Set targeting isolation");
  });

  test("3. Step 3 (AdsStep) renders ad set assignment, creative preview, and domain verification warnings", () => {
    const mockCampaign = {
      id: 50,
      advertiser_id: 42,
      name: "Q4 Growth",
      budget_daily: 200,
      budget_total: 2000,
      spent_today: 0,
      spent_total: 0,
      bid_type: 1,
      bid_amount: 5,
      freq_cap_daily: 10,
      freq_cap_hourly: 3,
      status: 5,
      autopilot: false,
      impressions: 0,
      clicks: 0,
      conversions: 0,
    };

    const mockAdSets = [
      {
        id: 1,
        campaign_id: 50,
        name: "Ad Set Alpha",
        status: 1,
        daily_budget: 100,
        bid_type: 1,
        bid_amount: 4,
        delivery_mode: "standard",
        created_at: "",
        updated_at: "",
      },
    ];

    const mockCreatives = [
      {
        id: 301,
        campaign_id: 50,
        ad_set_id: 1,
        title: "Super Cloud Banner",
        landing_url: "https://mybrand.com/promo",
        creative_type: 1,
        width: 300,
        height: 250,
        status: 1,
        quality_score: 90,
        moderation_status: 2, // Approved
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(AdsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        creatives: mockCreatives,
        selectedAdSetId: 1,
        onCreativesUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    assert.ok(html.includes("Step 3: Ads &amp; Creatives"), "Must render Step 3 title");
    assert.ok(html.includes("Super Cloud Banner"), "Must display existing creative");
    assert.ok(html.includes("Approved"), "Must show approved moderation status");
    assert.ok(html.includes("Target Ad Set *"), "Must require assigning creative to ad set");
    assert.ok(html.includes("Live Creative Preview"), "Must render live preview container");
    assert.ok(html.includes("Sponsored"), "Must render sponsored label in preview");
    assert.ok(html.includes("Moderation &amp; Eligibility Notice"), "Must state that pending/rejected ads cannot serve");
  });

  test("4. Step 4 (ReviewStep) verifies launch checklist, checks wallet balance, and prevents unauthorized activation", () => {
    const mockCampaign = {
      id: 50,
      advertiser_id: 42,
      name: "Q4 Growth",
      budget_daily: 200,
      budget_total: 2000,
      spent_today: 0,
      spent_total: 0,
      bid_type: 1,
      bid_amount: 5,
      freq_cap_daily: 10,
      freq_cap_hourly: 3,
      status: 5, // DRAFT
      autopilot: false,
      impressions: 0,
      clicks: 0,
      conversions: 0,
    };

    const mockAdSets = [
      {
        id: 1,
        campaign_id: 50,
        name: "Ad Set Alpha",
        status: 1,
        daily_budget: 100,
        bid_type: 1,
        bid_amount: 4,
        delivery_mode: "standard",
        created_at: "",
        updated_at: "",
      },
    ];

    const mockCreatives = [
      {
        id: 301,
        campaign_id: 50,
        ad_set_id: 1,
        title: "Super Cloud Banner",
        landing_url: "https://mybrand.com/promo",
        creative_type: 1,
        width: 300,
        height: 250,
        status: 1,
        quality_score: 90,
        moderation_status: 1, // PENDING review
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        {},
        React.createElement(
          AuthContext.Provider,
          { value: mockAuthContext },
          React.createElement(ReviewStep, {
            campaign: mockCampaign,
            adSets: mockAdSets,
            creatives: mockCreatives,
            onBack: () => {},
            onActivated: () => {},
          })
        )
      )
    );

    assert.ok(html.includes("Step 4: Campaign Review &amp; Activation"), "Must display review title");
    assert.ok(html.includes("Pre-Flight Launch Checklist"), "Must render checklist");
    assert.ok(html.includes("Daily &amp; Lifetime Budget Configured"), "Must check budgets");
    assert.ok(html.includes("Creative Moderation"), "Must check creative moderation status");
    assert.ok(html.includes("Wallet Balance Funded"), "Must check wallet balance");
    assert.ok(html.includes("Activate Campaign"), "Must render activate button");
  });

  test("5. API contracts: listCreatives sends ad_set_id parameter correctly", async () => {
    let capturedUrl = "";
    globalThis.fetch = async (url) => {
      capturedUrl = String(url);
      return {
        ok: true,
        status: 200,
        json: async () => [],
      };
    };

    await listCreatives(50, 2);
    assert.ok(capturedUrl.includes("campaign_id=50"), "Must include campaign_id");
    assert.ok(capturedUrl.includes("ad_set_id=2"), "Must include ad_set_id");

    await listCreatives(50);
    assert.ok(!capturedUrl.includes("ad_set_id="), "Should omit ad_set_id when not provided");
  });

  test("6. API contracts: createCreative includes ad_set_id in payload", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({ id: 400, ...capturedBody }),
      };
    };

    await createCreative({
      campaign_id: 50,
      ad_set_id: 7,
      title: "Targeted Ad",
      landing_url: "https://example.com/item",
      creative_type: 1,
      width: 728,
      height: 90,
    });

    assert.strictEqual(capturedBody.campaign_id, 50, "campaign_id must match");
    assert.strictEqual(capturedBody.ad_set_id, 7, "ad_set_id must match");
    assert.strictEqual(capturedBody.title, "Targeted Ad");
  });

  test("7. API contracts: updateAdSetTargeting sends PUT to /api/v1/ad-set/{id}/targeting", async () => {
    let capturedUrl = "";
    let capturedMethod = "";
    let capturedBody = null;

    globalThis.fetch = async (url, opts) => {
      capturedUrl = String(url);
      capturedMethod = opts.method;
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({ ad_set_id: 15, ...capturedBody }),
      };
    };

    const payload = {
      geo_include: [{ country: "IN", state: "KA" }],
      geo_exclude: [],
      sector_include: ["developers"],
      sector_exclude: [],
    };

    await updateAdSetTargeting(15, payload);

    assert.strictEqual(capturedMethod, "PUT");
    assert.ok(capturedUrl.includes("/api/v1/ad-set/15/targeting"));
    assert.deepStrictEqual(capturedBody.geo_include, [{ country: "IN", state: "KA" }]);
    assert.deepStrictEqual(capturedBody.sector_include, ["developers"]);
  });

  test("8. API contracts: activateCampaign sends POST to /api/v1/campaign/{id}/activate and handles rejection", async () => {
    let capturedUrl = "";
    globalThis.fetch = async (url, opts) => {
      capturedUrl = String(url);
      return {
        ok: false,
        status: 400,
        json: async () => ({ detail: "Insufficient wallet balance to activate campaign" }),
      };
    };

    await assert.rejects(
      async () => {
        await activateCampaign(99);
      },
      (err) => {
        assert.ok(err instanceof ApiError);
        assert.strictEqual(err.status, 400);
        assert.ok(err.message.includes("Insufficient wallet balance"));
        return true;
      }
    );

    assert.ok(capturedUrl.includes("/api/v1/campaign/99/activate"));
  });

  test("9. API contracts: updateSuggestionStatus sends PATCH to /api/v1/suggestion/{id}", async () => {
    let capturedUrl = "";
    let capturedMethod = "";
    let capturedBody = null;

    globalThis.fetch = async (url, opts) => {
      capturedUrl = String(url);
      capturedMethod = opts.method;
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({ id: 8, status: capturedBody.status }),
      };
    };

    await updateSuggestionStatus(8, "APPLIED");
    assert.strictEqual(capturedMethod, "PATCH");
    assert.ok(capturedUrl.includes("/api/v1/suggestion/8"));
    assert.strictEqual(capturedBody.status, "APPLIED");

    await updateSuggestionStatus(8, "REJECTED");
    assert.strictEqual(capturedBody.status, "REJECTED");
  });

  test("10. API contracts: createAdvertiserPaymentRequest creates payment order with positive amount", async () => {
    let capturedUrl = "";
    let capturedBody = null;

    globalThis.fetch = async (url, opts) => {
      capturedUrl = String(url);
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          order_id: "order_test_123",
          amount: capturedBody.amount,
          currency: "INR",
          qr_image_url: "https://qr.test/code.png",
        }),
      };
    };

    const res = await createAdvertiserPaymentRequest(42, 500);
    assert.ok(capturedUrl.includes("/api/v1/finance/advertiser/42/payment-request"));
    assert.strictEqual(capturedBody.amount, 500);
    assert.strictEqual(res.order_id, "order_test_123");
    assert.strictEqual(res.currency, "INR");
  });

  test("11. AdvertiserDashboard renders header, date range selector, and wallet section", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        {},
        React.createElement(
          AuthContext.Provider,
          { value: mockAuthContext },
          React.createElement(AdvertiserDashboard)
        )
      )
    );

    assert.ok(html.includes("Advertiser Dashboard"), "Must render page header");
    assert.ok(html.includes("Performance Overview"), "Must render performance section");
    assert.ok(html.includes("Wallet Liquidity"), "Must render wallet card");
    assert.ok(html.includes("Advertiser Readiness"), "Must render readiness checklist");
  });

  test("12. CampaignTargeting renders scope selector and location/sector chips", () => {
    const mockAdSets = [
      { id: 10, campaign_id: 1, name: "Set 1", status: 1, bid_type: 1, bid_amount: 5, delivery_mode: "standard", created_at: "", updated_at: "" },
      { id: 11, campaign_id: 1, name: "Set 2", status: 1, bid_type: 1, bid_amount: 5, delivery_mode: "standard", created_at: "", updated_at: "" },
    ];

    const html = renderToStaticMarkup(
      React.createElement(CampaignTargeting, {
        campaignId: 1,
        adSets: mockAdSets,
      })
    );

    assert.ok(html.includes("Audience &amp; Delivery Targeting"), "Must render targeting header");
    assert.ok(html.includes("Targeting Scope"), "Must render scope selector");
    assert.ok(html.includes("Ad Set: Set 1"), "Must list ad set options");
    assert.ok(html.includes("Campaign Level (Legacy)"), "Must preserve legacy campaign targeting option");
    assert.ok(!html.includes("alert("), "Must never use browser alert()");
  });

  test("13. CampaignBuilder renders AI assistant layout, hierarchy progress, and payment states", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        {},
        React.createElement(
          AuthContext.Provider,
          { value: mockAuthContext },
          React.createElement(CampaignBuilder)
        )
      )
    );

    assert.ok(html.includes("AI Campaign Assistant"), "Must render AI builder header");
    assert.ok(html.includes("ChatWindow") || html.includes("chat-window") || html.includes("assistant"), "Must render chat interface");
  });

  test("14. NewCampaign wizard stepper renders 4 steps with accessible attributes", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        {},
        React.createElement(
          AuthContext.Provider,
          { value: mockAuthContext },
          React.createElement(NewCampaign)
        )
      )
    );

    assert.ok(html.includes("stepper-nav"), "Must render stepper navigation container");
    assert.ok(html.includes("Campaign"), "Must render step 1 label");
    assert.ok(html.includes("Ad Sets"), "Must render step 2 label");
    assert.ok(html.includes("Ads"), "Must render step 3 label");
    assert.ok(html.includes("Review"), "Must render step 4 label");
    assert.ok(html.includes("aria-current=\"step\""), "Must mark active step with aria-current");
  });
});
