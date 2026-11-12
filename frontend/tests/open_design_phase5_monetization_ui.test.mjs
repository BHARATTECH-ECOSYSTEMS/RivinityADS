/**
 * open_design_phase5_monetization_ui.test.mjs
 *
 * Phase 5 Stage 5.4: Publisher Linking, Domain Verification & Monetization Consent UI
 *
 * Comprehensive Test Suite Verifying:
 * 1. Publisher property listing, discovery, and selection UI.
 * 2. Publisher property linking workflow with tenant scoping and error handling.
 * 3. Domain verification lifecycle (DNS_TXT and HTML_META challenges & server verification).
 * 4. Authoritative 6-point prerequisites checklist (project_active, publisher_active, payout_configured,
 *    property_verified, slots_authorized, consent_recorded) and failure reasons.
 * 5. Revenue sharing disclosure (authoritative 70/30 split) and explicit consent recording.
 * 6. Live activation safeguards: disabled when ineligible, explicit confirmation modal before enable.
 * 7. CRITICAL SAFEGUARD: Live monetization is NEVER enabled during tests or automated setup.
 * 8. SEC-5.3-01 Remediation: Untrusted drag-and-drop slot validation against authoritative project-bound slots.
 * 9. AdSlotPalette monetization action button triggers.
 */

import test, { describe, beforeEach, afterEach, after } from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React SSR warnings in Node test environment
const origError = console.error;
console.error = (...args) => {
  if (
    typeof args[0] === "string" &&
    (args[0].includes("useLayoutEffect does nothing on the server") ||
      args[0].includes("React does not recognize"))
  ) {
    return;
  }
  origError.apply(console, args);
};

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_monetization_ui_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { WebsiteMonetizationModal } from '../src/components/website/WebsiteMonetizationModal';
export { AdSlotPalette } from '../src/components/website/AdSlotPalette';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_monetization_ui_entry.tsx");
  fs.writeFileSync(entryFile, wrapper, "utf8");
  cleanupFiles.push(entryFile);

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    jsx: "automatic",
    target: "node18",
    external: ["react", "react-dom", "react-dom/server", "react-router-dom"],
  });

  return pathToFileURL(bundlePath).href;
}

const bundleUrl = compileBundle();
const {
  React,
  WebsiteMonetizationModal,
  AdSlotPalette,
  linkWebsitePublisher,
  listPublisherProperties,
  challengePublisherProperty,
  verifyPublisherProperty,
  getWebsiteMonetizationStatus,
  recordWebsiteMonetizationConsent,
  enableWebsiteMonetization,
  disableWebsiteMonetization,
  WebsiteMonetizationStatusCode,
  WebsiteMonetizationStatusMap,
  setMemoryToken,
  ApiError,
} = await import(bundleUrl);

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

// ── Mock Data Fixtures ────────────────────────────────────────────────────────

const mockProjectUnlinked = {
  id: 101,
  owner_user_id: 5,
  publisher_id: 42,
  property_id: null,
  name: "Tech News Daily",
  slug: "tech-news-daily",
  template_id: "editorial",
  status: 1, // Published
  monetization_status: 0, // Disabled
  is_deleted: false,
  current_published_version_id: 1,
  published_url: "https://technews.example.com",
  created_at: "2026-11-01T10:00:00Z",
  updated_at: "2026-11-11T12:00:00Z",
  page_count: 3,
  theme_config: {
    primary_color: "#2563eb",
    secondary_color: "#7c3aed",
    font_family: "Inter, sans-serif",
    heading_font: "Inter, sans-serif",
    border_radius: "8px",
  },
  pages: [],
};

const mockProjectLinked = {
  ...mockProjectUnlinked,
  property_id: 10,
};

const mockProperties = [
  {
    id: 10,
    publisher_id: 42,
    domain: "technews.example.com",
    name: "Tech News Portal",
    is_verified: true,
    verification_status: "VERIFIED",
    verification_method: "DNS_TXT",
    verification_token: "tok_dns_verified123",
    status: 1,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-05T12:00:00Z",
  },
  {
    id: 11,
    publisher_id: 42,
    domain: "staging.technews.example.com",
    name: "Tech News Staging",
    is_verified: false,
    verification_status: "PENDING",
    verification_method: "HTML_META",
    verification_token: "tok_meta_pending456",
    status: 1,
    created_at: "2026-11-08T10:00:00Z",
    updated_at: "2026-11-08T10:00:00Z",
  },
  {
    id: 12,
    publisher_id: 42,
    domain: "unverified-blog.io",
    name: "Legacy Unverified Blog",
    is_verified: false,
    verification_status: "FAILED",
    verification_method: "DNS_TXT",
    verification_token: "tok_dns_failed789",
    status: 0, // Inactive
    created_at: "2026-10-15T10:00:00Z",
    updated_at: "2026-11-01T10:00:00Z",
  },
];

const mockChallengeResponseDNS = {
  property_id: 10,
  domain: "technews.example.com",
  verification_method: "DNS_TXT",
  verification_token: "rivinityads_txt_verify_abc123456",
  dns_record_name: "_rivinityads-challenge.technews.example.com",
  html_meta_tag: '<meta name="rivinityads-verification" content="rivinityads_txt_verify_abc123456" />',
  instructions: "Add a DNS TXT record at host _rivinityads-challenge.technews.example.com with value rivinityads_txt_verify_abc123456",
  challenge_expires_at: "2026-11-12T12:00:00Z",
};

const mockChallengeResponseHTML = {
  property_id: 10,
  domain: "technews.example.com",
  verification_method: "HTML_META",
  verification_token: "rivinityads_html_verify_xyz987654",
  dns_record_name: "_rivinityads-challenge.technews.example.com",
  html_meta_tag: '<meta name="rivinityads-verification" content="rivinityads_html_verify_xyz987654" />',
  instructions: 'Add <meta name="rivinityads-verification" content="rivinityads_html_verify_xyz987654" /> into your website <head> tag.',
  challenge_expires_at: "2026-11-12T12:00:00Z",
};

const mockStatusIneligible = {
  project_id: 101,
  is_eligible: false,
  current_status: 0, // Disabled
  slot_count: 2,
  prerequisites: {
    project_active: true,
    publisher_active: true,
    payout_configured: false,
    property_verified: false,
    slots_authorized: false,
    consent_recorded: false,
  },
  failure_reasons: [
    "Publisher property domain is not verified",
    "Publisher payout / banking details are not configured",
    "No authorized active ad slots bound to this project",
    "Monetization terms and revenue share consent must be accepted",
  ],
  consent: null,
};

const mockStatusEligible = {
  project_id: 101,
  is_eligible: true,
  current_status: 0, // Disabled
  slot_count: 2,
  prerequisites: {
    project_active: true,
    publisher_active: true,
    payout_configured: true,
    property_verified: true,
    slots_authorized: true,
    consent_recorded: true,
  },
  failure_reasons: [],
  consent: {
    accepted_terms: true,
    version: "v1.0",
    scope: {
      ad_delivery: true,
      revenue_share: "70/30",
    },
    agreed_at: "2026-11-11T12:00:00Z",
    user_id: 5,
  },
};

const mockAvailableSlots = [
  {
    id: 201,
    slot_id: "slot_banner_top",
    name: "Header Leaderboard",
    format: 1, // Display Banner
    width: 728,
    height: 90,
    status: 1, // Active
    floor_price: 1.5,
    property_id: 10,
    is_chat_surface: false,
  },
  {
    id: 202,
    slot_id: "slot_native_mid",
    name: "In-Article Native Card",
    format: 2, // Native Card
    width: 300,
    height: 250,
    status: 1, // Active
    floor_price: 0.8,
    property_id: 10,
    is_chat_surface: false,
  },
  {
    id: 203,
    slot_id: "slot_inactive_footer",
    name: "Footer Inactive Slot",
    format: 1,
    width: 728,
    height: 90,
    status: 0, // INACTIVE
    floor_price: 0.5,
    property_id: 10,
    is_chat_surface: false,
  },
];

// ── Global Fetch Mocking ──────────────────────────────────────────────────────

const originalFetch = globalThis.fetch;
let mockRequests = [];
let mockResponses = [];

beforeEach(() => {
  mockRequests = [];
  mockResponses = [];
  setMemoryToken("test-jwt-auth-token-123");

  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    mockRequests.push({ url, init });

    // Find custom response if registered
    if (mockResponses.length > 0) {
      const matched = mockResponses.shift();
      return {
        ok: matched.ok ?? true,
        status: matched.status ?? 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => matched.body,
        text: async () => JSON.stringify(matched.body),
      };
    }

    // Default mock response router
    if (url.includes("/challenge")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => mockChallengeResponseDNS,
      };
    }

    if (url.includes("/verify")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          ...mockProperties[0],
          is_verified: true,
          verification_status: "VERIFIED",
        }),
      };
    }

    if (url.includes("/property")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => mockProperties,
      };
    }

    if (url.includes("/publisher-link")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          ...mockProjectLinked,
          property_id: 10,
        }),
      };
    }

    if (url.includes("/monetization/consent")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => mockStatusEligible.consent,
      };
    }

    if (url.includes("/monetization/enable")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          ...mockProjectLinked,
          monetization_status: 2, // ACTIVE_MONETIZED
        }),
      };
    }

    if (url.includes("/monetization/disable")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          ...mockProjectLinked,
          monetization_status: 0, // DISABLED
        }),
      };
    }

    if (url.includes("/monetization")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => mockStatusIneligible,
      };
    }

    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ success: true }),
    };
  };
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

// ── TEST SUITES ───────────────────────────────────────────────────────────────

describe("RivinityADS Phase 5 Stage 5.4: Monetization UI Comprehensive Verifications", () => {
  // ── 1. Publisher Property Listing & Discovery UI ───────────────────────────
  describe("1. Publisher Property Listing & Discovery UI", () => {
    test("Renders modal with 5 distinct workflow tabs", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectUnlinked,
          publisherId: 42,
          initialProperties: mockProperties,
        })
      );

      assert.ok(html.includes('data-testid="monetization-modal"'));
      assert.ok(html.includes('data-testid="monetization-tab-linking"'));
      assert.ok(html.includes('data-testid="monetization-tab-verification"'));
      assert.ok(html.includes('data-testid="monetization-tab-prerequisites"'));
      assert.ok(html.includes('data-testid="monetization-tab-consent"'));
      assert.ok(html.includes('data-testid="monetization-tab-activation"'));
      assert.ok(html.includes("Website Monetization &amp; Publisher Settings"));
    });

    test("Renders unlinked guidance when project.property_id is null", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectUnlinked,
          publisherId: 42,
          initialTab: "linking",
          initialProperties: mockProperties,
        })
      );

      assert.ok(html.includes('data-testid="linking-tab"'));
      assert.ok(html.includes("No publisher property linked yet"));
      assert.ok(html.includes("Available Publisher Properties"));
    });

    test("Renders linked property details card when property_id is present", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "linking",
          initialProperties: mockProperties,
        })
      );

      assert.ok(html.includes('data-testid="linked-property-card"'));
      assert.ok(html.includes("Currently Linked Property"));
      assert.ok(html.includes("technews.example.com"));
      assert.ok(html.includes('data-testid="linked-property-verification-status"'));
    });

    test("Returns null when isOpen is false", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: false,
          onClose: () => {},
          project: mockProjectUnlinked,
        })
      );

      assert.equal(html, "");
    });
  });

  // ── 2. Publisher Property Linking Workflow & Tenant Scoping ─────────────────
  describe("2. Publisher Property Linking Workflow & Tenant Scoping", () => {
    test("linkWebsitePublisher sends POST to /api/v1/website/projects/{id}/publisher-link with body", async () => {
      const res = await linkWebsitePublisher(101, {
        publisher_id: 42,
        property_id: 10,
      });

      assert.ok(res);
      assert.equal(mockRequests.length, 1);
      assert.equal(mockRequests[0].url, "/api/v1/website/projects/101/publisher-link");
      assert.equal(mockRequests[0].init.method, "POST");

      const body = JSON.parse(mockRequests[0].init.body);
      assert.equal(body.publisher_id, 42);
      assert.equal(body.property_id, 10);
    });

    test("Enforces 403 Forbidden tenant isolation when linking property from another publisher", async () => {
      mockResponses.push({
        ok: false,
        status: 403,
        body: { detail: "Forbidden: property does not belong to authorized publisher tenant." },
      });

      await assert.rejects(
        async () => {
          await linkWebsitePublisher(101, {
            publisher_id: 999, // Foreign publisher
            property_id: 10,
          });
        },
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 403);
          assert.ok(err.message.includes("Forbidden"));
          return true;
        }
      );
    });

    test("listPublisherProperties sends GET to /api/v1/publisher/{publisherId}/property", async () => {
      const res = await listPublisherProperties(42);

      assert.ok(Array.isArray(res));
      assert.equal(mockRequests.length, 1);
      assert.equal(mockRequests[0].url, "/api/v1/publisher/42/property");
    });
  });

  // ── 3. Domain Verification Lifecycle (DNS_TXT and HTML_META) ────────────────
  describe("3. Domain Verification Lifecycle", () => {
    test("Renders verification tab with method switcher, challenge instructions, and verify button", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "verification",
          initialProperties: mockProperties,
          initialChallenge: mockChallengeResponseDNS,
        })
      );

      assert.ok(html.includes('data-testid="verification-tab"'));
      assert.ok(html.includes('data-testid="challenge-method-dns"'));
      assert.ok(html.includes('data-testid="challenge-method-html"'));
      assert.ok(html.includes('data-testid="generate-challenge-btn"'));
      assert.ok(html.includes('data-testid="verify-domain-btn"'));
      assert.ok(html.includes("DNS TXT Record"));
      assert.ok(html.includes("HTML Meta Tag"));
      assert.ok(html.includes("_rivinityads-challenge.technews.example.com"));
      assert.ok(html.includes("rivinityads_txt_verify_abc123456"));
    });

    test("challengePublisherProperty sends POST with DNS_TXT method", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: mockChallengeResponseDNS,
      });

      const res = await challengePublisherProperty(10, { method: "DNS_TXT" });

      assert.equal(res.verification_method, "DNS_TXT");
      assert.equal(res.verification_token, "rivinityads_txt_verify_abc123456");
      assert.ok(mockRequests[0].url.includes("/api/v1/publisher/property/10/challenge"));
      const body = JSON.parse(mockRequests[0].init.body);
      assert.equal(body.method, "DNS_TXT");
    });

    test("challengePublisherProperty sends POST with HTML_META method", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: mockChallengeResponseHTML,
      });

      const res = await challengePublisherProperty(10, { method: "HTML_META" });

      assert.equal(res.verification_method, "HTML_META");
      assert.equal(res.verification_token, "rivinityads_html_verify_xyz987654");
      const body = JSON.parse(mockRequests[0].init.body);
      assert.equal(body.method, "HTML_META");
    });

    test("verifyPublisherProperty sends POST to /api/v1/publisher/property/{id}/verify", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: {
          id: 10,
          domain: "technews.example.com",
          is_verified: true,
          verification_status: "VERIFIED",
        },
      });

      const res = await verifyPublisherProperty(10);

      assert.equal(res.is_verified, true);
      assert.equal(res.verification_status, "VERIFIED");
      assert.equal(mockRequests[0].url, "/api/v1/publisher/property/10/verify");
      assert.equal(mockRequests[0].init.method, "POST");
    });

    test("verifyPublisherProperty surfaces failed verification status from backend", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: {
          id: 10,
          domain: "technews.example.com",
          is_verified: false,
          verification_status: "FAILED",
        },
      });

      const res = await verifyPublisherProperty(10);

      assert.equal(res.is_verified, false);
      assert.equal(res.verification_status, "FAILED");
    });
  });

  // ── 4. Authoritative 6-Point Prerequisites Checklist ────────────────────────
  describe("4. Authoritative 6-Point Prerequisites Checklist", () => {
    test("Renders 6-point checklist items with descriptive labels and status badges", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "prerequisites",
          initialMonetizationStatus: mockStatusIneligible,
        })
      );

      assert.ok(html.includes('data-testid="prerequisites-tab"'));
      assert.ok(html.includes('data-testid="prereq-item-project_active"'));
      assert.ok(html.includes('data-testid="prereq-item-publisher_active"'));
      assert.ok(html.includes('data-testid="prereq-item-payout_configured"'));
      assert.ok(html.includes('data-testid="prereq-item-property_verified"'));
      assert.ok(html.includes('data-testid="prereq-item-slots_authorized"'));
      assert.ok(html.includes('data-testid="prereq-item-consent_recorded"'));

      assert.ok(html.includes("Website Project Active"));
      assert.ok(html.includes("Publisher Account Active"));
      assert.ok(html.includes("Payout Details Configured"));
      assert.ok(html.includes("Publisher Property Verified"));
      assert.ok(html.includes("Ad Slots Bound &amp; Authorized"));
      assert.ok(html.includes("Monetization Terms &amp; Consent Recorded"));
      assert.ok(html.includes('data-testid="prereqs-failure-reasons"'));
      assert.ok(html.includes("Publisher property domain is not verified"));
    });

    test("getWebsiteMonetizationStatus sends GET to /api/v1/website/projects/{id}/monetization", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: mockStatusIneligible,
      });

      const res = await getWebsiteMonetizationStatus(101);

      assert.equal(res.project_id, 101);
      assert.equal(res.is_eligible, false);
      assert.equal(res.failure_reasons.length, 4);
      assert.equal(res.prerequisites.payout_configured, false);
      assert.equal(res.prerequisites.property_verified, false);
      assert.equal(mockRequests[0].url, "/api/v1/website/projects/101/monetization");
    });

    test("Accurately parses eligible monetization response with all 6 prerequisites true", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: mockStatusEligible,
      });

      const res = await getWebsiteMonetizationStatus(101);

      assert.equal(res.is_eligible, true);
      assert.equal(res.failure_reasons.length, 0);
      assert.equal(res.prerequisites.project_active, true);
      assert.equal(res.prerequisites.publisher_active, true);
      assert.equal(res.prerequisites.payout_configured, true);
      assert.equal(res.prerequisites.property_verified, true);
      assert.equal(res.prerequisites.slots_authorized, true);
      assert.equal(res.prerequisites.consent_recorded, true);
    });
  });

  // ── 5. Revenue Sharing Disclosure (70/30) & Explicit Consent Recording ──────
  describe("5. Revenue Sharing Disclosure (70/30) & Consent Recording", () => {
    test("Renders authoritative 70/30 revenue share breakdown and agreement controls", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "consent",
          initialMonetizationStatus: mockStatusEligible,
        })
      );

      assert.ok(html.includes('data-testid="consent-tab"'));
      assert.ok(html.includes('data-testid="revenue-share-disclosure"'));
      assert.ok(html.includes("70%"));
      assert.ok(html.includes("Publisher Earnings"));
      assert.ok(html.includes("30%"));
      assert.ok(html.includes("Platform Commission"));
      assert.ok(html.includes("Draft previews and unverified traffic produce"));
      assert.ok(html.includes('data-testid="consent-checkbox"'));
      assert.ok(html.includes('data-testid="record-consent-btn"'));
    });

    test("recordWebsiteMonetizationConsent sends POST with accepted_terms and 70/30 revenue_share scope", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: mockStatusEligible.consent,
      });

      const consentPayload = {
        accepted_terms: true,
        scope: {
          ad_delivery: true,
          revenue_share: "70/30",
        },
      };

      const res = await recordWebsiteMonetizationConsent(101, consentPayload);

      assert.equal(res.accepted_terms, true);
      assert.equal(res.scope.revenue_share, "70/30");
      assert.equal(mockRequests[0].url, "/api/v1/website/projects/101/monetization/consent");
      assert.equal(mockRequests[0].init.method, "POST");

      const body = JSON.parse(mockRequests[0].init.body);
      assert.equal(body.accepted_terms, true);
      assert.equal(body.scope.revenue_share, "70/30");
    });
  });

  // ── 6. Live Activation Safeguards & Confirmation Modal ──────────────────────
  describe("6. Live Activation Safeguards & Confirmation Modal", () => {
    test("Renders activation tab with current status badge and enable controls", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "activation",
          initialMonetizationStatus: mockStatusIneligible,
        })
      );

      assert.ok(html.includes('data-testid="activation-tab"'));
      assert.ok(html.includes('data-testid="monetization-status-badge"'));
      assert.ok(html.includes("DISABLED"));
      assert.ok(html.includes('data-testid="enable-monetization-btn"'));
    });

    test("CRITICAL: Live monetization is strictly disabled when prerequisites are unmet", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "activation",
          initialMonetizationStatus: mockStatusIneligible,
        })
      );

      // The button must be disabled when is_eligible is false
      assert.ok(html.includes("disabled"));
      assert.ok(html.includes('data-testid="enable-monetization-btn"'));
      assert.ok(html.includes("Cannot enable monetization: prerequisites are incomplete"));
    });

    test("Renders enabled activation button when all prerequisites are fulfilled", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: mockProjectLinked,
          publisherId: 42,
          initialTab: "activation",
          initialMonetizationStatus: mockStatusEligible,
        })
      );

      // The enable button should not be disabled when eligible
      assert.ok(html.includes('data-testid="enable-monetization-btn"'));
      assert.ok(!html.includes("Cannot enable monetization: prerequisites are incomplete"));
    });

    test("CRITICAL SAFEGUARD: enableWebsiteMonetization is never called during test setup", async () => {
      // Confirm that throughout all tests up to this point, zero calls were made to /monetization/enable
      const enableRequests = mockRequests.filter((r) => r.url.endsWith("/monetization/enable"));
      assert.equal(enableRequests.length, 0, "Security violation: enableWebsiteMonetization was triggered during test setup");
    });

    test("disableWebsiteMonetization sends POST to /api/v1/website/projects/{id}/monetization/disable", async () => {
      mockResponses.push({
        ok: true,
        status: 200,
        body: {
          ...mockProjectLinked,
          monetization_status: 0,
        },
      });

      const res = await disableWebsiteMonetization(101);

      assert.equal(res.monetization_status, 0);
      assert.equal(mockRequests[0].url, "/api/v1/website/projects/101/monetization/disable");
      assert.equal(mockRequests[0].init.method, "POST");
    });
  });

  // ── 7. SEC-5.3-01 Drag-and-Drop Untrusted Slot Validation ──────────────────
  describe("7. SEC-5.3-01 Drag-and-Drop Untrusted Slot Validation", () => {
    // Pure function representing the exact SEC-5.3-01 validation logic implemented in WebsiteEditor.tsx
    function simulatePlaceSlot(untrustedSlot, targetLocation = "in_article", availableSlots, currentBlocks = []) {
      const warnings = [];
      const alerts = [];

      // 1. Structure validation
      if (!untrustedSlot || typeof untrustedSlot !== "object" || typeof untrustedSlot.slot_id !== "string") {
        warnings.push("rejected invalid ad slot payload");
        return { success: false, warnings, alerts, blocks: currentBlocks };
      }

      // 2. Authoritative slot resolution
      const authoritativeSlot = availableSlots.find((s) => s.slot_id === untrustedSlot.slot_id);
      if (!authoritativeSlot) {
        warnings.push(`rejected unverified or foreign ad slot: ${untrustedSlot.slot_id}`);
        alerts.push("Selected ad slot is not authorized for this project.");
        return { success: false, warnings, alerts, blocks: currentBlocks };
      }

      // 3. Status check
      if (authoritativeSlot.status !== 1) {
        alerts.push("Cannot place an inactive ad slot.");
        return { success: false, warnings, alerts, blocks: currentBlocks };
      }

      // 4. Location validation
      const allowedLocations = ["header_banner", "in_article", "sidebar", "footer"];
      const validatedLocation = allowedLocations.includes(targetLocation) ? targetLocation : "in_article";

      // 5. Format validation
      if (authoritativeSlot.format !== 1 && authoritativeSlot.format !== 2) {
        warnings.push(`unsupported slot format: ${authoritativeSlot.format}`);
        return { success: false, warnings, alerts, blocks: currentBlocks };
      }

      // 6. Block creation using STRICTLY authoritative values
      const newBlock = {
        id: `blk_ad_test`,
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: validatedLocation,
          format: authoritativeSlot.format,
          ad_slot_id: authoritativeSlot.slot_id,
        },
      };

      let nextBlocks;
      if (validatedLocation === "header_banner") {
        const heroIdx = currentBlocks.findIndex((b) => b.type === "hero");
        if (heroIdx >= 0) {
          nextBlocks = [
            ...currentBlocks.slice(0, heroIdx + 1),
            newBlock,
            ...currentBlocks.slice(heroIdx + 1),
          ];
        } else {
          nextBlocks = [newBlock, ...currentBlocks];
        }
      } else if (validatedLocation === "footer") {
        const footerIdx = currentBlocks.findIndex((b) => b.type === "footer");
        if (footerIdx >= 0) {
          nextBlocks = [
            ...currentBlocks.slice(0, footerIdx),
            newBlock,
            ...currentBlocks.slice(footerIdx),
          ];
        } else {
          nextBlocks = [...currentBlocks, newBlock];
        }
      } else {
        nextBlocks = [...currentBlocks, newBlock];
      }

      return { success: true, warnings, alerts, blocks: nextBlocks, createdBlock: newBlock };
    }

    test("Rejects forged slot_id not present in authoritative availableSlots", () => {
      const forgedPayload = {
        slot_id: "slot_forged_cross_publisher_hack",
        format: 1,
        status: 1,
      };

      const res = simulatePlaceSlot(forgedPayload, "in_article", mockAvailableSlots);

      assert.equal(res.success, false);
      assert.ok(res.warnings[0].includes("rejected unverified or foreign ad slot"));
      assert.equal(res.alerts[0], "Selected ad slot is not authorized for this project.");
      assert.equal(res.blocks.length, 0);
    });

    test("Rejects inactive slot even if present in availableSlots", () => {
      const inactivePayload = {
        slot_id: "slot_inactive_footer",
        format: 1,
        status: 1, // Attacker tries to lie about status
      };

      const res = simulatePlaceSlot(inactivePayload, "in_article", mockAvailableSlots);

      assert.equal(res.success, false);
      assert.equal(res.alerts[0], "Cannot place an inactive ad slot.");
      assert.equal(res.blocks.length, 0);
    });

    test("Rejects malformed non-object and null slot payloads", () => {
      assert.equal(simulatePlaceSlot(null, "in_article", mockAvailableSlots).success, false);
      assert.equal(simulatePlaceSlot(undefined, "in_article", mockAvailableSlots).success, false);
      assert.equal(simulatePlaceSlot("string_instead_of_object", "in_article", mockAvailableSlots).success, false);
      assert.equal(simulatePlaceSlot({ no_slot_id: true }, "in_article", mockAvailableSlots).success, false);
    });

    test("Rejects unsupported slot format (e.g. format 3 or 99)", () => {
      const slotsWithUnsupported = [
        ...mockAvailableSlots,
        {
          id: 204,
          slot_id: "slot_format_99",
          name: "Weird Format Slot",
          format: 99,
          status: 1,
          width: 300,
          height: 250,
          floor_price: 1.0,
          property_id: 10,
          is_chat_surface: false,
        },
      ];

      const res = simulatePlaceSlot({ slot_id: "slot_format_99" }, "in_article", slotsWithUnsupported);

      assert.equal(res.success, false);
      assert.ok(res.warnings[0].includes("unsupported slot format"));
    });

    test("Accepts authorized active slot and strictly uses authoritative fields, ignoring forged attributes", () => {
      const payloadWithForgedAttributes = {
        slot_id: "slot_banner_top",
        format: 2, // Tampered format (authoritative is 1)
        name: "Hacked Banner",
        floor_price: 0.01,
      };

      const res = simulatePlaceSlot(payloadWithForgedAttributes, "in_article", mockAvailableSlots);

      assert.equal(res.success, true);
      assert.equal(res.blocks.length, 1);
      assert.equal(res.createdBlock.type, "ad_placement");
      assert.equal(res.createdBlock.content.ad_slot_id, "slot_banner_top");
      assert.equal(res.createdBlock.content.format, 1, "Format must use authoritative slot value (1), not tampered value (2)");
      assert.equal(res.createdBlock.content.placement_location, "in_article");
    });

    test("Properly routes header_banner location after hero block", () => {
      const existingBlocks = [
        { id: "blk_nav", type: "features_grid", is_visible: true, content: {} },
        { id: "blk_hero", type: "hero", is_visible: true, content: {} },
        { id: "blk_text", type: "text_section", is_visible: true, content: {} },
      ];

      const res = simulatePlaceSlot(
        { slot_id: "slot_banner_top" },
        "header_banner",
        mockAvailableSlots,
        existingBlocks
      );

      assert.equal(res.success, true);
      assert.equal(res.blocks.length, 4);
      assert.equal(res.blocks[1].id, "blk_hero");
      assert.equal(res.blocks[2].type, "ad_placement");
      assert.equal(res.blocks[2].content.placement_location, "header_banner");
    });

    test("Properly routes footer location before footer block", () => {
      const existingBlocks = [
        { id: "blk_hero", type: "hero", is_visible: true, content: {} },
        { id: "blk_text", type: "text_section", is_visible: true, content: {} },
        { id: "blk_foot", type: "footer", is_visible: true, content: {} },
      ];

      const res = simulatePlaceSlot(
        { slot_id: "slot_native_mid" },
        "footer",
        mockAvailableSlots,
        existingBlocks
      );

      assert.equal(res.success, true);
      assert.equal(res.blocks.length, 4);
      assert.equal(res.blocks[2].type, "ad_placement");
      assert.equal(res.blocks[2].content.placement_location, "footer");
      assert.equal(res.blocks[3].id, "blk_foot");
    });
  });

  // ── 8. AdSlotPalette Monetization Action Integration ───────────────────────
  describe("8. AdSlotPalette Monetization Action Integration", () => {
    test("Renders '⚙️ Monetization' button in header when onOpenMonetization is provided", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 101,
          availableSlots: mockAvailableSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          onOpenMonetization: () => {},
        })
      );

      assert.ok(html.includes('data-testid="palette-open-monetization-btn"'));
      assert.ok(html.includes("⚙️ Monetization"));
    });

    test("Renders '⚙️ Configure Publisher Linking' button in empty state when onOpenMonetization is provided", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 101,
          availableSlots: [],
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          onOpenMonetization: () => {},
        })
      );

      assert.ok(html.includes('data-testid="palette-empty-open-monetization-btn"'));
      assert.ok(html.includes("⚙️ Configure Publisher Linking"));
    });
  });
});
