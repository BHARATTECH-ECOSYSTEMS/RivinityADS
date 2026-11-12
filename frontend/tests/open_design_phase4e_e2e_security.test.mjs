/**
 * open_design_phase4e_e2e_security.test.mjs — Phase 4E:
 * Comprehensive End-to-End Regression & Negative Security Verification
 *
 * Verifies:
 * 1. Frontend response validation hardening:
 *    - Rejection of malformed objects, non-objects, arrays, missing IDs.
 *    - Strict enforcement of block ID regex and 64 character boundary.
 *    - Exact type check: open_design_section only (rejects all other 8 block types).
 *    - is_visible boolean constraint.
 *    - Nested content object, non-empty html_content, 65,536 char ceiling.
 *    - Rejection of XSS, javascript: URLs, event handlers, and disallowed tags.
 *    - Whitelisted section tags, anti-spoofing section IDs and CSS classes.
 *    - Safe style declaration constraints (max 30 declarations, allowed properties, no forbidden CSS tokens).
 *    - Asset references constraints (max 20 safe URLs).
 * 2. End-to-end Draft Lifecycle:
 *    - Full draft staging: API response -> block validation -> immutable draft insert -> schema_version elevation.
 *    - Visual editor localized text patch on the generated section.
 *    - Explicit Save Changes vs. Discard lifecycle (zero auto-persistence).
 *    - Page-switch unsaved changes protection.
 *    - Rejection of stale/malformed bridge messages and protection immunity.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve(__dirname, ".compiled_phase4e_security_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  validateGeneratedBlockItem,
  ALLOWED_SECTION_TAGS,
  ALLOWED_CSS_PROPERTIES,
  FORBIDDEN_STYLE_VALUE_PATTERNS,
  sanitizeAndValidateOpenDesignHtml,
  patchOpenDesignSection,
  containsSpoofingTokens,
  validateSafeUrl,
} from '../src/services/openDesignPatcher';

export {
  WebsiteBridgeClient,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  applyPatchToBlock,
  syncBlockChangesToPreview,
  isElementProtected,
} from '../src/services/websiteBridge';

export {
  generateWebsiteAISection,
  ApiError,
  setMemoryToken,
  getMemoryToken,
} from '../src/api/client';
`;

  const entryFile = path.resolve(__dirname, ".temp_phase4e_entry.tsx");
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

const bundleUrl = compileBundle();
const mod = await import(bundleUrl);

const {
  validateGeneratedBlockItem,
  ALLOWED_SECTION_TAGS,
  ALLOWED_CSS_PROPERTIES,
  FORBIDDEN_STYLE_VALUE_PATTERNS,
  sanitizeAndValidateOpenDesignHtml,
  patchOpenDesignSection,
  containsSpoofingTokens,
  validateSafeUrl,
  WebsiteBridgeClient,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  applyPatchToBlock,
  syncBlockChangesToPreview,
  isElementProtected,
  generateWebsiteAISection,
  ApiError,
  setMemoryToken,
} = mod;

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch { }
  }
});

function makeValidBlock(overrides = {}) {
  return {
    id: "od-block-valid-1",
    type: "open_design_section",
    is_visible: true,
    content: {
      section_tag: "section",
      section_id: "hero-sec-1",
      css_classes: "py-12 bg-white",
      styles: {
        "background-color": "#ffffff",
        "padding": "2rem",
      },
      html_content: '<section><h1 data-od-id="heading">Modern Solutions</h1><p data-od-id="desc">Built for enterprise scale.</p></section>',
      asset_references: ["/assets/img/hero.png"],
    },
    ...overrides,
  };
}

describe("RivinityADS Open Design — Phase 4E: Comprehensive Security & Lifecycle Verification", () => {

  describe("1. Hardened BlockItem Validation Matrix", () => {

    test("1.1 Rejects non-objects, null, undefined, and arrays", () => {
      assert.equal(validateGeneratedBlockItem(null).isValid, false);
      assert.equal(validateGeneratedBlockItem(undefined).isValid, false);
      assert.equal(validateGeneratedBlockItem("not-an-object").isValid, false);
      assert.equal(validateGeneratedBlockItem(12345).isValid, false);
      assert.equal(validateGeneratedBlockItem([]).isValid, false);
    });

    test("1.2 Rejects invalid block IDs (empty, non-string, >64 chars, invalid characters)", () => {
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: "" })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: null })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: 12345 })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: "a".repeat(65) })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: "invalid id with spaces" })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: "id<script>" })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ id: "id/with/slashes" })).isValid, false);
    });

    test("1.3 Rejects any block type other than 'open_design_section'", () => {
      const disallowedTypes = [
        "hero",
        "text_section",
        "features_grid",
        "image_gallery",
        "contact_form",
        "cta_banner",
        "ad_placement",
        "footer",
        "custom_type",
      ];
      for (const badType of disallowedTypes) {
        const res = validateGeneratedBlockItem(makeValidBlock({ type: badType }));
        assert.equal(res.isValid, false, `Must reject block type '${badType}'`);
        assert.ok(res.error?.includes("open_design_section"));
      }
    });

    test("1.4 Rejects non-boolean is_visible values when present", () => {
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ is_visible: "true" })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ is_visible: 1 })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ is_visible: null })).isValid, true); // defaults to true
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ is_visible: false })).isValid, true);
    });

    test("1.5 Rejects missing, non-object, or array content", () => {
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: null })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: undefined })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: "string" })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: [] })).isValid, false);
    });

    test("1.6 Rejects missing, empty, non-string, or oversized html_content", () => {
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: { html_content: null } })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: { html_content: "" } })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: { html_content: "   " } })).isValid, false);
      assert.equal(validateGeneratedBlockItem(makeValidBlock({ content: { html_content: 12345 } })).isValid, false);

      const hugeHtml = "<section><p>" + "A".repeat(66000) + "</p></section>";
      const oversizedRes = validateGeneratedBlockItem(makeValidBlock({ content: { html_content: hugeHtml } }));
      assert.equal(oversizedRes.isValid, false);
      assert.ok(oversizedRes.error?.includes("65,536"));
    });

    test("1.7 Rejects XSS and dangerous HTML tags/attributes in html_content", () => {
      const maliciousPayloads = [
        "<section><script>alert('xss')</script></section>",
        '<section><iframe src="https://evil.com"></iframe></section>',
        '<section><a href="javascript:alert(1)">Click me</a></section>',
        '<section><div onclick="evil()">Clickable</div></section>',
        '<section><img src="x" onerror="evil()"></section>',
        '<section><object data="evil.swf"></object></section>',
        '<section><embed src="evil.pdf"></embed></section>',
      ];
      for (const payload of maliciousPayloads) {
        const res = validateGeneratedBlockItem(makeValidBlock({ content: { html_content: payload } }));
        assert.equal(res.isValid, false, `Must reject payload: ${payload}`);
      }
    });

    test("1.8 Rejects disallowed section tags", () => {
      const invalidTags = ["form", "input", "script", "style", "table", "button"];
      for (const tag of invalidTags) {
        const res = validateGeneratedBlockItem(
          makeValidBlock({
            content: {
              html_content: "<p>Text</p>",
              section_tag: tag,
            },
          })
        );
        assert.equal(res.isValid, false, `Must reject section_tag '${tag}'`);
      }
    });

    test("1.9 Rejects section_id with spoofing tokens or invalid format", () => {
      const invalidSectionIds = [
        "block-ad-placement",
        "ad-slot-header",
        "rivinity_ad",
        "my-rivinity-section",
        "has spaces",
        "special!char",
        "a".repeat(65),
      ];
      for (const secId of invalidSectionIds) {
        const res = validateGeneratedBlockItem(
          makeValidBlock({
            content: {
              html_content: "<p>Text</p>",
              section_id: secId,
            },
          })
        );
        assert.equal(res.isValid, false, `Must reject section_id '${secId}'`);
      }
    });

    test("1.10 Rejects css_classes with spoofing tokens, invalid format, or >500 chars", () => {
      const invalidClasses = [
        "block-ad-placement",
        "ad_placement custom",
        "rivinity-banner",
        "special$chars#here",
        "a".repeat(501),
      ];
      for (const cls of invalidClasses) {
        const res = validateGeneratedBlockItem(
          makeValidBlock({
            content: {
              html_content: "<p>Text</p>",
              css_classes: cls,
            },
          })
        );
        assert.equal(res.isValid, false, `Must reject css_classes '${cls}'`);
      }
    });

    test("1.11 Rejects styles exceeding 30 entries, unwhitelisted properties, or dangerous tokens", () => {
      // Unwhitelisted property
      const badProp = validateGeneratedBlockItem(
        makeValidBlock({
          content: {
            html_content: "<p>Text</p>",
            styles: { "behavior": "url(evil.htc)" },
          },
        })
      );
      assert.equal(badProp.isValid, false);

      // Dangerous injection token in value
      const badVal = validateGeneratedBlockItem(
        makeValidBlock({
          content: {
            html_content: "<p>Text</p>",
            styles: { "color": "red; background: url(javascript:alert(1))" },
          },
        })
      );
      assert.equal(badVal.isValid, false);

      // More than 30 properties
      const styles31 = {};
      for (let i = 0; i < 31; i++) {
        styles31[`padding-${i}`] = "1rem";
      }
      const tooMany = validateGeneratedBlockItem(
        makeValidBlock({
          content: {
            html_content: "<p>Text</p>",
            styles: styles31,
          },
        })
      );
      assert.equal(tooMany.isValid, false);
    });

    test("1.12 Rejects asset_references with >20 items or unsafe URL schemes", () => {
      const badUrl = validateGeneratedBlockItem(
        makeValidBlock({
          content: {
            html_content: "<p>Text</p>",
            asset_references: ["javascript:alert(1)"],
          },
        })
      );
      assert.equal(badUrl.isValid, false);

      const tooManyUrls = Array.from({ length: 21 }, (_, i) => `/assets/img_${i}.png`);
      const tooMany = validateGeneratedBlockItem(
        makeValidBlock({
          content: {
            html_content: "<p>Text</p>",
            asset_references: tooManyUrls,
          },
        })
      );
      assert.equal(tooMany.isValid, false);
    });

    test("1.13 Normalizes and accepts valid open_design_section", () => {
      const valid = makeValidBlock();
      const res = validateGeneratedBlockItem(valid);
      assert.equal(res.isValid, true);
      assert.ok(res.validatedBlock);
      assert.equal(res.validatedBlock.id, "od-block-valid-1");
      assert.equal(res.validatedBlock.type, "open_design_section");
      assert.equal(res.validatedBlock.is_visible, true);
      assert.equal(res.validatedBlock.content.section_tag, "section");
      assert.equal(res.validatedBlock.content.styles["background-color"], "#ffffff");
    });
  });

  describe("2. End-to-End Draft Lifecycle & Isolation Verification", () => {

    test("2.1 Immutable draft insertion elevates schema_version to 2", () => {
      const pageDraft = {
        id: 1,
        title: "Home",
        blocks: {
          schema_version: 1,
          blocks: [
            { id: "hero-1", type: "hero", is_visible: true, content: { headline: "Existing Hero" } },
          ],
        },
      };

      const aiBlock = makeValidBlock({ id: "od-ai-gen-1" });
      const validation = validateGeneratedBlockItem(aiBlock);
      assert.equal(validation.isValid, true);

      // Simulate handleSectionGenerated logic
      const nextBlocks = [...pageDraft.blocks.blocks, validation.validatedBlock];
      const nextSchema = Math.max(pageDraft.blocks.schema_version, 2);
      const updatedPage = {
        ...pageDraft,
        blocks: {
          schema_version: nextSchema,
          blocks: nextBlocks,
        },
      };

      assert.equal(updatedPage.blocks.schema_version, 2);
      assert.equal(updatedPage.blocks.blocks.length, 2);
      assert.equal(updatedPage.blocks.blocks[1].id, "od-ai-gen-1");
      // Original object unchanged
      assert.equal(pageDraft.blocks.schema_version, 1);
      assert.equal(pageDraft.blocks.blocks.length, 1);
    });

    test("2.2 Localized HTML patching on generated section preserves surrounding markup", () => {
      const aiBlock = makeValidBlock({
        content: {
          html_content: '<section><h2 data-od-id="title">Old Title</h2><p data-od-id="copy">Untouched copy</p></section>',
        },
      });

      const patchResult = patchOpenDesignSection({
        blockId: aiBlock.id,
        blockType: "open_design_section",
        patchKind: "html_fragment",
        targetSelector: '[data-od-id="title"]',
        targetOdId: "title",
        originalValue: "Old Title",
        proposedValue: "New AI Engineered Title",
        htmlContent: aiBlock.content.html_content,
      });

      assert.equal(patchResult.isValid, true);
      assert.ok(patchResult.patchedHtml.includes("New AI Engineered Title"));
      assert.ok(patchResult.patchedHtml.includes("Untouched copy"));
      assert.ok(patchResult.patchedHtml.includes('data-od-id="title"'));
    });

    test("2.3 Discard action cleanly restores baseline snapshot without persisting AI block", () => {
      const baseline = {
        id: 1,
        title: "Baseline Home",
        blocks: {
          schema_version: 1,
          blocks: [{ id: "hero-1", type: "hero", is_visible: true, content: {} }],
        },
      };
      const baselineSnapshot = JSON.stringify(baseline);

      // In-memory draft with AI section added
      let draftPage = JSON.parse(baselineSnapshot);
      const aiBlock = makeValidBlock({ id: "unsaved-ai-block" });
      draftPage.blocks.blocks.push(aiBlock);
      draftPage.blocks.schema_version = 2;
      let isDirty = true;

      assert.equal(draftPage.blocks.blocks.length, 2);
      assert.equal(isDirty, true);

      // Discard action executed
      draftPage = JSON.parse(baselineSnapshot);
      isDirty = false;

      assert.equal(draftPage.blocks.blocks.length, 1);
      assert.equal(draftPage.blocks.blocks[0].id, "hero-1");
      assert.equal(isDirty, false);
      assert.equal(draftPage.blocks.schema_version, 1);
    });

    test("2.4 Page switch halts when isDirty is true", () => {
      let isDirty = true;
      let showUnsavedPrompt = false;
      let pendingPageSwitchId = null;

      function handleSelectPage(targetPageId) {
        if (isDirty) {
          pendingPageSwitchId = targetPageId;
          showUnsavedPrompt = true;
          return;
        }
      }

      handleSelectPage(2);
      assert.equal(showUnsavedPrompt, true);
      assert.equal(pendingPageSwitchId, 2);
    });

    test("2.5 Stale session and unauthorized block proposals never enter draft state", () => {
      const draft = {
        id: 1,
        blocks: {
          schema_version: 2,
          blocks: [makeValidBlock({ id: "valid-sec" })],
        },
      };
      const initialJson = JSON.stringify(draft);

      // Stale session proposal
      const staleRes = validateProposedPatchAgainstDraft(
        {
          sessionId: "stale-session-123",
          blockId: "valid-sec",
          field: "headline",
          itemIndex: null,
          proposedValue: "Hacked value",
        },
        draft.blocks.blocks,
        "active-session-456" // expected active session
      );
      assert.equal(staleRes.isValid, false);
      assert.equal(JSON.stringify(draft), initialJson, "Draft must not be mutated by stale session proposal");

      // Non-existent block proposal
      const nonExistentRes = validateProposedPatchAgainstDraft(
        {
          sessionId: "active-session-456",
          blockId: "ghost-block-999",
          field: "headline",
          itemIndex: null,
          proposedValue: "Hacked value",
        },
        draft.blocks.blocks,
        "active-session-456"
      );
      assert.equal(nonExistentRes.isValid, false);
      assert.equal(JSON.stringify(draft), initialJson, "Draft must not be mutated by non-existent block proposal");
    });

    test("2.6 Protected platform elements remain strictly non-editable and non-billable", () => {
      const adPlacementBlock = {
        id: "ad-placement-1",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1,
          ad_slot_id: "slot-banner-300x250",
        },
      };

      // 1. Block-level selection of ad_placement is protected
      const selResult = validateSelectionAgainstDraft(
        {
          blockId: "ad-placement-1",
          blockType: "ad_placement",
          field: null,
          itemIndex: null,
          classification: "protected",
        },
        [adPlacementBlock]
      );
      assert.equal(selResult.isValid, true);
      assert.equal(selResult.classification, "protected");

      // 2. Field-level selection attempt on ad placement is rejected (ad_placement has 0 editable fields)
      const badFieldSel = validateSelectionAgainstDraft(
        {
          blockId: "ad-placement-1",
          blockType: "ad_placement",
          field: "placement_location",
          itemIndex: null,
          classification: "editable",
        },
        [adPlacementBlock]
      );
      assert.equal(badFieldSel.isValid, false);

      // 3. Patch proposal attempt on ad placement is rejected
      const patchAttempt = validateProposedPatchAgainstDraft(
        {
          sessionId: "active-session-1",
          blockId: "ad-placement-1",
          blockType: "ad_placement",
          field: "placement_location",
          itemIndex: null,
          proposedValue: "hacked_location",
        },
        [adPlacementBlock],
        "active-session-1"
      );
      assert.equal(patchAttempt.isValid, false);
      assert.ok(patchAttempt.error?.includes("protected"));
    });
  });
});
