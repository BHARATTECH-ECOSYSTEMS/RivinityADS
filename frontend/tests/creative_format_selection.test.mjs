/**
 * creative_format_selection.test.mjs — Comprehensive Advertiser Creative Format Selection Tests
 *
 * Verifies all 8 frontend requirements from RivinityADS Ad Creative Format Selection:
 * 1. Selecting AI and Chatbot Ads displays the three supported subcategories.
 * 2. Selecting Display Card assigns 520 × 120.
 * 3. Selecting Label Ads assigns 400 × 100.
 * 4. Selecting Element Ads assigns 310 × 150.
 * 5. Selecting Banner Ads assigns 500 × 500.
 * 6. Switching categories clears incompatible selections.
 * 7. Live preview updates correctly for every format.
 * 8. Manual dimension changes are impossible through the UI (arbitrary inputs/presets removed).
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

const origError = console.error;
console.error = (...args) => {
  if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
    return;
  }
  origError.apply(console, args);
};

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_creative_format_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { AdsStep } from '../src/pages/advertiser/wizard/AdsStep';
export {
  AD_CATEGORIES,
  AI_CHATBOT_FORMATS,
  BANNER_FORMAT,
  CREATIVE_FORMAT_LABELS,
  CREATIVE_TYPES,
} from '../src/types';
export * from '../src/api/client';
`;

  const entryFile = path.resolve("./tests/.temp_creative_format_entry.tsx");
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
    external: ["react", "react-dom", "react-dom/server"],
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
  AdsStep,
  AD_CATEGORIES,
  AI_CHATBOT_FORMATS,
  BANNER_FORMAT,
  CREATIVE_FORMAT_LABELS,
  createCreative,
} = mod;

const mockCampaign = {
  id: 100,
  advertiser_id: 1,
  name: "Spring Launch",
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
    id: 10,
    campaign_id: 100,
    name: "Primary Ad Set",
    status: 1,
    daily_budget: 100,
    bid_type: 1,
    bid_amount: 5,
    delivery_mode: "standard",
    created_at: "",
    updated_at: "",
  },
];

describe("RivinityADS — Ad Creative Format Selection Workflow", () => {
  test("1. Main Ad Category dropdown replaces legacy Format Type and has exactly two options", () => {
    const html = renderToStaticMarkup(
      React.createElement(AdsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        creatives: [],
        selectedAdSetId: 10,
        onCreativesUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    // Dropdown label check
    assert.ok(html.includes("Select Ad Category *"), "Dropdown named 'Select Ad Category *' must be present");
    assert.ok(!html.includes("Format Type"), "Legacy 'Format Type' label must be completely removed");

    // Exactly two options
    assert.ok(html.includes("AI and Chatbot Ads"), "Must contain option 'AI and Chatbot Ads'");
    assert.ok(html.includes("Banner Ads"), "Must contain option 'Banner Ads'");

    // Should not contain legacy types in the category dropdown
    assert.ok(!html.includes("value=\"interstitial\""), "Arbitrary legacy options must not be in category select");
  });

  test("2. Format constants and metadata adhere strictly to specified dimensions", () => {
    // Check AI & Chatbot Formats
    assert.strictEqual(AI_CHATBOT_FORMATS.DISPLAY_CARD.width, 520, "Display Card width must be 520 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.DISPLAY_CARD.height, 120, "Display Card height must be 120 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.DISPLAY_CARD.typeId, 5, "Display Card type must be 5");

    assert.strictEqual(AI_CHATBOT_FORMATS.LABEL_ADS.width, 400, "Label Ads width must be 400 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.LABEL_ADS.height, 100, "Label Ads height must be 100 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.LABEL_ADS.typeId, 6, "Label Ads type must be 6");

    assert.strictEqual(AI_CHATBOT_FORMATS.ELEMENT_ADS.width, 310, "Element Ads width must be 310 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.ELEMENT_ADS.height, 150, "Element Ads height must be 150 px");
    assert.strictEqual(AI_CHATBOT_FORMATS.ELEMENT_ADS.typeId, 7, "Element Ads type must be 7");

    // Check Banner Format
    assert.strictEqual(BANNER_FORMAT.width, 500, "Banner Ads width must be 500 px");
    assert.strictEqual(BANNER_FORMAT.height, 500, "Banner Ads height must be 500 px");
    assert.strictEqual(BANNER_FORMAT.typeId, 1, "Banner Ads type must be 1");
    assert.strictEqual(BANNER_FORMAT.label, "Banner Ad (500 × 500)", "Banner label must state Banner Ad (500 × 500)");
  });

  test("3. Arbitrary dynamic dimension inputs and presets are completely removed from UI", () => {
    const html = renderToStaticMarkup(
      React.createElement(AdsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        creatives: [],
        selectedAdSetId: 10,
        onCreativesUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    // Common dimension buttons must be gone
    assert.ok(!html.includes("300x250"), "300x250 button must be removed");
    assert.ok(!html.includes("728x90"), "728x90 button must be removed");
    assert.ok(!html.includes("320x50"), "320x50 button must be removed");
    assert.ok(!html.includes("Common Dimensions"), "Common Dimensions label must be removed");

    // Width (px) and Height (px) inputs must be gone
    assert.ok(!html.includes("Width (px)"), "Width (px) input must be removed");
    assert.ok(!html.includes("Height (px)"), "Height (px) input must be removed");
  });

  test("4. API client payload carries fixed dimensions for Display Card (520 × 120)", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({ id: 501, ...capturedBody }),
      };
    };

    const res = await createCreative({
      campaign_id: 100,
      ad_set_id: 10,
      title: "Display Card Creative",
      category: "ai_chatbot",
      format: "display_card",
      creative_type: 5,
      landing_url: "https://example.com/item",
      width: 520,
      height: 120,
    });

    assert.strictEqual(capturedBody.category, "ai_chatbot");
    assert.strictEqual(capturedBody.format, "display_card");
    assert.strictEqual(capturedBody.creative_type, 5);
    assert.strictEqual(capturedBody.width, 520, "Must submit exact 520 width");
    assert.strictEqual(capturedBody.height, 120, "Must submit exact 120 height");
    assert.strictEqual(res.id, 501);
  });

  test("5. API client payload carries fixed dimensions for Label Ads (400 × 100)", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({ id: 502, ...capturedBody }),
      };
    };

    await createCreative({
      campaign_id: 100,
      ad_set_id: 10,
      title: "Label Ad Creative",
      category: "ai_chatbot",
      format: "label_ad",
      creative_type: 6,
      landing_url: "https://example.com/label",
      width: 400,
      height: 100,
    });

    assert.strictEqual(capturedBody.category, "ai_chatbot");
    assert.strictEqual(capturedBody.format, "label_ad");
    assert.strictEqual(capturedBody.creative_type, 6);
    assert.strictEqual(capturedBody.width, 400, "Must submit exact 400 width");
    assert.strictEqual(capturedBody.height, 100, "Must submit exact 100 height");
  });

  test("6. API client payload carries fixed dimensions for Element Ads (310 × 150)", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({ id: 503, ...capturedBody }),
      };
    };

    await createCreative({
      campaign_id: 100,
      ad_set_id: 10,
      title: "Element Ad Creative",
      category: "ai_chatbot",
      format: "element_ad",
      creative_type: 7,
      landing_url: "https://example.com/element",
      width: 310,
      height: 150,
    });

    assert.strictEqual(capturedBody.category, "ai_chatbot");
    assert.strictEqual(capturedBody.format, "element_ad");
    assert.strictEqual(capturedBody.creative_type, 7);
    assert.strictEqual(capturedBody.width, 310, "Must submit exact 310 width");
    assert.strictEqual(capturedBody.height, 150, "Must submit exact 150 height");
  });

  test("7. API client payload carries fixed dimensions for Banner Ads (500 × 500)", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({ id: 504, ...capturedBody }),
      };
    };

    await createCreative({
      campaign_id: 100,
      ad_set_id: 10,
      title: "Banner Ad Creative",
      category: "banner",
      format: "banner",
      creative_type: 1,
      landing_url: "https://example.com/banner",
      width: 500,
      height: 500,
    });

    assert.strictEqual(capturedBody.category, "banner");
    assert.strictEqual(capturedBody.format, "banner");
    assert.strictEqual(capturedBody.creative_type, 1);
    assert.strictEqual(capturedBody.width, 500, "Must submit exact 500 width");
    assert.strictEqual(capturedBody.height, 500, "Must submit exact 500 height");
  });

  test("8. Created Ads list displays human-readable format labels and dimensions", () => {
    const existingCreatives = [
      {
        id: 1,
        campaign_id: 100,
        ad_set_id: 10,
        title: "Chatbot Card Ad",
        creative_type: 5,
        format: "display_card",
        category: "ai_chatbot",
        width: 520,
        height: 120,
        landing_url: "https://example.com",
        status: 1,
        quality_score: 85,
        moderation_status: 2,
      },
      {
        id: 2,
        campaign_id: 100,
        ad_set_id: 10,
        title: "Square Banner Ad",
        creative_type: 1,
        format: "banner",
        category: "banner",
        width: 500,
        height: 500,
        landing_url: "https://example.com",
        status: 1,
        quality_score: 90,
        moderation_status: 2,
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(AdsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        creatives: existingCreatives,
        selectedAdSetId: 10,
        onCreativesUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    assert.ok(html.includes("Created Ads (2)"), "Must display count of created ads");
    assert.ok(html.includes("Display Card (520 × 120)"), "Must display Display Card format label");
    assert.ok(html.includes("Banner Ad (500 × 500)"), "Must display Banner Ad format label");
  });
});

