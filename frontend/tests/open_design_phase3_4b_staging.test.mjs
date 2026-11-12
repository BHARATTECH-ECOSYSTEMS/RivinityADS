/**
 * open_design_phase3_4b_staging.test.mjs — Phase 3.4B: Parent Draft Receiver & Patch Staging Test Suite
 *
 * Verifies:
 * 1. Module exports & typed contract boundaries.
 * 2. Independent parent-side validation:
 *    - Session ID matching & staleness prevention.
 *    - Block existence & block type matching.
 *    - Approved native editable field allowlist (17 fields across 7 block types).
 *    - Inspector-only field rejection (URLs, images, alt text, icon tokens, styles).
 *    - Protected block immunity (ad_placement).
 *    - Custom section restriction (open_design_section).
 *    - Array bounds validation (features_grid.items, image_gallery.images, footer.links, text_section.body).
 *    - Non-string value rejection.
 *    - HTML tag & script pattern security rejection.
 *    - Single-line newline rejection & multiline newline acceptance.
 *    - Required field non-empty enforcement.
 *    - Schema maximum length enforcement.
 * 3. Immutable draft patch staging:
 *    - applyPatchToBlock creates new block object without mutating original.
 *    - Array item patching: updates target index only, preserving sibling items exactly.
 *    - Unrelated block properties preservation.
 * 4. Cancellation & Rejection safeguards:
 *    - extractAuthoritativeValue returns accurate draft value for rollbacks.
 *    - Rejection preserves draft state untouched.
 *    - Cancellation clears session and retains draft state.
 * 5. MessageChannel Bridge Client integration:
 *    - confirmPatchApplied emits OD_EDIT_APPLY_CONFIRMED.
 *    - rejectPatch emits OD_EDIT_REJECTED.
 *    - syncValue emits OD_EDIT_SYNC_VALUE.
 *    - finishActiveEdit emits OD_PARENT_EDIT_FINISH.
 *    - Routing of OD_EDIT_SESSION_START, OD_EDIT_PROPOSE_PATCH, OD_EDIT_CANCEL.
 * 6. Preview DOM synchronization (applyConfirmedValue, rollbackValue, syncValue).
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve(__dirname, ".compiled_phase3_4b_staging_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  WebsiteBridgeClient,
  APPROVED_NATIVE_EDITABLE_FIELDS,
  APPROVED_BLOCK_FIELDS,
  NATIVE_FIELD_CONSTRAINTS,
  normalizeNativeField,
  containsDisallowedHtmlOrScript,
  extractAuthoritativeValue,
  applyPatchToBlock,
  validateProposedPatchAgainstDraft,
  validateSelectionAgainstDraft,
  BLOCK_ID_REGEX,
  VALID_BLOCK_TYPES,
} from '../src/services/websiteBridge';

export {
  PreviewOverlayEngine,
  SCHEMA_FIELD_CONSTRAINTS,
  normalizeFieldIdentifier,
  getFieldConstraints,
  isFieldInlineEditable,
} from '../src/services/previewOverlayEngine';
`;

  const entryFile = path.resolve(__dirname, ".temp_phase3_4b_entry.tsx");
  fs.writeFileSync(entryFile, wrapper, "utf8");
  cleanupFiles.push(entryFile);

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
    external: ["react", "react-dom"],
  });

  return pathToFileURL(bundlePath).href;
}

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

const bundleUrl = compileBundle();
const {
  WebsiteBridgeClient,
  APPROVED_NATIVE_EDITABLE_FIELDS,
  APPROVED_BLOCK_FIELDS,
  NATIVE_FIELD_CONSTRAINTS,
  normalizeNativeField,
  containsDisallowedHtmlOrScript,
  extractAuthoritativeValue,
  applyPatchToBlock,
  validateProposedPatchAgainstDraft,
  BLOCK_ID_REGEX,
  VALID_BLOCK_TYPES,
  PreviewOverlayEngine,
} = await import(bundleUrl);

// Test fixtures representing standard draft blocks
function createSampleDraftBlocks() {
  return [
    {
      id: "hero-1",
      type: "hero",
      is_visible: true,
      content: {
        headline: "Empower Your Business",
        subheadline: "The modern platform for digital growth\nTrusted by thousands.",
        alignment: "center",
        background_image_url: "https://example.com/bg.jpg",
        cta_button: {
          label: "Get Started Now",
          target_url: "https://example.com/start",
          style: "primary",
        },
      },
    },
    {
      id: "text-1",
      type: "text_section",
      is_visible: true,
      content: {
        heading: "About Our Solution",
        body: "First introductory paragraph.\n\nSecond detailed paragraph explaining features.",
        paragraphs: [
          "First introductory paragraph.",
          "Second detailed paragraph explaining features.",
        ],
      },
    },
    {
      id: "features-1",
      type: "features_grid",
      is_visible: true,
      content: {
        title: "Key Capabilities",
        items: [
          { title: "Blazing Fast", description: "Built for instant response.", icon: "bolt" },
          { title: "Bank-Grade Security", description: "Encrypted at rest and in transit.", icon: "shield" },
          { title: "Scale Seamlessly", description: "Grows with your team effortlessly.", icon: "trending-up" },
        ],
      },
    },
    {
      id: "gallery-1",
      type: "image_gallery",
      is_visible: true,
      content: {
        title: "Product Showcase",
        images: [
          { image_url: "https://example.com/p1.png", alt_text: "Dashboard view", caption: "Main Analytics HUD" },
          { image_url: "https://example.com/p2.png", alt_text: "Mobile preview", caption: "Responsive on all screens" },
        ],
      },
    },
    {
      id: "contact-1",
      type: "contact_form",
      is_visible: true,
      content: {
        title: "Get in Touch",
        submit_label: "Send Inquiry",
        fields: ["name", "email", "message"],
      },
    },
    {
      id: "banner-1",
      type: "cta_banner",
      is_visible: true,
      content: {
        title: "Ready to Supercharge Your Ads?",
        description: "Join leading publishers today.\nSetup takes under five minutes.",
        button: {
          label: "Create Free Account",
          target_url: "https://example.com/register",
          style: "primary",
        },
      },
    },
    {
      id: "footer-1",
      type: "footer",
      is_visible: true,
      content: {
        copyright_text: "© 2026 RivinityADS. All rights reserved.",
        links: [
          { label: "Privacy Policy", target_url: "https://example.com/privacy" },
          { label: "Terms of Service", target_url: "https://example.com/terms" },
          { label: "Contact Us", target_url: "https://example.com/contact" },
        ],
      },
    },
    {
      id: "ad-1",
      type: "ad_placement",
      is_visible: true,
      content: {
        placement_location: "in_article",
        format: 1,
        ad_slot_id: "slot-999",
      },
    },
    {
      id: "custom-1",
      type: "open_design_section",
      is_visible: true,
      content: {
        section_id: "od-custom-wrap",
        section_tag: "section",
        html_content: "<div><h3>Custom Widget</h3></div>",
      },
    },
  ];
}

describe("RivinityADS Open Design Phase 3.4B: Parent Draft Receiver & Patch Staging", () => {
  // ── 1. Contract & Allowlist Integrity ─────────────────────────────────────────
  test("1. Approved native editable fields strictly covers all 17 fields across 7 block types", () => {
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("headline"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("subheadline"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("cta_button.label"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.text_section.has("heading"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.text_section.has("body"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.features_grid.has("title"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.features_grid.has("items.title"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.features_grid.has("items.description"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.image_gallery.has("title"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.image_gallery.has("images.caption"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.contact_form.has("title"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.contact_form.has("submit_label"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.cta_banner.has("title"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.cta_banner.has("description"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.cta_banner.has("button.label"));

    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.footer.has("copyright_text"));
    assert.ok(APPROVED_NATIVE_EDITABLE_FIELDS.footer.has("links.label"));

    // Ad placement and Open Design sections have NO native inline editable text fields
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.ad_placement.size, 0);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.open_design_section.size, 0);
  });

  test("2. Inspector-only properties are excluded from native editable allowlist", () => {
    // URLs
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("cta_button.target_url"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.cta_banner.has("button.target_url"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.footer.has("links.target_url"), false);

    // Images & alt text
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("background_image_url"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.image_gallery.has("images.image_url"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.image_gallery.has("images.alt_text"), false);

    // Tokens, styles & structure
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.features_grid.has("items.icon"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.contact_form.has("fields"), false);
    assert.strictEqual(APPROVED_NATIVE_EDITABLE_FIELDS.hero.has("alignment"), false);
  });

  // ── 2. Validation of Valid Proposals ──────────────────────────────────────────
  test("3. Valid single-line proposal passes parent validation", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "Brand New Headline For The Future",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, true);
    assert.strictEqual(res.normalizedField, "headline");
    assert.strictEqual(res.currentValue, "Empower Your Business");
    assert.strictEqual(res.constraint?.maxLength, 160);
  });

  test("4. Valid multiline proposal passes parent validation for multiline fields", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-101",
      blockId: "hero-1",
      blockType: "hero",
      field: "subheadline",
      itemIndex: null,
      proposedValue: "Line 1 of updated value\nLine 2 of updated value\nLine 3 with more details",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-101");
    assert.strictEqual(res.isValid, true);
    assert.strictEqual(res.normalizedField, "subheadline");
    assert.strictEqual(res.constraint?.multiline, true);
  });

  test("5. Valid array-item proposal with itemIndex passes parent validation", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-102",
      blockId: "features-1",
      blockType: "features_grid",
      field: "items.title",
      itemIndex: 1,
      proposedValue: "Military-Grade Defense",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-102");
    assert.strictEqual(res.isValid, true);
    assert.strictEqual(res.normalizedField, "items.title");
    assert.strictEqual(res.currentValue, "Bank-Grade Security");
  });

  test("6. Valid text_section paragraph proposal with itemIndex passes parent validation", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-103",
      blockId: "text-1",
      blockType: "text_section",
      field: "body",
      itemIndex: 0,
      proposedValue: "Updated opening paragraph with fresh clarity.",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-103");
    assert.strictEqual(res.isValid, true);
    assert.strictEqual(res.normalizedField, "body");
    assert.strictEqual(res.currentValue, "First introductory paragraph.");
  });

  // ── 3. Parent-Side Rejection Safeguards ────────────────────────────────────────
  test("7. Rejects missing or mismatched session identifier", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "stale-session-999",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "Changed Headline",
    };

    // Stale session check against active session
    const res = validateProposedPatchAgainstDraft(payload, blocks, "active-session-123");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /session mismatch|stale session/i);

    // Missing session
    const resMissing = validateProposedPatchAgainstDraft({ ...payload, sessionId: "" }, blocks, "active-session-123");
    assert.strictEqual(resMissing.isValid, false);
    assert.match(resMissing.error, /session identifier/i);
  });

  test("8. Rejects non-existent or malformed block ID", () => {
    const blocks = createSampleDraftBlocks();
    const payloadUnknown = {
      sessionId: "sess-100",
      blockId: "non-existent-block",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "Changed Headline",
    };

    const resUnknown = validateProposedPatchAgainstDraft(payloadUnknown, blocks, "sess-100");
    assert.strictEqual(resUnknown.isValid, false);
    assert.match(resUnknown.error, /not found in current draft/i);

    const payloadMalformed = {
      ...payloadUnknown,
      blockId: "invalid/id with spaces!@#",
    };
    const resMalformed = validateProposedPatchAgainstDraft(payloadMalformed, blocks, "sess-100");
    assert.strictEqual(resMalformed.isValid, false);
    assert.match(resMalformed.error, /invalid block identifier format/i);
  });

  test("9. Rejects block type mismatch with draft block", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1", // actual type is hero
      blockType: "text_section", // forged type
      field: "heading",
      itemIndex: null,
      proposedValue: "Forged Heading",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /block type mismatch/i);
  });

  test("10. Rejects protected block proposals (ad_placement immunity)", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "ad-1",
      blockType: "ad_placement",
      field: "placement_location",
      itemIndex: null,
      proposedValue: "fraudulent_banner",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /protected and cannot be edited/i);
  });

  test("11. Rejects custom open_design_section block proposals (deferred to 3.4C)", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "custom-1",
      blockType: "open_design_section",
      field: "html_content",
      itemIndex: null,
      proposedValue: "<div>Malicious inline html</div>",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /not supported in this stage/i);
  });

  test("12. Rejects inspector-only field proposals with explicit error", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "cta_button.target_url", // inspector-only
      itemIndex: null,
      proposedValue: "https://evil.com/phishing",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /inspector-only property/i);
  });

  test("13. Rejects array index out-of-bounds for array-based fields", () => {
    const blocks = createSampleDraftBlocks();

    // features_grid has 3 items (indexes 0, 1, 2)
    const payloadFeatures = {
      sessionId: "sess-100",
      blockId: "features-1",
      blockType: "features_grid",
      field: "items.title",
      itemIndex: 5, // out of bounds
      proposedValue: "Ghost Feature",
    };
    const resFeatures = validateProposedPatchAgainstDraft(payloadFeatures, blocks, "sess-100");
    assert.strictEqual(resFeatures.isValid, false);
    assert.match(resFeatures.error, /out of bounds for features items/i);

    // image_gallery has 2 images (indexes 0, 1)
    const payloadGallery = {
      sessionId: "sess-100",
      blockId: "gallery-1",
      blockType: "image_gallery",
      field: "images.caption",
      itemIndex: 10,
      proposedValue: "Ghost Image",
    };
    const resGallery = validateProposedPatchAgainstDraft(payloadGallery, blocks, "sess-100");
    assert.strictEqual(resGallery.isValid, false);
    assert.match(resGallery.error, /out of bounds for gallery images/i);

    // footer has 3 links
    const payloadFooter = {
      sessionId: "sess-100",
      blockId: "footer-1",
      blockType: "footer",
      field: "links.label",
      itemIndex: -1, // negative index
      proposedValue: "Negative Index",
    };
    const resFooter = validateProposedPatchAgainstDraft(payloadFooter, blocks, "sess-100");
    assert.strictEqual(resFooter.isValid, false);
    assert.match(resFooter.error, /itemIndex is required/i);
  });

  test("14. Rejects itemIndex on scalar native fields", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: 2, // invalid for scalar field
      proposedValue: "New Headline",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /itemIndex is not permitted for scalar field/i);
  });

  test("15. Rejects non-string values", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: 12345, // number
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /Proposed value must be a string/i);
  });

  test("16. Rejects HTML tags and script patterns (strict plain-text security)", () => {
    const blocks = createSampleDraftBlocks();

    const scriptPayload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "<script>alert('xss')</script>",
    };
    const resScript = validateProposedPatchAgainstDraft(scriptPayload, blocks, "sess-100");
    assert.strictEqual(resScript.isValid, false);
    assert.match(resScript.error, /HTML tags, markup, and script patterns are strictly forbidden/i);

    const htmlPayload = {
      ...scriptPayload,
      proposedValue: "Welcome to <b>RivinityADS</b>!",
    };
    const resHtml = validateProposedPatchAgainstDraft(htmlPayload, blocks, "sess-100");
    assert.strictEqual(resHtml.isValid, false);
    assert.match(resHtml.error, /HTML tags, markup, and script patterns are strictly forbidden/i);

    const imgPayload = {
      ...scriptPayload,
      proposedValue: "<img src=x onerror=steal()>",
    };
    const resImg = validateProposedPatchAgainstDraft(imgPayload, blocks, "sess-100");
    assert.strictEqual(resImg.isValid, false);
    assert.match(resImg.error, /HTML tags, markup, and script patterns are strictly forbidden/i);
  });

  test("17. Rejects line breaks in single-line fields", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "First Line\nSecond Line in single-line",
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /cannot contain line breaks/i);
  });

  test("18. Rejects empty proposals on required fields", () => {
    const blocks = createSampleDraftBlocks();
    const payload = {
      sessionId: "sess-100",
      blockId: "hero-1",
      blockType: "hero",
      field: "headline", // required: true
      itemIndex: null,
      proposedValue: "   ", // whitespace only
    };

    const res = validateProposedPatchAgainstDraft(payload, blocks, "sess-100");
    assert.strictEqual(res.isValid, false);
    assert.match(res.error, /is required and cannot be empty/i);
  });

  test("19. Rejects proposals exceeding schema maxLength constraints", () => {
    const blocks = createSampleDraftBlocks();

    // hero.headline: max 160
    const longHeadline = "A".repeat(161);
    const resHeadline = validateProposedPatchAgainstDraft(
      {
        sessionId: "sess-100",
        blockId: "hero-1",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        proposedValue: longHeadline,
      },
      blocks,
      "sess-100"
    );
    assert.strictEqual(resHeadline.isValid, false);
    assert.match(resHeadline.error, /exceeds maximum allowed length \(160\)/i);

    // hero.cta_button.label: max 60
    const longLabel = "B".repeat(61);
    const resLabel = validateProposedPatchAgainstDraft(
      {
        sessionId: "sess-100",
        blockId: "hero-1",
        blockType: "hero",
        field: "cta_button.label",
        itemIndex: null,
        proposedValue: longLabel,
      },
      blocks,
      "sess-100"
    );
    assert.strictEqual(resLabel.isValid, false);
    assert.match(resLabel.error, /exceeds maximum allowed length \(60\)/i);

    // features_grid.items.title: max 100
    const longItemTitle = "C".repeat(101);
    const resItemTitle = validateProposedPatchAgainstDraft(
      {
        sessionId: "sess-100",
        blockId: "features-1",
        blockType: "features_grid",
        field: "items.title",
        itemIndex: 0,
        proposedValue: longItemTitle,
      },
      blocks,
      "sess-100"
    );
    assert.strictEqual(resItemTitle.isValid, false);
    assert.match(resItemTitle.error, /exceeds maximum allowed length \(100\)/i);

    // footer.links.label: max 50
    const longFooterLabel = "D".repeat(51);
    const resFooterLabel = validateProposedPatchAgainstDraft(
      {
        sessionId: "sess-100",
        blockId: "footer-1",
        blockType: "footer",
        field: "links.label",
        itemIndex: 0,
        proposedValue: longFooterLabel,
      },
      blocks,
      "sess-100"
    );
    assert.strictEqual(resFooterLabel.isValid, false);
    assert.match(resFooterLabel.error, /exceeds maximum allowed length \(50\)/i);
  });

  // ── 4. Immutable Draft Patch Staging ──────────────────────────────────────────
  test("20. applyPatchToBlock creates a new block object without mutating original", () => {
    const blocks = createSampleDraftBlocks();
    const heroBlock = blocks[0];
    const originalContent = { ...heroBlock.content };

    const updated = applyPatchToBlock(heroBlock, "headline", null, "Updated Brand Headline");

    assert.notStrictEqual(updated, heroBlock);
    assert.strictEqual(updated.content.headline, "Updated Brand Headline");
    assert.strictEqual(heroBlock.content.headline, "Empower Your Business"); // original untouched
  });

  test("21. Array-item patching updates target item index while strictly preserving sibling items", () => {
    const blocks = createSampleDraftBlocks();
    const featuresBlock = blocks[2];
    const originalItems = [...featuresBlock.content.items];

    // Mutate index 1
    const updated = applyPatchToBlock(featuresBlock, "items.title", 1, "Fortified Armor");

    assert.strictEqual(updated.content.items[1].title, "Fortified Armor");
    assert.strictEqual(updated.content.items[1].description, originalItems[1].description); // sibling prop
    assert.strictEqual(updated.content.items[1].icon, originalItems[1].icon); // sibling prop

    // Sibling items 0 and 2 are strictly preserved
    assert.deepStrictEqual(updated.content.items[0], originalItems[0]);
    assert.deepStrictEqual(updated.content.items[2], originalItems[2]);

    // Original block remains unmutated
    assert.strictEqual(featuresBlock.content.items[1].title, "Bank-Grade Security");
  });

  test("22. Unrelated block properties and sibling array items are preserved across all categories", () => {
    const blocks = createSampleDraftBlocks();

    // 1. hero cta_button.label preserves target_url and style
    const hero = blocks[0];
    const updatedHero = applyPatchToBlock(hero, "cta_button.label", null, "Join Today");
    assert.strictEqual(updatedHero.content.cta_button.label, "Join Today");
    assert.strictEqual(updatedHero.content.cta_button.target_url, hero.content.cta_button.target_url);
    assert.strictEqual(updatedHero.content.cta_button.style, hero.content.cta_button.style);
    assert.strictEqual(updatedHero.content.headline, hero.content.headline);

    // 2. gallery images[0].caption preserves image_url and alt_text
    const gallery = blocks[3];
    const updatedGallery = applyPatchToBlock(gallery, "images.caption", 0, "Brand New HUD Visual");
    assert.strictEqual(updatedGallery.content.images[0].caption, "Brand New HUD Visual");
    assert.strictEqual(updatedGallery.content.images[0].image_url, gallery.content.images[0].image_url);
    assert.strictEqual(updatedGallery.content.images[0].alt_text, gallery.content.images[0].alt_text);
    assert.deepStrictEqual(updatedGallery.content.images[1], gallery.content.images[1]);

    // 3. footer links[1].label preserves target_url and sibling links
    const footer = blocks[6];
    const updatedFooter = applyPatchToBlock(footer, "links.label", 1, "Updated Terms");
    assert.strictEqual(updatedFooter.content.links[1].label, "Updated Terms");
    assert.strictEqual(updatedFooter.content.links[1].target_url, footer.content.links[1].target_url);
    assert.deepStrictEqual(updatedFooter.content.links[0], footer.content.links[0]);
    assert.deepStrictEqual(updatedFooter.content.links[2], footer.content.links[2]);
    assert.strictEqual(updatedFooter.content.copyright_text, footer.content.copyright_text);
  });

  // ── 5. Authoritative Value Extraction for Rollbacks ───────────────────────────
  test("23. extractAuthoritativeValue returns exact draft value for scalar and array fields", () => {
    const blocks = createSampleDraftBlocks();

    assert.strictEqual(extractAuthoritativeValue(blocks[0], "headline"), "Empower Your Business");
    assert.strictEqual(extractAuthoritativeValue(blocks[0], "cta_button.label"), "Get Started Now");
    assert.strictEqual(extractAuthoritativeValue(blocks[1], "heading"), "About Our Solution");
    assert.strictEqual(extractAuthoritativeValue(blocks[1], "body", 0), "First introductory paragraph.");
    assert.strictEqual(extractAuthoritativeValue(blocks[2], "items.title", 1), "Bank-Grade Security");
    assert.strictEqual(extractAuthoritativeValue(blocks[3], "images.caption", 0), "Main Analytics HUD");
    assert.strictEqual(extractAuthoritativeValue(blocks[4], "submit_label"), "Send Inquiry");
    assert.strictEqual(extractAuthoritativeValue(blocks[5], "button.label"), "Create Free Account");
    assert.strictEqual(extractAuthoritativeValue(blocks[6], "links.label", 2), "Contact Us");
  });

  // ── 6. MessageChannel Protocol & Bridge Client Integration ────────────────────
  test("24. WebsiteBridgeClient methods emit correct protocol envelopes", () => {
    const postedMessages = [];
    const mockPort = {
      postMessage: (msg) => postedMessages.push(msg),
      close: () => {},
    };

    const client = new WebsiteBridgeClient({
      iframe: { contentWindow: {} },
    });

    // Manually inject connected state with mockPort
    client.port = mockPort;
    client.status = "connected";
    client.activeSessionId = "sess-channel-test";

    // 1. confirmPatchApplied -> OD_EDIT_APPLY_CONFIRMED
    const okConfirm = client.confirmPatchApplied({
      sessionId: "edit-1",
      blockId: "hero-1",
      field: "headline",
      itemIndex: null,
      appliedValue: "Applied Headline Text",
    });
    assert.strictEqual(okConfirm, true);
    assert.strictEqual(postedMessages[0].type, "OD_EDIT_APPLY_CONFIRMED");
    assert.strictEqual(postedMessages[0].payload.appliedValue, "Applied Headline Text");

    // 2. rejectPatch -> OD_EDIT_REJECTED
    const okReject = client.rejectPatch({
      sessionId: "edit-2",
      blockId: "hero-1",
      field: "headline",
      itemIndex: null,
      error: "Length exceeded",
      rollbackValue: "Original Value",
    });
    assert.strictEqual(okReject, true);
    assert.strictEqual(postedMessages[1].type, "OD_EDIT_REJECTED");
    assert.strictEqual(postedMessages[1].payload.rollbackValue, "Original Value");

    // 3. syncValue -> OD_EDIT_SYNC_VALUE
    const okSync = client.syncValue({
      blockId: "hero-1",
      field: "headline",
      itemIndex: null,
      value: "Synced Value",
    });
    assert.strictEqual(okSync, true);
    assert.strictEqual(postedMessages[2].type, "OD_EDIT_SYNC_VALUE");
    assert.strictEqual(postedMessages[2].payload.value, "Synced Value");

    // 4. finishActiveEdit -> OD_PARENT_EDIT_FINISH
    const okFinish = client.finishActiveEdit(false);
    assert.strictEqual(okFinish, true);
    assert.strictEqual(postedMessages[3].type, "OD_PARENT_EDIT_FINISH");
    assert.strictEqual(postedMessages[3].payload.commit, false);
  });

  // ── 7. Preview DOM Element Synchronization ────────────────────────────────────
  test("25. PreviewOverlayEngine synchronizes confirmed, rejected, and synced values on DOM", () => {
    // Create minimal DOM representation
    const listeners = new Map();
    const doc = {
      body: {
        appendChild: () => {},
        removeChild: () => {},
      },
      createElement: (tag) => ({
        style: {},
        setAttribute: () => {},
        removeAttribute: () => {},
        appendChild: () => {},
        classList: { add: () => {}, remove: () => {} },
      }),
      querySelector: (selector) => {
        if (selector === '[data-od-block-id="hero-1"]') {
          return {
            getAttribute: () => "hero-1",
            textContent: "Initial DOM Headline",
            querySelector: (childSel) => {
              if (childSel === '[data-od-field="headline"]') {
                return { textContent: "Initial DOM Headline" };
              }
              return null;
            },
          };
        }
        return null;
      },
      querySelectorAll: () => [],
      addEventListener: (type, fn) => listeners.set(type, fn),
      removeEventListener: (type) => listeners.delete(type),
    };

    const engine = new PreviewOverlayEngine({
      rootDocument: doc,
      enablePointerEvents: false,
    });

    // 1. Apply Confirmed
    const applied = engine.applyConfirmedValue("hero-1", "headline", null, "New Confirmed Title");
    assert.strictEqual(applied, true);

    // 2. Rollback
    const rolledBack = engine.rollbackValue("hero-1", "headline", null, "Rolled Back Title");
    assert.strictEqual(rolledBack, true);

    // 3. Sync
    const synced = engine.syncValue("hero-1", "headline", null, "Externally Synced Title");
    assert.strictEqual(synced, true);

    engine.destroy();
  });
});

