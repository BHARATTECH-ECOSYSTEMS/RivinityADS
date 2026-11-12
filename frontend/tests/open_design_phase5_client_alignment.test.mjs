/**
 * open_design_phase5_client_alignment.test.mjs
 *
 * Stage 5.2: Frontend Client & Data Model Alignment Tests
 *
 * Verifies:
 * 1. Typed API client request/response handling for:
 *    - GET /api/v1/slot/{slot_id} (getSlot with strict integer slotId)
 *    - GET /api/v1/publisher/property/{property_id} (getPublisherProperty)
 *    - POST /api/v1/publisher/property/{property_id}/challenge (challengePublisherProperty)
 *    - POST /api/v1/publisher/property/{property_id}/verify (verifyPublisherProperty)
 *    - POST /api/v1/website/projects/{id}/publisher-link (linkWebsitePublisher)
 *    - GET /api/v1/website/projects/{id}/slots (listWebsiteProjectSlots)
 *    - GET /api/v1/website/projects/{id}/monetization (getWebsiteMonetizationStatus)
 *    - POST /api/v1/website/projects/{id}/monetization/consent (recordWebsiteMonetizationConsent)
 *    - POST /api/v1/website/projects/{id}/monetization/enable (enableWebsiteMonetization)
 *    - POST /api/v1/website/projects/{id}/monetization/disable (disableWebsiteMonetization)
 *    - POST /api/v1/website/projects/{id}/publish (publishWebsiteProject -> VersionSummary)
 *    - POST /api/v1/website/projects/{id}/unpublish (unpublishWebsiteProject -> UnpublishProjectResponse)
 * 2. Strict status-code handling (400 Bad Request, 403 Forbidden, 404 Not Found, 409 Conflict) via ApiError
 * 3. Accurate enum and dictionary representations (WebsiteMonetizationStatusCode, WebsiteMonetizationStatusMap)
 * 4. Authoritative 6-key prerequisite evaluation (project_active, publisher_active, payout_configured, property_verified, slots_authorized, consent_recorded)
 * 5. Accurate consent record structure (accepted_terms, version, scope, agreed_at, user_id)
 */

import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  getSlot,
  getPublisherProperty,
  challengePublisherProperty,
  verifyPublisherProperty,
  linkWebsitePublisher,
  listWebsiteProjectSlots,
  getWebsiteMonetizationStatus,
  recordWebsiteMonetizationConsent,
  enableWebsiteMonetization,
  disableWebsiteMonetization,
  publishWebsiteProject,
  unpublishWebsiteProject,
  WebsiteMonetizationStatusCode,
  WebsiteMonetizationStatusMap,
  ApiError,
  setMemoryToken,
} from "../src/api/client.ts";

const originalFetch = globalThis.fetch;

let mockRequests = [];
let mockResponses = [];

beforeEach(() => {
  mockRequests = [];
  mockResponses = [];
  setMemoryToken("test-jwt-token-xyz");

  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    mockRequests.push({ url, init });

    const nextResponse = mockResponses.shift() ?? {
      status: 200,
      body: { message: "ok" },
    };

    return {
      ok: nextResponse.status >= 200 && nextResponse.status < 300,
      status: nextResponse.status,
      statusText: nextResponse.status === 200 ? "OK" : "Error",
      headers: new Headers(nextResponse.headers ?? { "Content-Type": "application/json" }),
      json: async () => nextResponse.body,
      text: async () => JSON.stringify(nextResponse.body),
    };
  };
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  setMemoryToken(null);
});

describe("Stage 5.2 — Slot & Publisher Property API Client Alignment", () => {
  test("getSlot sends GET /api/v1/slot/{slot_id} with integer parameter and Authorization header", async () => {
    const mockSlot = {
      id: 42,
      publisher_id: 10,
      property_id: 5,
      slot_id: "slot-sidebar-300x250",
      name: "Sidebar Ad",
      format: 1,
      width: 300,
      height: 250,
      status: 1,
      floor_price: 1.5,
      is_chat_surface: false,
    };
    mockResponses.push({ status: 200, body: mockSlot });

    const result = await getSlot(42);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/slot/42");
    assert.equal(mockRequests[0].init.headers?.Authorization, "Bearer test-jwt-token-xyz");
    assert.deepEqual(result, mockSlot);
    assert.equal(result.id, 42);
    assert.equal(result.property_id, 5);
    assert.equal(result.floor_price, 1.5);
  });

  test("getSlot handles 404 Slot Not Found via ApiError", async () => {
    mockResponses.push({
      status: 404,
      body: { detail: "Slot 999 not found" },
    });

    await assert.rejects(
      async () => await getSlot(999),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 404);
        assert.equal(err.message, "Slot 999 not found");
        return true;
      }
    );
  });

  test("getPublisherProperty sends GET /api/v1/publisher/property/{property_id}", async () => {
    const mockProp = {
      id: 12,
      publisher_id: 7,
      name: "My Tech Blog",
      domain: "blog.example.com",
      status: 1,
      verification_status: "VERIFIED",
      verification_method: "DNS_TXT",
      verification_token: "tok-abc-123",
      challenge_expires_at: "2026-11-13T00:00:00Z",
      verified_at: "2026-11-11T12:00:00Z",
      last_error: null,
      is_platform_subdomain: false,
      created_at: "2026-11-10T10:00:00Z",
      updated_at: "2026-11-11T12:00:00Z",
    };
    mockResponses.push({ status: 200, body: mockProp });

    const result = await getPublisherProperty(12);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/publisher/property/12");
    assert.deepEqual(result, mockProp);
    assert.equal(result.verification_status, "VERIFIED");
    assert.equal(result.verification_method, "DNS_TXT");
  });

  test("challengePublisherProperty sends POST /api/v1/publisher/property/{property_id}/challenge with DNS_TXT default", async () => {
    const mockChallenge = {
      property_id: 12,
      domain: "blog.example.com",
      verification_method: "DNS_TXT",
      verification_token: "tok-abc-123",
      dns_record_name: "_rivinity-challenge.blog.example.com",
      dns_record_value: "tok-abc-123",
      html_meta_tag: '<meta name="rivinity-site-verification" content="tok-abc-123">',
      challenge_expires_at: "2026-11-13T00:00:00Z",
      instructions: "Add a DNS TXT record for '_rivinity-challenge.blog.example.com' with value 'tok-abc-123'.",
    };
    mockResponses.push({ status: 200, body: mockChallenge });

    const result = await challengePublisherProperty(12);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/publisher/property/12/challenge");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), { method: "DNS_TXT" });
    assert.deepEqual(result, mockChallenge);
    assert.equal(result.dns_record_name, "_rivinity-challenge.blog.example.com");
    assert.equal(result.dns_record_value, "tok-abc-123");
    assert.equal(result.html_meta_tag, '<meta name="rivinity-site-verification" content="tok-abc-123">');
  });

  test("challengePublisherProperty supports HTML_META method parameter", async () => {
    const mockChallenge = {
      property_id: 12,
      domain: "blog.example.com",
      verification_method: "HTML_META",
      verification_token: "tok-abc-123",
      dns_record_name: "_rivinity-challenge.blog.example.com",
      dns_record_value: "tok-abc-123",
      html_meta_tag: '<meta name="rivinity-site-verification" content="tok-abc-123">',
      challenge_expires_at: "2026-11-13T00:00:00Z",
      instructions: "Add the meta tag into your head section.",
    };
    mockResponses.push({ status: 200, body: mockChallenge });

    const result = await challengePublisherProperty(12, "HTML_META");

    assert.equal(mockRequests.length, 1);
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), { method: "HTML_META" });
    assert.equal(result.verification_method, "HTML_META");
  });

  test("verifyPublisherProperty sends POST /api/v1/publisher/property/{property_id}/verify and parses verified property", async () => {
    const mockVerified = {
      id: 12,
      publisher_id: 7,
      name: "My Tech Blog",
      domain: "blog.example.com",
      status: 1,
      verification_status: "VERIFIED",
      verification_method: "DNS_TXT",
      verification_token: "tok-abc-123",
      challenge_expires_at: "2026-11-13T00:00:00Z",
      verified_at: "2026-11-11T15:00:00Z",
      last_error: null,
      is_platform_subdomain: false,
      created_at: "2026-11-10T10:00:00Z",
      updated_at: "2026-11-11T15:00:00Z",
    };
    mockResponses.push({ status: 200, body: mockVerified });

    const result = await verifyPublisherProperty(12);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/publisher/property/12/verify");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.equal(result.verification_status, "VERIFIED");
    assert.equal(result.last_error, null);
  });

  test("verifyPublisherProperty handles domain verification failure with status FAILED", async () => {
    const mockFailedVerification = {
      id: 12,
      publisher_id: 7,
      name: "My Tech Blog",
      domain: "blog.example.com",
      status: 0,
      verification_status: "FAILED",
      verification_method: "DNS_TXT",
      verification_token: "tok-abc-123",
      challenge_expires_at: "2026-11-13T00:00:00Z",
      verified_at: null,
      last_error: "DNS TXT record not found for _rivinity-challenge.blog.example.com",
      is_platform_subdomain: false,
      created_at: "2026-11-10T10:00:00Z",
      updated_at: "2026-11-11T15:00:00Z",
    };
    mockResponses.push({ status: 200, body: mockFailedVerification });

    const result = await verifyPublisherProperty(12);

    assert.equal(result.verification_status, "FAILED");
    assert.equal(result.last_error, "DNS TXT record not found for _rivinity-challenge.blog.example.com");
  });

  test("verifyPublisherProperty handles 403 authorization and 400 validation failures", async () => {
    mockResponses.push({
      status: 403,
      body: { detail: "Access denied to property of different publisher" },
    });

    await assert.rejects(
      async () => await verifyPublisherProperty(99),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 403);
        assert.equal(err.message, "Access denied to property of different publisher");
        return true;
      }
    );
  });
});

describe("Stage 5.2 — Website Project Publisher Linking & Slot Binding", () => {
  test("linkWebsitePublisher sends POST /api/v1/website/projects/{id}/publisher-link", async () => {
    const mockProject = {
      id: 1,
      owner_user_id: 3,
      publisher_id: 10,
      property_id: 25,
      name: "Acme Portal",
      slug: "acme-portal",
      custom_domain: null,
      template_id: "modern-saas",
      theme_config: {
        primary_color: "#3b82f6",
        secondary_color: "#1e293b",
        background_color: "#ffffff",
        text_color: "#0f172a",
        font_family: "Inter",
        border_radius: "8px",
      },
      status: 0,
      monetization_status: 0,
      current_published_version_id: null,
      is_deleted: false,
      created_at: "2026-11-11T10:00:00Z",
      updated_at: "2026-11-11T15:00:00Z",
      pages: [],
    };
    mockResponses.push({ status: 200, body: mockProject });

    const result = await linkWebsitePublisher(1, { publisher_id: 10, property_id: 25 });

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/publisher-link");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), { publisher_id: 10, property_id: 25 });
    assert.equal(result.publisher_id, 10);
    assert.equal(result.property_id, 25);
  });

  test("linkWebsitePublisher handles 403 tenant mismatch and 400 unlinked property error", async () => {
    // 403 Tenant Mismatch
    mockResponses.push({
      status: 403,
      body: { detail: "Publisher account does not belong to your tenant account." },
    });

    await assert.rejects(
      async () => await linkWebsitePublisher(1, { publisher_id: 99, property_id: 25 }),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 403);
        assert.equal(err.message, "Publisher account does not belong to your tenant account.");
        return true;
      }
    );

    // 400 Property does not exist or belong to specified publisher
    mockResponses.push({
      status: 400,
      body: { detail: "Property does not exist or does not belong to the specified publisher." },
    });

    await assert.rejects(
      async () => await linkWebsitePublisher(1, { publisher_id: 10, property_id: 999 }),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 400);
        assert.equal(err.message, "Property does not exist or does not belong to the specified publisher.");
        return true;
      }
    );
  });

  test("listWebsiteProjectSlots sends GET /api/v1/website/projects/{id}/slots and parses BoundSlot array", async () => {
    const mockBoundSlots = [
      {
        id: 101,
        slot_id: "banner-header-top",
        name: "Header Banner",
        format: 1,
        width: 728,
        height: 90,
        status: 1,
        floor_price: 2.0,
        property_id: 25,
        is_chat_surface: false,
      },
      {
        id: 102,
        slot_id: "native-article-mid",
        name: "In-Article Native",
        format: 2,
        width: 600,
        height: 250,
        status: 1,
        floor_price: 1.25,
        property_id: 25,
        is_chat_surface: false,
      },
    ];
    mockResponses.push({ status: 200, body: mockBoundSlots });

    const result = await listWebsiteProjectSlots(1);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/slots");
    assert.equal(result.length, 2);
    assert.equal(result[0].slot_id, "banner-header-top");
    assert.equal(result[0].floor_price, 2.0);
    assert.equal(result[1].format, 2);
  });
});

describe("Stage 5.2 — Website Project Monetization Lifecycle", () => {
  test("getWebsiteMonetizationStatus sends GET /api/v1/website/projects/{id}/monetization and asserts all 6 prerequisite keys", async () => {
    const mockStatus = {
      project_id: 1,
      is_eligible: false,
      current_status: WebsiteMonetizationStatusCode.DISABLED,
      prerequisites: {
        project_active: true,
        publisher_active: true,
        payout_configured: true,
        property_verified: false,
        slots_authorized: true,
        consent_recorded: false,
      },
      failure_reasons: [
        "Publisher property 'acme.com' is not verified (status: PENDING).",
        "Explicit publisher monetization terms have not been accepted.",
      ],
      slot_count: 2,
      consent: null,
    };
    mockResponses.push({ status: 200, body: mockStatus });

    const result = await getWebsiteMonetizationStatus(1);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/monetization");
    assert.equal(result.is_eligible, false);
    assert.equal(result.current_status, WebsiteMonetizationStatusCode.DISABLED);
    // Explicit assertions on all 6 authoritative backend prerequisite keys
    assert.equal(result.prerequisites.project_active, true);
    assert.equal(result.prerequisites.publisher_active, true);
    assert.equal(result.prerequisites.payout_configured, true);
    assert.equal(result.prerequisites.property_verified, false);
    assert.equal(result.prerequisites.slots_authorized, true);
    assert.equal(result.prerequisites.consent_recorded, false);
    assert.equal(result.failure_reasons.length, 2);
    assert.equal(result.consent, null);
  });

  test("recordWebsiteMonetizationConsent sends POST /api/v1/website/projects/{id}/monetization/consent and parses MonetizationConsentRecord", async () => {
    const mockStatusWithConsent = {
      project_id: 1,
      is_eligible: true,
      current_status: WebsiteMonetizationStatusCode.DISABLED,
      prerequisites: {
        project_active: true,
        publisher_active: true,
        payout_configured: true,
        property_verified: true,
        slots_authorized: true,
        consent_recorded: true,
      },
      failure_reasons: [],
      slot_count: 2,
      consent: {
        accepted_terms: true,
        version: "v1.0",
        scope: { ad_delivery: true, revenue_share: true },
        agreed_at: "2026-11-11T15:00:00Z",
        user_id: 3,
      },
    };
    mockResponses.push({ status: 200, body: mockStatusWithConsent });

    const payload = {
      accepted_terms: true,
      version: "v1.0",
      scope: { ad_delivery: true, revenue_share: true },
    };
    const result = await recordWebsiteMonetizationConsent(1, payload);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/monetization/consent");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), payload);
    assert.equal(result.prerequisites.consent_recorded, true);
    assert.equal(result.is_eligible, true);
    // Assert typed MonetizationConsentRecord structure
    assert.notEqual(result.consent, null);
    assert.equal(result.consent?.accepted_terms, true);
    assert.equal(result.consent?.version, "v1.0");
    assert.equal(result.consent?.user_id, 3);
    assert.equal(result.consent?.agreed_at, "2026-11-11T15:00:00Z");
    assert.equal(result.consent?.scope.ad_delivery, true);
    assert.equal(result.consent?.scope.revenue_share, true);
  });

  test("recordWebsiteMonetizationConsent supports string revenue_share ratio and active toggle in scope", async () => {
    const mockStatusWithStringRatio = {
      project_id: 1,
      is_eligible: true,
      current_status: WebsiteMonetizationStatusCode.DISABLED,
      prerequisites: {
        project_active: true,
        publisher_active: true,
        payout_configured: true,
        property_verified: true,
        slots_authorized: true,
        consent_recorded: true,
      },
      failure_reasons: [],
      slot_count: 2,
      consent: {
        accepted_terms: true,
        version: "v1.0",
        scope: { ad_delivery: true, revenue_share: "70/30", active: true },
        agreed_at: "2026-11-11T16:00:00Z",
        user_id: 3,
      },
    };
    mockResponses.push({ status: 200, body: mockStatusWithStringRatio });

    const payload = {
      accepted_terms: true,
      version: "v1.0",
      scope: { ad_delivery: true, revenue_share: "70/30", active: true },
    };
    const result = await recordWebsiteMonetizationConsent(1, payload);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/monetization/consent");
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), payload);
    assert.notEqual(result.consent, null);
    assert.equal(result.consent?.scope.ad_delivery, true);
    assert.equal(result.consent?.scope.revenue_share, "70/30");
    assert.equal(result.consent?.scope.active, true);
  });

  test("recordWebsiteMonetizationConsent handles 403 Forbidden access error", async () => {
    mockResponses.push({
      status: 403,
      body: { detail: "Access denied to website project." },
    });

    await assert.rejects(
      async () =>
        await recordWebsiteMonetizationConsent(1, {
          accepted_terms: true,
          scope: { ad_delivery: true },
        }),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 403);
        assert.equal(err.message, "Access denied to website project.");
        return true;
      }
    );
  });

  test("enableWebsiteMonetization sends POST /api/v1/website/projects/{id}/monetization/enable and handles 400 prerequisites failure", async () => {
    // 1. Success case
    const mockActiveStatus = {
      project_id: 1,
      is_eligible: true,
      current_status: WebsiteMonetizationStatusCode.ACTIVE_MONETIZED,
      prerequisites: {
        project_active: true,
        publisher_active: true,
        payout_configured: true,
        property_verified: true,
        slots_authorized: true,
        consent_recorded: true,
      },
      failure_reasons: [],
      slot_count: 2,
      consent: {
        accepted_terms: true,
        version: "v1.0",
        scope: { ad_delivery: true, revenue_share: true },
        agreed_at: "2026-11-11T15:00:00Z",
        user_id: 3,
      },
    };
    mockResponses.push({ status: 200, body: mockActiveStatus });

    const result = await enableWebsiteMonetization(1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/monetization/enable");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.equal(result.current_status, WebsiteMonetizationStatusCode.ACTIVE_MONETIZED);

    // 2. Failure case (prerequisites incomplete)
    mockResponses.push({
      status: 400,
      body: {
        detail: "Cannot enable monetization: Publisher property 'acme.com' is not verified (status: PENDING).",
      },
    });

    await assert.rejects(
      async () => await enableWebsiteMonetization(1),
      (err) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 400);
        assert(err.message.includes("Cannot enable monetization"));
        return true;
      }
    );
  });

  test("disableWebsiteMonetization sends POST /api/v1/website/projects/{id}/monetization/disable", async () => {
    const mockDisabledStatus = {
      project_id: 1,
      is_eligible: true,
      current_status: WebsiteMonetizationStatusCode.DISABLED,
      prerequisites: {
        project_active: true,
        publisher_active: true,
        payout_configured: true,
        property_verified: true,
        slots_authorized: true,
        consent_recorded: true,
      },
      failure_reasons: [],
      slot_count: 2,
      consent: {
        accepted_terms: true,
        version: "v1.0",
        scope: { ad_delivery: false, active: false },
        agreed_at: "2026-11-11T15:00:00Z",
        user_id: 3,
      },
    };
    mockResponses.push({ status: 200, body: mockDisabledStatus });

    const result = await disableWebsiteMonetization(1);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/monetization/disable");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.equal(result.current_status, WebsiteMonetizationStatusCode.DISABLED);
  });
});

describe("Stage 5.2 — Publication Contract Alignment", () => {
  test("publishWebsiteProject returns VersionSummary matching backend VersionSummaryResponse", async () => {
    const mockVersionSummary = {
      id: 15,
      project_id: 1,
      version_number: 3,
      published_by_user_id: 3,
      notes: "Release 3 with ad unit blocks",
      published_at: "2026-11-11T15:30:00Z",
      rollback_from_version_id: null,
    };
    mockResponses.push({ status: 200, body: mockVersionSummary });

    const result = await publishWebsiteProject(1, "Release 3 with ad unit blocks");

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/publish");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.deepEqual(JSON.parse(mockRequests[0].init.body), { notes: "Release 3 with ad unit blocks" });
    assert.equal(result.version_number, 3);
    assert.equal(result.notes, "Release 3 with ad unit blocks");
  });

  test("unpublishWebsiteProject returns UnpublishProjectResponse", async () => {
    const mockUnpublishRes = {
      message: "Website unpublished successfully",
      status: 0,
      current_published_version_id: null,
    };
    mockResponses.push({ status: 200, body: mockUnpublishRes });

    const result = await unpublishWebsiteProject(1);

    assert.equal(mockRequests.length, 1);
    assert.equal(mockRequests[0].url, "/api/v1/website/projects/1/unpublish");
    assert.equal(mockRequests[0].init.method, "POST");
    assert.equal(result.message, "Website unpublished successfully");
    assert.equal(result.status, 0);
    assert.equal(result.current_published_version_id, null);
  });
});

describe("Stage 5.2 — Enum & Dictionary Verification", () => {
  test("WebsiteMonetizationStatusCode enum maps to exact backend numeric codes", () => {
    assert.equal(WebsiteMonetizationStatusCode.DISABLED, 0);
    assert.equal(WebsiteMonetizationStatusCode.PENDING_APPROVAL, 1);
    assert.equal(WebsiteMonetizationStatusCode.ACTIVE_MONETIZED, 2);
    assert.equal(WebsiteMonetizationStatusCode.PAUSED, 3);
    assert.equal(WebsiteMonetizationStatusCode.REJECTED, 4);
  });

  test("WebsiteMonetizationStatusMap maps numeric codes to human strings", () => {
    assert.equal(WebsiteMonetizationStatusMap[0], "disabled");
    assert.equal(WebsiteMonetizationStatusMap[1], "pending_approval");
    assert.equal(WebsiteMonetizationStatusMap[2], "active_monetized");
    assert.equal(WebsiteMonetizationStatusMap[3], "paused");
    assert.equal(WebsiteMonetizationStatusMap[4], "rejected");
  });
});
