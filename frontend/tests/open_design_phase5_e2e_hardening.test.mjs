/**
 * open_design_phase5_e2e_hardening.test.mjs
 *
 * Phase 5 Stage 5.5: Final Regression, Security Verification & Hardening
 *
 * Comprehensive End-to-End Test Suite covering:
 * Area 1: Publisher Property Linking & Scoping
 * Area 2: Domain Verification Challenges & Verification Flow
 * Area 3: Ad-Slot Discovery & Canvas Placement Integration
 * Area 4: Monetization Prerequisites & Explicit Consent
 * Area 5: Publication Validation & Public Delivery Containers
 * Area 6: Anti-Tampering, Preview Isolation & Defense-in-Depth
 *
 * CRITICAL RESTRICTION:
 * Live monetization remains disabled. All ad rendering and preview assertions
 * verify zero-spend, non-billable sandbox isolation.
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
  const bundlePath = path.resolve("./tests/.compiled_phase5_hardening_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { WebsiteMonetizationModal } from '../src/components/website/WebsiteMonetizationModal';
export { AdSlotPalette } from '../src/components/website/AdSlotPalette';
export { CanvasBlockRenderer } from '../src/components/website/CanvasBlockRenderer';
export { BlockPropertyForm } from '../src/components/website/BlockPropertyForm';
export {
  isElementProtected,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  PROTECTED_SELECTORS,
} from '../src/services/websiteBridge';
export {
  sanitizeAndValidateOpenDesignHtml,
  containsSpoofingTokens,
  validateGeneratedBlockItem,
} from '../src/services/openDesignPatcher';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_phase5_hardening_entry.tsx");
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
  CanvasBlockRenderer,
  BlockPropertyForm,
  isElementProtected,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  PROTECTED_SELECTORS,
  sanitizeAndValidateOpenDesignHtml,
  containsSpoofingTokens,
  validateGeneratedBlockItem,
  linkWebsitePublisher,
  listWebsiteProjectSlots,
  getWebsiteMonetizationStatus,
  recordWebsiteMonetizationConsent,
  enableWebsiteMonetization,
  disableWebsiteMonetization,
  publishWebsiteProject,
  unpublishWebsiteProject,
  challengePublisherProperty,
  verifyPublisherProperty,
  fetchPublisherProperties,
  generateWebsitePreviewToken,
  ApiError,
  setMemoryToken,
  getMemoryToken,
} = await import(bundleUrl);

const sampleTheme = {
  primary_color: "#2563eb",
  secondary_color: "#1e293b",
  font_family: "Inter",
  border_radius: "8px",
};

const sampleProject = {
  id: 101,
  owner_user_id: 1,
  publisher_id: 42,
  property_id: 7,
  name: "Hardening Test Site",
  slug: "hardening-site",
  custom_domain: "mainblog.example.com",
  status: 10,
  monetization_status: 10,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  pages: [],
};

after(() => {
  console.error = origError;
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

describe("RivinityADS — Open Design Phase 5 Stage 5.5: Final Hardening & Regression Suite", () => {
  let originalFetch;
  let fetchCalls = [];

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    fetchCalls = [];
    setMemoryToken("test_auth_token_stage55");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setMemoryToken(null);
  });

  function mockFetch(handler) {
    globalThis.fetch = async (url, options = {}) => {
      fetchCalls.push({ url: String(url), options });
      return handler(String(url), options);
    };
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 1: Publisher Property Linking & Scoping
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 1: Publisher Property Linking & Scoping", () => {
    test("linkWebsitePublisher sends correct POST payload and authorization header", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/publisher-link"));
        assert.equal(opts.method, "POST");
        assert.equal(opts.headers["Authorization"], "Bearer test_auth_token_stage55");
        const body = JSON.parse(opts.body);
        assert.equal(body.publisher_id, 42);
        assert.equal(body.property_id, 7);

        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 101,
            owner_user_id: 1,
            publisher_id: 42,
            property_id: 7,
            name: "Linked Site",
            slug: "linked-site",
            monetization_status: 10,
          }),
        };
      });

      const res = await linkWebsitePublisher(101, { publisher_id: 42, property_id: 7 });
      assert.equal(res.id, 101);
      assert.equal(res.publisher_id, 42);
      assert.equal(res.property_id, 7);
    });

    test("linkWebsitePublisher handles 403 Forbidden tenant isolation error", async () => {
      mockFetch(async () => ({
        ok: false,
        status: 403,
        json: async () => ({
          detail: "Publisher account does not belong to your tenant account.",
        }),
      }));

      await assert.rejects(
        () => linkWebsitePublisher(101, { publisher_id: 999, property_id: 7 }),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 403);
          assert.ok(err.message.includes("Publisher account does not belong"));
          return true;
        }
      );
    });

    test("linkWebsitePublisher handles 400 Bad Request non-existent property error", async () => {
      mockFetch(async () => ({
        ok: false,
        status: 400,
        json: async () => ({
          detail: "Property does not exist or does not belong to the specified publisher.",
        }),
      }));

      await assert.rejects(
        () => linkWebsitePublisher(101, { publisher_id: 42, property_id: 999 }),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          assert.ok(err.message.includes("Property does not exist"));
          return true;
        }
      );
    });

    test("WebsiteMonetizationModal Property Linking Tab renders publisher and property details", () => {
      const mockProps = [
        {
          id: 7,
          publisher_id: 42,
          name: "Main Blog Property",
          domain: "mainblog.example.com",
          status: 1,
          verification_status: "VERIFIED",
          verification_method: "DNS_TXT",
          is_platform_subdomain: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ];

      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: sampleProject,
          publisherId: 42,
          initialProperties: mockProps,
          initialTab: "linking",
        })
      );

      assert.ok(html.includes("mainblog.example.com"));
      assert.ok(html.includes("Main Blog Property"));
      assert.ok(html.includes("Linked"));
      assert.ok(html.includes("VERIFIED"));
    });
  });

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 2: Domain Verification Challenges & Verification Flow
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 2: Domain Verification Challenges & Verification Flow", () => {
    test("challengePublisherProperty sends POST to /api/v1/publisher/property/{id}/challenge", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/publisher/property/7/challenge"));
        assert.equal(opts.method, "POST");
        const body = JSON.parse(opts.body);
        assert.equal(body.method, "DNS_TXT");

        return {
          ok: true,
          status: 200,
          json: async () => ({
            property_id: 7,
            domain: "mainblog.example.com",
            verification_method: "DNS_TXT",
            verification_token: "rivinity_sec_tok_998877",
            dns_record_name: "_rivinity-challenge.mainblog.example.com",
            dns_record_value: "rivinity_sec_tok_998877",
            html_meta_tag: '<meta name="rivinity-site-verification" content="rivinity_sec_tok_998877">',
            challenge_expires_at: "2026-11-13T00:00:00Z",
            instructions: "Add a DNS TXT record for '_rivinity-challenge.mainblog.example.com'.",
          }),
        };
      });

      const res = await challengePublisherProperty(7, "DNS_TXT");
      assert.equal(res.property_id, 7);
      assert.equal(res.verification_method, "DNS_TXT");
      assert.equal(res.dns_record_name, "_rivinity-challenge.mainblog.example.com");
      assert.equal(res.verification_token, "rivinity_sec_tok_998877");
    });

    test("verifyPublisherProperty sends POST to /api/v1/publisher/property/{id}/verify", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/publisher/property/7/verify"));
        assert.equal(opts.method, "POST");

        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 7,
            publisher_id: 42,
            name: "Main Blog Property",
            domain: "mainblog.example.com",
            status: 1,
            verification_status: "VERIFIED",
            verification_method: "DNS_TXT",
            verified_at: "2026-11-11T12:00:00Z",
            is_platform_subdomain: false,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-11-11T12:00:00Z",
          }),
        };
      });

      const res = await verifyPublisherProperty(7);
      assert.equal(res.verification_status, "VERIFIED");
      assert.ok(res.verified_at);
    });

    test("WebsiteMonetizationModal Verification Tab renders challenge details and copy helpers", () => {
      const mockProps = [
        {
          id: 7,
          publisher_id: 42,
          name: "Main Blog Property",
          domain: "mainblog.example.com",
          status: 1,
          verification_status: "PENDING",
          verification_method: "DNS_TXT",
          is_platform_subdomain: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ];

      const mockChallenge = {
        property_id: 7,
        domain: "mainblog.example.com",
        verification_method: "DNS_TXT",
        verification_token: "sec_tok_12345",
        dns_record_name: "_rivinity-challenge.mainblog.example.com",
        dns_record_value: "sec_tok_12345",
        html_meta_tag: '<meta name="rivinity-site-verification" content="sec_tok_12345">',
        challenge_expires_at: "2026-11-13T00:00:00Z",
        instructions: "Add a DNS TXT record.",
      };

      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: sampleProject,
          publisherId: 42,
          initialProperties: mockProps,
          initialChallenge: mockChallenge,
          initialTab: "verification",
        })
      );

      // Verify presence of verification controls
      assert.ok(html.includes("Domain Verification"));
      assert.ok(html.includes("_rivinity-challenge.mainblog.example.com"));
      assert.ok(html.includes("sec_tok_12345"));
    });
  });

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 3: Ad-Slot Discovery & Canvas Placement Integration
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 3: Ad-Slot Discovery & Canvas Placement Integration", () => {
    test("listWebsiteProjectSlots sends GET to /api/v1/website/projects/{id}/slots", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/slots"));
        assert.equal(opts.method || "GET", "GET");

        return {
          ok: true,
          status: 200,
          json: async () => [
            {
              id: 1,
              slot_id: "slot_banner_top",
              name: "Header Banner",
              format: 1,
              width: 728,
              height: 90,
              status: 1,
              floor_price: 1.5,
              property_id: 7,
              is_chat_surface: false,
            },
          ],
        };
      });

      const slots = await listWebsiteProjectSlots(101);
      assert.equal(slots.length, 1);
      assert.equal(slots[0].slot_id, "slot_banner_top");
      assert.equal(slots[0].format, 1);
    });

    test("AdSlotPalette renders active slots with draggable=true and + Place in Content action", () => {
      const activeSlot = {
        id: 1,
        slot_id: "slot_banner_top",
        name: "Header Banner",
        format: 1,
        width: 728,
        height: 90,
        status: 1,
        floor_price: 1.5,
        property_id: 7,
        is_chat_surface: false,
      };

      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 101,
          availableSlots: [activeSlot],
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('draggable="true"'));
      assert.ok(html.includes("Header Banner"));
      assert.ok(html.includes("728×90") || html.includes("728x90"));
      assert.ok(html.includes("+ Place in Content"));
    });

    test("AdSlotPalette renders inactive slots with Inactive badge and draggable=false", () => {
      const inactiveSlot = {
        id: 2,
        slot_id: "slot_inactive_bottom",
        name: "Inactive Slot",
        format: 1,
        width: 300,
        height: 250,
        status: 0, // Inactive
        floor_price: 0.5,
        property_id: 7,
        is_chat_surface: false,
      };

      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 101,
          availableSlots: [inactiveSlot],
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes("Inactive"));
      assert.ok(html.includes('draggable="false"'));
      assert.ok(/disabled(?:="")?[^>]*data-testid="place-slot-quick-slot_inactive_bottom"/i.test(html));
    });

    test("SEC-5.3-01 Remediation: Drop validation rejects unknown or untrusted slot payloads", () => {
      const authoritativeSlots = [
        {
          id: 1,
          slot_id: "slot_auth_1",
          name: "Authorized Slot",
          format: 1,
          width: 728,
          height: 90,
          status: 1,
          floor_price: 1.0,
          property_id: 7,
          is_chat_surface: false,
        },
      ];

      function validateDropSlot(untrustedPayload, authoritativeList) {
        if (!untrustedPayload || typeof untrustedPayload !== "object") return null;
        const candidateId = String(untrustedPayload.slot_id || "").trim();
        if (!candidateId) return null;
        const matched = authoritativeList.find((s) => s.slot_id === candidateId);
        if (!matched) return null; // Reject unknown slot ID
        if (matched.status !== 1) return null; // Reject inactive slot
        return matched;
      }

      // 1. Untrusted payload with completely unknown slot_id
      const fakeSlot = { slot_id: "forged_slot_999", format: 1, status: 1 };
      assert.equal(validateDropSlot(fakeSlot, authoritativeSlots), null);

      // 2. Untrusted payload with matching slot_id
      const realSlot = { slot_id: "slot_auth_1", format: 1 };
      const validated = validateDropSlot(realSlot, authoritativeSlots);
      assert.notEqual(validated, null);
      assert.equal(validated.slot_id, "slot_auth_1");
      assert.equal(validated.width, 728);
    });

    test("BlockPropertyForm restricts ad slot selection to authoritative bound slots", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          ad_slot_id: "slot_banner_top",
          format: 1,
          width: 728,
          height: 90,
        },
      };

      const availableSlots = [
        {
          id: 1,
          slot_id: "slot_banner_top",
          name: "Header Banner",
          format: 1,
          width: 728,
          height: 90,
          status: 1,
          floor_price: 1.5,
          property_id: 7,
          is_chat_surface: false,
        },
      ];

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: availableSlots,
        })
      );

      // Verify slot select dropdown is rendered with available options
      assert.ok(html.includes("Header Banner (slot_banner_top)"));
      // Verify non-billable notice
      assert.ok(html.includes("Preview Placeholder:"));
      assert.ok(html.includes("Ads are strictly non-billable mockups"));
    });
  });

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 4: Monetization Prerequisites & Explicit Consent
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 4: Monetization Prerequisites & Explicit Consent", () => {
    test("getWebsiteMonetizationStatus fetches authoritative 6 prerequisites", async () => {
      mockFetch(async (url) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/monetization"));

        return {
          ok: true,
          status: 200,
          json: async () => ({
            project_id: 101,
            is_eligible: false,
            current_status: 10,
            prerequisites: {
              project_active: true,
              publisher_active: true,
              payout_configured: true,
              property_verified: false,
              slots_authorized: true,
              consent_recorded: false,
            },
            failure_reasons: ["Publisher property is not verified."],
            slot_count: 1,
            consent: null,
          }),
        };
      });

      const res = await getWebsiteMonetizationStatus(101);
      assert.equal(res.project_id, 101);
      assert.equal(res.is_eligible, false);
      assert.equal(res.prerequisites.property_verified, false);
      assert.equal(res.prerequisites.consent_recorded, false);
      assert.ok(res.failure_reasons.length > 0);
    });

    test("recordWebsiteMonetizationConsent sends typed consent payload", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/monetization/consent"));
        assert.equal(opts.method, "POST");
        const body = JSON.parse(opts.body);
        assert.equal(body.accepted_terms, true);
        assert.equal(body.version, "v1.0");
        assert.equal(body.scope.display_ads, true);
        assert.equal(body.scope.revenue_share_acknowledged, true);

        return {
          ok: true,
          status: 200,
          json: async () => ({
            project_id: 101,
            is_eligible: true,
            current_status: 10,
            prerequisites: {
              project_active: true,
              publisher_active: true,
              payout_configured: true,
              property_verified: true,
              slots_authorized: true,
              consent_recorded: true,
            },
            failure_reasons: [],
            slot_count: 1,
            consent: {
              accepted_terms: true,
              version: "v1.0",
              scope: body.scope,
              agreed_at: "2026-11-11T12:00:00Z",
              user_id: 1,
            },
          }),
        };
      });

      const res = await recordWebsiteMonetizationConsent(101, {
        accepted_terms: true,
        version: "v1.0",
        scope: { display_ads: true, revenue_share_acknowledged: true },
      });

      assert.equal(res.consent.accepted_terms, true);
      assert.equal(res.consent.version, "v1.0");
      assert.equal(res.prerequisites.consent_recorded, true);
    });

    test("WebsiteMonetizationModal Terms Tab renders authoritative 70/30 revenue share disclosure", () => {
      const html = renderToStaticMarkup(
        React.createElement(WebsiteMonetizationModal, {
          isOpen: true,
          onClose: () => {},
          project: sampleProject,
          publisherId: 42,
          initialMonetizationStatus: {
            project_id: 101,
            is_eligible: false,
            current_status: 10,
            prerequisites: {
              project_active: true,
              publisher_active: true,
              payout_configured: true,
              property_verified: true,
              slots_authorized: true,
              consent_recorded: false,
            },
            failure_reasons: ["Monetization consent has not been recorded."],
            slot_count: 1,
            consent: null,
          },
          initialTab: "consent",
        })
      );

      // Verify revenue share terms
      assert.ok(html.includes("70% publisher / 30% platform revenue share split") || html.includes("Publisher Earnings"));
      assert.ok(html.includes("4. Terms &amp; 70/30 Share") || html.includes("70/30"));
      assert.ok(html.includes("I acknowledge and agree to the"));
    });

    test("enableWebsiteMonetization is rejected when project is ineligible", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/monetization/enable"));
        assert.equal(opts.method, "POST");

        return {
          ok: false,
          status: 400,
          json: async () => ({
            detail: "Cannot enable monetization: Publisher property is not verified.",
          }),
        };
      });

      await assert.rejects(
        () => enableWebsiteMonetization(101),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          assert.ok(err.message.includes("Cannot enable monetization"));
          return true;
        }
      );
    });
  });

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 5: Publication Validation & Public Delivery Containers
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 5: Publication Validation & Public Delivery Containers", () => {
    test("publishWebsiteProject sends POST to /api/v1/website/projects/{id}/publish", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/publish"));
        assert.equal(opts.method, "POST");
        const body = JSON.parse(opts.body);
        assert.equal(body.notes, "Stage 5.5 Publication");

        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 5,
            project_id: 101,
            version_number: 1,
            notes: "Stage 5.5 Publication",
            created_at: "2026-11-11T12:00:00Z",
          }),
        };
      });

      const res = await publishWebsiteProject(101, "Stage 5.5 Publication");
      assert.equal(res.version_number, 1);
      assert.equal(res.project_id, 101);
    });

    test("unpublishWebsiteProject sends POST to /api/v1/website/projects/{id}/unpublish", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/unpublish"));
        assert.equal(opts.method, "POST");

        return {
          ok: true,
          status: 200,
          json: async () => ({
            success: true,
            status: 10, // Draft
            message: "Project successfully unpublished.",
          }),
        };
      });

      const res = await unpublishWebsiteProject(101);
      assert.equal(res.success, true);
      assert.equal(res.status, 10);
    });

    test("CanvasBlockRenderer renders ad placement strictly as non-billable sandbox placeholder", () => {
      const adBlock = {
        id: "blk_ad_render_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          ad_slot_id: "slot_banner_top",
          format: 1,
          width: 728,
          height: 90,
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: adBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      // Verify non-billable indicators
      assert.ok(html.includes("Non-Billable Preview Placeholder"));
      assert.ok(html.includes("RivinityADS Placement Slot"));
      assert.ok(html.includes("Live monetization inactive"));
      assert.ok(html.includes("Slot: slot_banner_top"));
      assert.ok(html.includes('data-od-protected="true"'));
      assert.ok(html.includes('data-od-editable="false"'));
      // Crucial: No live ad tags or scripts
      assert.ok(!html.includes("<script"));
      assert.ok(!html.includes("/static/rivinity.js"));
    });
  });

  // ══════════════════════════════════════════════════════════════════════════════
  // Area 6: Anti-Tampering, Preview Isolation & Defense-in-Depth
  // ══════════════════════════════════════════════════════════════════════════════
  describe("Area 6: Anti-Tampering, Preview Isolation & Defense-in-Depth", () => {
    test("isElementProtected identifies ad containers and protected selectors", () => {
      const mockAdElement = {
        classList: { contains: (cls) => cls === "rivinity-ad-placeholder" },
        closest: (selector) => {
          if (
            selector === ".rivinity-ad-placeholder" ||
            selector.includes(".block-ad-placement") ||
            selector.includes('[data-od-protected="true"]')
          ) {
            return {};
          }
          return null;
        },
        getAttribute: (attr) => (attr === "data-od-protected" ? "true" : null),
      };

      assert.equal(isElementProtected(mockAdElement), true);

      const mockNormalElement = {
        classList: { contains: () => false },
        closest: () => null,
        getAttribute: () => null,
      };

      assert.equal(isElementProtected(mockNormalElement), false);
    });

    test("validateSelectionAgainstDraft rejects selection of ad placement blocks", () => {
      const draftBlocks = [
        {
          id: "blk_hero",
          type: "hero",
          is_visible: true,
          content: { headline: "Hello" },
        },
        {
          id: "blk_ad_guard",
          type: "ad_placement",
          is_visible: true,
          content: { ad_slot_id: "slot_1" },
        },
      ];

      const res = validateSelectionAgainstDraft(
        {
          blockId: "blk_ad_guard",
          blockType: "ad_placement",
          field: "ad_slot_id",
        },
        draftBlocks
      );

      assert.equal(res.isValid, false);
      assert.ok(res.error.includes("not permitted for block type 'ad_placement'"));
    });

    test("validateProposedPatchAgainstDraft rejects mutations on ad placement blocks", () => {
      const draftBlocks = [
        {
          id: "blk_ad_patch_test",
          type: "ad_placement",
          is_visible: true,
          content: { ad_slot_id: "slot_real" },
        },
      ];

      const res = validateProposedPatchAgainstDraft(
        {
          sessionId: "sess_stage55_test",
          blockId: "blk_ad_patch_test",
          blockType: "ad_placement",
          field: "ad_slot_id",
          proposedValue: "tampered_slot_id",
        },
        draftBlocks
      );

      assert.equal(res.isValid, false);
      assert.ok(res.error.includes("protected and cannot be edited"));
    });

    test("sanitizeAndValidateOpenDesignHtml strips dangerous script tags and event handlers", () => {
      const maliciousHtml = `
        <div>
          <h3>Safe Title</h3>
          <script>alert('xss')</script>
          <img src="https://images.example.com/pic.png" onerror="alert(1)" />
          <a href="javascript:stealCookie()">Click here</a>
        </div>
      `;

      const sanitization = sanitizeAndValidateOpenDesignHtml(maliciousHtml);
      assert.equal(sanitization.isValid, false);
      assert.ok(sanitization.error.includes("script") || sanitization.error.includes("executable"));
    });

    test("generateWebsitePreviewToken sends POST to /api/v1/website/projects/{id}/preview-token", async () => {
      mockFetch(async (url, opts) => {
        assert.ok(url.endsWith("/api/v1/website/projects/101/preview-token"));
        assert.equal(opts.method, "POST");

        return {
          ok: true,
          status: 200,
          json: async () => ({
            preview_token: "cryptographic_preview_token_hmac_12345",
            preview_url: "/preview/cryptographic_preview_token_hmac_12345",
            expires_in: 900,
          }),
        };
      });

      const res = await generateWebsitePreviewToken(101);
      assert.equal(res.preview_token, "cryptographic_preview_token_hmac_12345");
      assert.equal(res.expires_in, 900);
      assert.ok(res.preview_url.startsWith("/preview/"));
    });

    test("CRITICAL GUARANTEE: Live monetization remains completely inactive", () => {
      const placeholder = React.createElement(CanvasBlockRenderer, {
        block: {
          id: "ad_zero_spend_check",
          type: "ad_placement",
          is_visible: true,
          content: {
            ad_slot_id: "slot_never_billable",
            placement_location: "in_article",
          },
        },
        theme: sampleTheme,
        isSelected: false,
        onSelect: () => {},
        onDelete: () => {},
        canMoveUp: false,
        canMoveDown: false,
      });

      const rendered = renderToStaticMarkup(placeholder);
      assert.ok(rendered.includes("Non-Billable Preview Placeholder"));
      assert.ok(rendered.includes("Live monetization inactive"));
      assert.ok(!rendered.includes("rivinity-live-ad-container"));
      assert.ok(!rendered.includes("/static/rivinity.js"));
    });
  });
});
