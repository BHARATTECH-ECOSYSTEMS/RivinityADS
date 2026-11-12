/**
 * Stage 3.5 negative security regression tests for Open Design.
 *
 * Verifies:
 * A. Forged metadata (block ID, block type, protected/editable markers, platform tokens, case/whitespace/encoded variations).
 * B. Protected content (ad placements, consent banners, preview notices, honeypots, protected descendants).
 * C. Bridge message validation (envelope types, malformed payloads, stale sessions, inspector-only fields, array index safety, size limits).
 * D. Persistence protection (draft immutability upon rejected edits, patch function immunity, preview synchronization immunity).
 * E. Sanitizer consistency (cross-layer parity against XSS, executable protocols, spoofing classes/IDs, and oversized payloads).
 */

import test, { after, describe } from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve(__dirname, ".compiled_stage3_5_negative_security_bundle.mjs");
  const entryFile = path.resolve(__dirname, ".temp_stage3_5_negative_security_entry.ts");
  cleanupFiles.push(bundlePath, entryFile);

  fs.writeFileSync(
    entryFile,
    `
export {
  extractElementMetadata,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  isElementProtected,
  getBlockId,
  getBlockType,
  getFieldIdentifier,
  getItemIndex,
  applyPatchToBlock,
  syncBlockChangesToPreview,
  WebsiteBridgeClient,
  containsDisallowedHtmlOrScript,
} from '../src/services/websiteBridge';
export {
  parseHtmlFragment,
  patchOpenDesignSection,
  sanitizeAndValidateOpenDesignHtml,
  validateSafeUrl,
  containsSpoofingTokens,
} from '../src/services/openDesignPatcher';
`,
    "utf8"
  );

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
  });

  return pathToFileURL(bundlePath).href;
}

after(() => {
  for (const file of cleanupFiles) {
    try {
      if (fs.existsSync(file)) fs.unlinkSync(file);
    } catch {}
  }
});

const {
  extractElementMetadata,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  isElementProtected,
  getBlockId,
  getBlockType,
  getFieldIdentifier,
  getItemIndex,
  applyPatchToBlock,
  syncBlockChangesToPreview,
  WebsiteBridgeClient,
  containsDisallowedHtmlOrScript,
  parseHtmlFragment,
  patchOpenDesignSection,
  sanitizeAndValidateOpenDesignHtml,
  validateSafeUrl,
  containsSpoofingTokens,
} = await import(compileBundle());

function createMockElement(tag, attrs = {}, parent = null) {
  const el = {
    tagName: tag.toUpperCase(),
    attributes: { ...attrs },
    parentElement: parent,
    className: attrs.class || "",
    id: attrs.id || "",
    getAttribute(name) {
      if (name === "class") return this.className || null;
      if (name === "id") return this.id || null;
      return this.attributes[name] === undefined ? null : String(this.attributes[name]);
    },
    hasAttribute(name) {
      if (name === "class") return Boolean(this.className);
      if (name === "id") return Boolean(this.id);
      return this.attributes[name] !== undefined;
    },
    closest(selector) {
      let current = this;
      const selectors = selector.split(",").map((s) => s.trim());
      while (current) {
        for (const sel of selectors) {
          if (sel.startsWith("#") && current.id === sel.slice(1)) return current;
          if (sel.startsWith(".") && current.className.split(/\s+/).includes(sel.slice(1))) return current;
          if (sel === '[data-od-protected="true"]' && current.getAttribute("data-od-protected") === "true") return current;
          if (sel === "[data-od-block-id]" && current.hasAttribute("data-od-block-id")) return current;
          if (sel === "[data-od-block-type]" && current.hasAttribute("data-od-block-type")) return current;
          if (sel === '[data-od-block-type="ad_placement"]' && current.getAttribute("data-od-block-type") === "ad_placement") return current;
          if (sel.includes('[name="hp_comment"]') && current.getAttribute("name") === "hp_comment") return current;
          if (sel.includes("[data-od-edit-bridge]") && current.hasAttribute("data-od-edit-bridge")) return current;
        }
        current = current.parentElement;
      }
      return null;
    },
  };
  return el;
}

function createDraft() {
  return [
    {
      id: "hero-1",
      type: "hero",
      content: {
        headline: "Original headline",
        subheadline: "Original subheadline",
        cta_button: { label: "Click Me", target_url: "https://example.com" },
        alignment: "left",
      },
    },
    {
      id: "ad-1",
      type: "ad_placement",
      content: { placement_location: "header_banner", format: 1, ad_slot_id: "slot_banner_top" },
    },
    {
      id: "features-1",
      type: "features_grid",
      content: {
        title: "Key Features",
        items: [
          { title: "Feature One", description: "First feature description", icon: "star" },
          { title: "Feature Two", description: "Second feature description", icon: "check" },
        ],
      },
    },
    {
      id: "gallery-1",
      type: "image_gallery",
      content: {
        title: "Gallery",
        images: [
          { image_url: "https://example.com/img1.jpg", alt_text: "Image 1", caption: "Caption 1" },
        ],
      },
    },
    {
      id: "custom-1",
      type: "open_design_section",
      content: {
        html_content: '<section><div class="content"><p data-od-id="copy">Original copy</p></div></section>',
      },
    },
  ];
}

// ─────────────────────────────────────────────────────────────────────────────
// Suite A: Forged Metadata & Platform Identifiers
// ─────────────────────────────────────────────────────────────────────────────

describe("Open Design Stage 3.5 — A. Forged Metadata", () => {
  test("rejects malformed and non-existent block identifiers in draft validation", () => {
    const draft = createDraft();
    const invalidBlockIds = [
      "",
      "   ",
      "hero 1",
      "hero<script>",
      "hero/../path",
      "hero!@#",
      "a".repeat(65),
      "nonexistent_block_999",
      null,
      undefined,
      12345,
    ];

    for (const badId of invalidBlockIds) {
      const selResult = validateSelectionAgainstDraft(
        { blockId: badId, blockType: "hero", field: "headline", classification: "editable" },
        draft
      );
      assert.equal(selResult.isValid, false, `Selection should reject blockId: ${badId}`);

      const patchResult = validateProposedPatchAgainstDraft(
        { sessionId: "s1", blockId: badId, blockType: "hero", field: "headline", proposedValue: "Test" },
        draft,
        "s1"
      );
      assert.equal(patchResult.isValid, false, `Patch should reject blockId: ${badId}`);
    }
  });

  test("rejects forged or unrecognized block types in selection and patching", () => {
    const draft = createDraft();
    const badTypes = ["admin_panel", "system_script", "malicious_widget", "unknown", "", null];

    for (const badType of badTypes) {
      assert.equal(
        validateSelectionAgainstDraft(
          { blockId: "hero-1", blockType: badType, field: "headline", classification: "editable" },
          draft
        ).isValid,
        false
      );

      assert.equal(
        validateProposedPatchAgainstDraft(
          { sessionId: "s1", blockId: "hero-1", blockType: badType, field: "headline", proposedValue: "Val" },
          draft,
          "s1"
        ).isValid,
        false
      );
    }

    // Type mismatch with existing draft block
    assert.equal(
      validateProposedPatchAgainstDraft(
        { sessionId: "s1", blockId: "hero-1", blockType: "features_grid", field: "headline", proposedValue: "Val" },
        draft,
        "s1"
      ).isValid,
      false
    );
  });

  test("getBlockId and getBlockType return null for forged DOM attributes", () => {
    assert.equal(getBlockId(createMockElement("div", { "data-od-block-id": "hero with spaces" })), null);
    assert.equal(getBlockId(createMockElement("div", { "data-od-block-id": "hero<script>" })), null);
    assert.equal(getBlockId(createMockElement("div", { "data-od-block-id": "a".repeat(65) })), null);
    assert.equal(getBlockId(createMockElement("div", { "data-od-block-id": "" })), null);

    assert.equal(getBlockType(createMockElement("div", { "data-od-block-type": "invalid_type" })), null);
    assert.equal(getBlockType(createMockElement("div", { "data-od-block-type": "admin_widget" })), null);
  });

  test("extractElementMetadata fails closed when block identifier or type is forged", () => {
    const forgedIdEl = createMockElement("h1", {
      "data-od-block-id": "bad id!",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    assert.equal(extractElementMetadata(forgedIdEl), null);

    const forgedTypeEl = createMockElement("h1", {
      "data-od-block-id": "hero-1",
      "data-od-block-type": "unrecognized",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    assert.equal(extractElementMetadata(forgedTypeEl), null);
  });

  test("forged data-od-protected='false' cannot bypass ancestor protection", () => {
    const protectedParent = createMockElement("div", {
      class: "block-ad-placement",
      "data-od-block-id": "ad-1",
      "data-od-block-type": "ad_placement",
      "data-od-protected": "true",
    });

    const forgedChild = createMockElement(
      "span",
      {
        "data-od-protected": "false",
        "data-od-editable": "true",
        "data-od-field": "headline",
      },
      protectedParent
    );

    assert.equal(isElementProtected(forgedChild), true);
    const meta = extractElementMetadata(forgedChild);
    assert.equal(meta?.isProtected, true);
    assert.equal(meta?.isEditable, false);
  });

  test("forged editable marker on read-only elements fails selection verification", () => {
    const draft = createDraft();
    const result = validateSelectionAgainstDraft(
      {
        blockId: "features-1",
        blockType: "features_grid",
        field: "items.icon",
        itemIndex: 0,
        classification: "editable",
      },
      draft
    );
    assert.equal(result.isValid, false);
    assert.match(result.error || "", /icon element cannot have editable/i);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Suite B: Protected Content & Placement Immunity
// ─────────────────────────────────────────────────────────────────────────────

describe("Open Design Stage 3.5 — B. Protected Content", () => {
  test("direct attempts to modify ad placements are rejected in both proposals and selection", () => {
    const draft = createDraft();

    // 1. Native proposal targeting ad placement
    const patchResult = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "ad-1",
        blockType: "ad_placement",
        field: "placement_location",
        proposedValue: "footer",
      },
      draft,
      "s1"
    );
    assert.equal(patchResult.isValid, false);
    assert.match(patchResult.error || "", /ad placement block is protected/i);

    // 2. Selection claiming editable classification on ad placement
    const selResult = validateSelectionAgainstDraft(
      {
        blockId: "ad-1",
        blockType: "ad_placement",
        field: null,
        classification: "editable",
      },
      draft
    );
    assert.equal(selResult.isValid, false);
    assert.match(selResult.error || "", /ad placement block cannot have editable/i);
  });

  test("consent banner, preview notices, honeypots, and bridge elements are protected", () => {
    const protectedElements = [
      createMockElement("div", { id: "rivinity-consent-banner" }),
      createMockElement("div", { class: "preview-banner" }),
      createMockElement("input", { name: "hp_comment" }),
      createMockElement("script", { "data-od-edit-bridge": "" }),
      createMockElement("div", { class: "rivinity-live-ad-container" }),
      createMockElement("div", { class: "rivinity-ad-placeholder" }),
      createMockElement("div", { class: "rivinity-ad-slot" }),
    ];

    for (const el of protectedElements) {
      assert.equal(isElementProtected(el), true, `Element should be protected: ${el.tagName}`);
    }
  });

  test("protected descendants inherit immunity even with forged editable attributes", () => {
    const consentBanner = createMockElement("div", { id: "rivinity-consent-banner" });
    const innerHeadline = createMockElement(
      "h3",
      {
        "data-od-block-id": "hero-1",
        "data-od-block-type": "hero",
        "data-od-field": "title",
        "data-od-editable": "true",
      },
      consentBanner
    );

    assert.equal(isElementProtected(innerHeadline), true);
    const meta = extractElementMetadata(innerHeadline);
    assert.equal(meta?.isProtected, true);
    assert.equal(meta?.isEditable, false);
  });

  test("localized custom section patcher rejects targeting protected platform elements", () => {
    const protectedSelectors = [
      ".block-ad-placement",
      ".rivinity-ad-placeholder",
      ".rivinity-ad-slot",
      "#rivinity-consent-banner",
      ".preview-banner",
      '[name="hp_comment"]',
      '[data-od-edit-bridge]',
    ];

    for (const sel of protectedSelectors) {
      const result = patchOpenDesignSection({
        blockId: "custom-1",
        blockType: "open_design_section",
        patchKind: "html_fragment",
        targetSelector: sel,
        originalValue: "Safe",
        proposedValue: "Hacked",
        htmlContent: '<section><p data-od-id="copy">Safe</p></section>',
      });
      assert.equal(result.isValid, false, `Target selector ${sel} must be rejected`);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Suite C: Bridge Message Validation
// ─────────────────────────────────────────────────────────────────────────────

describe("Open Design Stage 3.5 — C. Bridge Message Validation", () => {
  test("WebsiteBridgeClient drops missing and unsupported message types without triggering callbacks", () => {
    let selectCalled = false;
    let editCalled = false;

    const mockIframe = { contentWindow: {} };
    const client = new WebsiteBridgeClient({
      iframe: mockIframe,
      onElementSelect: () => { selectCalled = true; },
      onEditProposePatch: () => { editCalled = true; },
    });

    // Simulate active connection state
    client["status"] = "connected";
    client["activeSessionId"] = "active-sess-1";

    // 1. Missing message type
    client["handlePortMessage"]({ data: { session_id: "active-sess-1", payload: {} } });
    assert.equal(selectCalled, false);
    assert.equal(editCalled, false);

    // 2. Unsupported message types
    client["handlePortMessage"]({ data: { type: "OD_SYSTEM_EVAL", session_id: "active-sess-1", payload: {} } });
    client["handlePortMessage"]({ data: { type: "OD_UNKNOWN", session_id: "active-sess-1", payload: {} } });
    client["handlePortMessage"]({ data: { type: "OD_MALICIOUS_EXEC", session_id: "active-sess-1", payload: {} } });
    assert.equal(selectCalled, false);
    assert.equal(editCalled, false);
  });

  test("WebsiteBridgeClient drops stale session messages", () => {
    let patchProposePayload = null;

    const mockIframe = { contentWindow: {} };
    const client = new WebsiteBridgeClient({
      iframe: mockIframe,
      onEditProposePatch: (payload) => { patchProposePayload = payload; },
    });

    client["status"] = "connected";
    client["activeSessionId"] = "active-sess-1";

    // Mismatched / stale session_id in envelope
    client["handlePortMessage"]({
      data: {
        type: "OD_EDIT_PROPOSE_PATCH",
        session_id: "stale-expired-session",
        payload: { blockId: "hero-1", proposedValue: "Hacked" },
      },
    });

    assert.equal(patchProposePayload, null);
  });

  test("WebsiteBridgeClient drops oversized messages exceeding max payload limit", () => {
    let selectCalled = false;

    const mockIframe = { contentWindow: {} };
    const client = new WebsiteBridgeClient({
      iframe: mockIframe,
      maxPayloadBytes: 1024, // 1 KB limit
      onElementSelect: () => { selectCalled = true; },
    });

    client["status"] = "connected";
    client["activeSessionId"] = "active-sess-1";

    const oversizedPayload = {
      type: "OD_ELEMENT_SELECT",
      session_id: "active-sess-1",
      payload: { data: "X".repeat(2048) },
    };

    client["handlePortMessage"]({ data: oversizedPayload });
    assert.equal(selectCalled, false);
  });

  test("validateProposedPatchAgainstDraft enforces session ID matching and rejection of stale sessions", () => {
    const draft = createDraft();

    // 1. Missing session ID in payload
    const missingSess = validateProposedPatchAgainstDraft(
      {
        sessionId: "",
        blockId: "hero-1",
        blockType: "hero",
        field: "headline",
        proposedValue: "Test",
      },
      draft,
      "expected-sess"
    );
    assert.equal(missingSess.isValid, false);
    assert.match(missingSess.error || "", /session identifier is missing/i);

    // 2. Stale session ID
    const staleSess = validateProposedPatchAgainstDraft(
      {
        sessionId: "stale-sess-123",
        blockId: "hero-1",
        blockType: "hero",
        field: "headline",
        proposedValue: "Test",
      },
      draft,
      "expected-sess"
    );
    assert.equal(staleSess.isValid, false);
    assert.match(staleSess.error || "", /session mismatch or stale/i);
  });

  test("rejects inspector-only fields and unauthorized fields from inline proposals", () => {
    const draft = createDraft();

    const inspectorOnlyCases = [
      { blockId: "hero-1", blockType: "hero", field: "alignment" },
      { blockId: "hero-1", blockType: "hero", field: "background_image_url" },
      { blockId: "hero-1", blockType: "hero", field: "cta_button.target_url" },
      { blockId: "gallery-1", blockType: "image_gallery", field: "images.image_url", itemIndex: 0 },
      { blockId: "gallery-1", blockType: "image_gallery", field: "images.alt_text", itemIndex: 0 },
    ];

    for (const c of inspectorOnlyCases) {
      const res = validateProposedPatchAgainstDraft(
        {
          sessionId: "s1",
          blockId: c.blockId,
          blockType: c.blockType,
          field: c.field,
          itemIndex: c.itemIndex ?? null,
          proposedValue: "test",
        },
        draft,
        "s1"
      );
      assert.equal(res.isValid, false);
      assert.match(res.error || "", /inspector-only property/i);
    }

    // Prototype pollution / arbitrary fields
    const arbitraryRes = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "hero-1",
        blockType: "hero",
        field: "__proto__",
        proposedValue: "test",
      },
      draft,
      "s1"
    );
    assert.equal(arbitraryRes.isValid, false);

    // Custom section non-html field
    const secRes = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "custom-1",
        blockType: "open_design_section",
        field: "section_id",
        proposedValue: "test",
      },
      draft,
      "s1"
    );
    assert.equal(secRes.isValid, false);
  });

  test("strictly enforces array index integrity and rejects out-of-bounds or non-numeric indexes", () => {
    const draft = createDraft();

    // 1. Array index on scalar field is forbidden
    const scalarRes = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "hero-1",
        blockType: "hero",
        field: "headline",
        itemIndex: 0,
        proposedValue: "Test",
      },
      draft,
      "s1"
    );
    assert.equal(scalarRes.isValid, false);
    assert.match(scalarRes.error || "", /itemindex is not permitted for scalar/i);

    // 2. Missing array index on array field is forbidden
    const missingIdxRes = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "features-1",
        blockType: "features_grid",
        field: "items.title",
        itemIndex: null,
        proposedValue: "Test",
      },
      draft,
      "s1"
    );
    assert.equal(missingIdxRes.isValid, false);
    assert.match(missingIdxRes.error || "", /itemindex is required/i);

    // 3. Out-of-bounds index
    const oobRes = validateProposedPatchAgainstDraft(
      {
        sessionId: "s1",
        blockId: "features-1",
        blockType: "features_grid",
        field: "items.title",
        itemIndex: 99,
        proposedValue: "Test",
      },
      draft,
      "s1"
    );
    assert.equal(oobRes.isValid, false);
    assert.match(oobRes.error || "", /out of bounds/i);

    // 4. Negative and floating-point indexes
    for (const badIdx of [-1, 1.5, NaN]) {
      const badIdxRes = validateProposedPatchAgainstDraft(
        {
          sessionId: "s1",
          blockId: "features-1",
          blockType: "features_grid",
          field: "items.title",
          itemIndex: badIdx,
          proposedValue: "Test",
        },
        draft,
        "s1"
      );
      assert.equal(badIdxRes.isValid, false);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Suite D: Persistence & Live Ad Serving Protection
// ─────────────────────────────────────────────────────────────────────────────

describe("Open Design Stage 3.5 — D. Persistence Protection", () => {
  test("rejected proposals never mutate the active draft state", () => {
    const draft = createDraft();
    const beforeSnapshot = JSON.stringify(draft);

    const maliciousProposals = [
      { sessionId: "s1", blockId: "hero-1", blockType: "hero", field: "headline", proposedValue: "<script>alert(1)</script>" },
      { sessionId: "s1", blockId: "hero-1", blockType: "hero", field: "background_image_url", proposedValue: "https://evil.com" },
      { sessionId: "wrong-s", blockId: "hero-1", blockType: "hero", field: "headline", proposedValue: "New Headline" },
      { sessionId: "s1", blockId: "ad-1", blockType: "ad_placement", field: "placement_location", proposedValue: "footer" },
      { sessionId: "s1", blockId: "features-1", blockType: "features_grid", field: "items.title", itemIndex: 100, proposedValue: "New" },
      { sessionId: "s1", blockId: "custom-1", blockType: "open_design_section", field: "html_content", patchKind: "native_field", proposedValue: "Bad" },
    ];

    for (const proposal of maliciousProposals) {
      const res = validateProposedPatchAgainstDraft(proposal, draft, "s1");
      assert.equal(res.isValid, false);
      assert.equal(JSON.stringify(draft), beforeSnapshot, "Draft must not be mutated on failure");
    }
  });

  test("applyPatchToBlock returns block unmodified for ad placement or unrecognized fields", () => {
    const adBlock = { id: "ad-1", type: "ad_placement", content: { placement_location: "header_banner" } };
    const patchedAd = applyPatchToBlock(adBlock, "placement_location", null, "sidebar");
    assert.deepEqual(patchedAd, adBlock);

    const heroBlock = { id: "hero-1", type: "hero", content: { headline: "Safe" } };
    const patchedHero = applyPatchToBlock(heroBlock, "malicious_field", null, "value");
    assert.deepEqual(patchedHero, heroBlock);
  });

  test("syncBlockChangesToPreview ignores ad placements and non-scalar fields", () => {
    let synced = false;
    const mockClient = {
      getStatus: () => "connected",
      syncValue: () => { synced = true; },
    };

    const prevAd = { id: "ad-1", type: "ad_placement", content: { placement_location: "header_banner" } };
    const nextAd = { id: "ad-1", type: "ad_placement", content: { placement_location: "sidebar" } };

    const result = syncBlockChangesToPreview(mockClient, prevAd, nextAd);
    assert.equal(result, false);
    assert.equal(synced, false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Suite E: Sanitizer Parity & Consistency
// ─────────────────────────────────────────────────────────────────────────────

describe("Open Design Stage 3.5 — E. Sanitizer Consistency", () => {
  test("frontend sanitizer rejects all forbidden script tags and event handlers", () => {
    const dangerousSnippets = [
      '<script>alert(1)</script>',
      '<SCRIPT SRC="//evil.com/xss.js"></SCRIPT>',
      '<script type="text/javascript">foo()</script>',
      '<div onclick="alert(1)">Click</div>',
      '<img src="x" onerror="alert(1)">',
      '<a onmouseover="alert(1)">Hover</a>',
      '<div oN cLiCk = "alert(1)">Spaces in handler</div>',
      '<span ONFOCUS=alert(1)>Focus</span>',
    ];

    for (const snippet of dangerousSnippets) {
      const res = sanitizeAndValidateOpenDesignHtml(snippet);
      assert.equal(res.isValid, false, `Should reject dangerous snippet: ${snippet}`);
    }
  });

  test("frontend sanitizer rejects dangerous URL schemes and spoofed platform classes", () => {
    const dangerousSchemesAndSpoofs = [
      '<a href="javascript:alert(1)">Link</a>',
      '<a href=" JAVASCRIPT:alert(1) ">Link with whitespace</a>',
      '<a href="vbscript:msgbox(1)">VBScript</a>',
      '<a href="data:text/html;base64,PHNjcmlwdD4=">Data HTML</a>',
      '<div class="block-ad-placement">Spoofed ad</div>',
      '<div class=" BLOCK-AD-PLACEMENT ">Upper spoof</div>',
      '<div class="rivinity-ad-placeholder">Placeholder spoof</div>',
      '<div class="rivinity-live-ad-container">Live container spoof</div>',
      '<div class="rivinity-ad-slot">Ad slot spoof</div>',
      '<div id="rivinity-ad-slot">ID spoof</div>',
      '<div id="ad_placement">Ad placement ID spoof</div>',
    ];

    for (const snippet of dangerousSchemesAndSpoofs) {
      const res = sanitizeAndValidateOpenDesignHtml(snippet);
      assert.equal(res.isValid, false, `Should reject: ${snippet}`);
    }
  });

  test("frontend sanitizer rejects forged data-od-* and data-slot attributes", () => {
    const forgedAttributes = [
      '<div data-od-block-id="hero-1">Forged block ID</div>',
      '<div data-od-block-type="hero">Forged block type</div>',
      '<div data-od-protected="false">Forged protection</div>',
      '<div data-od-editable="true">Forged editable</div>',
      '<div data-slot="ad-123">Forged slot</div>',
      '<div data-format="1">Forged format</div>',
      '<div data-rivinity="platform">Forged platform</div>',
      '<div data-ad="true">Forged ad</div>',
    ];

    for (const snippet of forgedAttributes) {
      const res = sanitizeAndValidateOpenDesignHtml(snippet);
      assert.equal(res.isValid, false, `Should reject forged attribute: ${snippet}`);
    }
  });

  test("frontend sanitizer rejects forbidden tags and oversized HTML", () => {
    const forbiddenTags = [
      "iframe", "object", "embed", "style", "link", "form", "input",
      "button", "textarea", "select", "meta", "base", "svg", "canvas", "video", "audio",
    ];

    for (const tag of forbiddenTags) {
      const snippet = `<${tag}>Forbidden</${tag}>`;
      const res = sanitizeAndValidateOpenDesignHtml(snippet);
      assert.equal(res.isValid, false, `Should reject tag: <${tag}>`);
    }

    // Oversized > 64 KB
    const largeHtml = "<div>" + "A".repeat(65540) + "</div>";
    const sizeRes = sanitizeAndValidateOpenDesignHtml(largeHtml);
    assert.equal(sizeRes.isValid, false);
    assert.match(sizeRes.error || "", /maximum allowed size/i);
  });

  test("frontend sanitizer accepts clean valid semantic markup", () => {
    const cleanSnippet = (
      '<div class="safe-card">' +
      '<h2 class="title">Clean Heading</h2>' +
      '<p>Text with <strong>bold</strong> and <em>italic</em>.</p>' +
      '<a href="https://example.com" target="_blank" rel="noopener noreferrer">Valid Link</a>' +
      '</div>'
    );
    const res = sanitizeAndValidateOpenDesignHtml(cleanSnippet);
    assert.equal(res.isValid, true);
    assert.ok(res.sanitizedHtml);
    assert.match(res.sanitizedHtml, /Clean Heading/);
  });
});
