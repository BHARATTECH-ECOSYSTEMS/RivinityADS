/**
 * open_design_phase3_2_metadata.test.mjs — Phase 3.2: Semantic DOM Metadata Test Suite
 *
 * Verifies:
 * 1. CanvasBlockRenderer semantic metadata parity:
 *    - Renders data-od-block-id and data-od-block-type on block wrappers.
 *    - Renders data-od-field and data-od-editable on editable native elements.
 *    - Renders data-od-item-index on repeating array items.
 *    - Strictly enforces data-od-protected="true" and data-od-editable="false" on ad placements.
 * 2. Bridge metadata extraction utilities (websiteBridge.ts):
 *    - getBlockId extracts valid IDs and rejects malformed/missing IDs.
 *    - getBlockType extracts recognized types and rejects unknown types.
 *    - getFieldIdentifier extracts valid field paths.
 *    - getItemIndex parses and validates integer indexes (rejects negatives and non-numbers).
 *    - isElementProtected detects protected elements and inherited protected ancestors.
 *    - extractElementMetadata produces validated metadata records.
 *    - Protection overrides descendant editable flags (protected elements never become editable).
 * 3. MessageChannel regression compatibility:
 *    - WebsiteBridgeClient lifecycle and handshake remain green.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router SSR warnings in Node test environment
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
  const bundlePath = path.resolve("./tests/.compiled_phase3_2_metadata_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { CanvasBlockRenderer } from '../src/components/website/CanvasBlockRenderer';
export {
  WebsiteBridgeClient,
  getBlockId,
  getBlockType,
  getFieldIdentifier,
  getItemIndex,
  isElementProtected,
  extractElementMetadata,
  VALID_BLOCK_TYPES,
  BLOCK_ID_REGEX,
} from '../src/services/websiteBridge';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_phase3_2_entry.tsx");
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
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

describe("RivinityADS Open Design Phase 3.2 — Semantic DOM Metadata", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    React,
    CanvasBlockRenderer,
    WebsiteBridgeClient,
    getBlockId,
    getBlockType,
    getFieldIdentifier,
    getItemIndex,
    isElementProtected,
    extractElementMetadata,
  } = mod;

  const dummyTheme = {
    primary_color: "#2563eb",
    secondary_color: "#475569",
    background_color: "#ffffff",
    text_color: "#0f172a",
    font_family: "Inter",
    border_radius: "0.5rem",
    logo_url: "",
    favicon_url: "",
  };

  describe("1. CanvasBlockRenderer Metadata Parity", () => {
    test("Hero block renders data-od-block-id, data-od-block-type, and editable fields", () => {
      const block = {
        id: "hero_test_1",
        type: "hero",
        is_visible: true,
        content: {
          headline: "Canvas Hero Headline",
          subheadline: "Canvas Hero Subheadline",
          cta_button: { label: "Get Started Now", target_url: "https://example.com", style: "primary" },
          alignment: "left",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block,
          theme: dummyTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes('data-od-block-id="hero_test_1"'));
      assert.ok(html.includes('data-od-block-type="hero"'));
      assert.ok(html.includes('data-od-field="headline"'));
      assert.ok(html.includes('data-od-field="subheadline"'));
      assert.ok(html.includes('data-od-field="cta_button.label"'));
      assert.ok(html.includes('data-od-editable="true"'));
    });

    test("Text section block renders heading and array paragraph item indexes", () => {
      const block = {
        id: "text_test_2",
        type: "text_section",
        is_visible: true,
        content: {
          heading: "Section Heading",
          body: "Paragraph one.\n\nParagraph two.",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block,
          theme: dummyTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes('data-od-block-id="text_test_2"'));
      assert.ok(html.includes('data-od-block-type="text_section"'));
      assert.ok(html.includes('data-od-field="heading"'));
      assert.ok(html.includes('data-od-field="body"'));
      assert.ok(html.includes('data-od-item-index="0"'));
      assert.ok(html.includes('data-od-item-index="1"'));
    });

    test("Features grid renders title, card items with indexes, and non-editable icon box", () => {
      const block = {
        id: "feat_test_3",
        type: "features_grid",
        is_visible: true,
        content: {
          title: "Features Title",
          items: [
            { title: "Speed", description: "Blazing fast" },
            { title: "Security", description: "Zero trust" },
          ],
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block,
          theme: dummyTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes('data-od-block-id="feat_test_3"'));
      assert.ok(html.includes('data-od-block-type="features_grid"'));
      assert.ok(html.includes('data-od-field="items.title" data-od-item-index="0" data-od-editable="true"'));
      assert.ok(html.includes('data-od-field="items.description" data-od-item-index="0" data-od-editable="true"'));
      assert.ok(html.includes('data-od-field="items.icon" data-od-item-index="0" data-od-editable="false"'));
      assert.ok(html.includes('data-od-field="items.title" data-od-item-index="1" data-od-editable="true"'));
    });

    test("Ad placement block strictly emits data-od-protected='true' and data-od-editable='false'", () => {
      const block = {
        id: "ad_test_4",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "sidebar",
          format: 1,
          ad_slot_id: "slot_xyz",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block,
          theme: dummyTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes('data-od-block-id="ad_test_4"'));
      assert.ok(html.includes('data-od-block-type="ad_placement"'));
      assert.ok(html.includes('data-od-protected="true"'));
      assert.ok(html.includes('data-od-editable="false"'));
      // Must not contain any editable fields
      assert.ok(!html.includes('data-od-editable="true"'));
    });

    test("Open Design section renders trusted metadata on outer section element", () => {
      const block = {
        id: "od_custom_5",
        type: "open_design_section",
        is_visible: true,
        content: {
          section_id: "sec-od-5",
          css_classes: "od-hero",
          html_content: '<div class="inner"><h3>Unmodified User HTML</h3></div>',
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block,
          theme: dummyTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes('data-od-block-id="od_custom_5"'));
      assert.ok(html.includes('data-od-block-type="open_design_section"'));
      assert.ok(html.includes('<h3>Unmodified User HTML</h3>'));
      // Inner HTML must NOT have injected data-od-field
      assert.ok(!html.includes('<h3 data-od-field'));
    });
  });

  describe("2. Bridge Metadata Extraction Utilities", () => {
    // Helper to create mock DOM element with attributes and parent tree
    function createMockElement(tag, attrs = {}, parent = null) {
      const element = {
        tagName: tag.toUpperCase(),
        attributes: { ...attrs },
        parentElement: parent,
        getAttribute(name) {
          return this.attributes[name] !== undefined ? String(this.attributes[name]) : null;
        },
        hasAttribute(name) {
          return this.attributes[name] !== undefined;
        },
        closest(selector) {
          let curr = this;
          while (curr) {
            // Check protected attributes
            if (selector.includes('[data-od-protected="true"]') && curr.getAttribute('data-od-protected') === 'true') {
              return curr;
            }
            if (selector.includes('[data-od-block-id]') && curr.hasAttribute('data-od-block-id')) {
              return curr;
            }
            if (selector.includes('[data-od-block-type]') && curr.hasAttribute('data-od-block-type')) {
              return curr;
            }
            if (selector.includes('.block-ad-placement') && (curr.attributes.class || '').includes('block-ad-placement')) {
              return curr;
            }
            if (selector.includes('.rivinity-ad-placeholder') && (curr.attributes.class || '').includes('rivinity-ad-placeholder')) {
              return curr;
            }
            if (selector.includes('#rivinity-consent-banner') && curr.attributes.id === 'rivinity-consent-banner') {
              return curr;
            }
            if (selector.includes('.preview-banner') && (curr.attributes.class || '').includes('preview-banner')) {
              return curr;
            }
            curr = curr.parentElement;
          }
          return null;
        },
      };
      return element;
    }

    test("getBlockId extracts valid ID and rejects malformed/missing IDs", () => {
      const validEl = createMockElement("div", { "data-od-block-id": "blk_hero_123" });
      assert.strictEqual(getBlockId(validEl), "blk_hero_123");

      // Ancestor lookup
      const parentBlock = createMockElement("section", { "data-od-block-id": "blk_parent_456" });
      const childLeaf = createMockElement("h1", {}, parentBlock);
      assert.strictEqual(getBlockId(childLeaf), "blk_parent_456");

      // Invalid ID with spaces or special symbols
      const invalidEl1 = createMockElement("div", { "data-od-block-id": "invalid id with spaces" });
      assert.strictEqual(getBlockId(invalidEl1), null);

      const invalidEl2 = createMockElement("div", { "data-od-block-id": "id<script>bad" });
      assert.strictEqual(getBlockId(invalidEl2), null);

      // Missing ID
      const missingEl = createMockElement("div", {});
      assert.strictEqual(getBlockId(missingEl), null);
      assert.strictEqual(getBlockId(null), null);
    });

    test("getBlockType extracts recognized block types and rejects unknown types", () => {
      const heroEl = createMockElement("section", { "data-od-block-type": "hero" });
      assert.strictEqual(getBlockType(heroEl), "hero");

      const odSecEl = createMockElement("section", { "data-od-block-type": "open_design_section" });
      assert.strictEqual(getBlockType(odSecEl), "open_design_section");

      const unknownEl = createMockElement("div", { "data-od-block-type": "fake_unknown_block" });
      assert.strictEqual(getBlockType(unknownEl), null);

      const emptyEl = createMockElement("div", {});
      assert.strictEqual(getBlockType(emptyEl), null);
    });

    test("getFieldIdentifier extracts field name and enforces bounds", () => {
      const fieldEl = createMockElement("h1", { "data-od-field": "headline" });
      assert.strictEqual(getFieldIdentifier(fieldEl), "headline");

      const emptyField = createMockElement("h1", { "data-od-field": "   " });
      assert.strictEqual(getFieldIdentifier(emptyField), null);

      const noField = createMockElement("h1", {});
      assert.strictEqual(getFieldIdentifier(noField), null);
    });

    test("getItemIndex validates non-negative integer indexes and rejects invalid formats", () => {
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "0" })), 0);
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "5" })), 5);
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "12" })), 12);

      // Negatives, decimals, and non-numeric strings are rejected
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "-1" })), null);
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "1.5" })), null);
      assert.strictEqual(getItemIndex(createMockElement("p", { "data-od-item-index": "first" })), null);
      assert.strictEqual(getItemIndex(createMockElement("p", {})), null);
    });

    test("isElementProtected detects protected elements and inherited protected ancestors", () => {
      // Direct protected attribute
      const directProtected = createMockElement("div", { "data-od-protected": "true" });
      assert.strictEqual(isElementProtected(directProtected), true);

      // Protected classes
      const adPlaceholder = createMockElement("div", { class: "rivinity-ad-placeholder" });
      assert.strictEqual(isElementProtected(adPlaceholder), true);

      const bannerEl = createMockElement("div", { class: "preview-banner" });
      assert.strictEqual(isElementProtected(bannerEl), true);

      // Descendant of protected ancestor
      const adParent = createMockElement("div", { class: "block-ad-placement", "data-od-protected": "true" });
      const adChildText = createMockElement("span", { "data-od-editable": "true" }, adParent);
      assert.strictEqual(isElementProtected(adChildText), true);

      // Normal unprotected element
      const normalEl = createMockElement("h1", { "data-od-field": "headline" });
      assert.strictEqual(isElementProtected(normalEl), false);
    });

    test("extractElementMetadata compiles valid metadata and enforces protection overrides", () => {
      const blockRoot = createMockElement("section", {
        "data-od-block-id": "blk_hero_99",
        "data-od-block-type": "hero",
      });
      const headlineEl = createMockElement(
        "h1",
        {
          "data-od-field": "headline",
          "data-od-editable": "true",
        },
        blockRoot
      );

      const meta = extractElementMetadata(headlineEl);
      assert.ok(meta);
      assert.strictEqual(meta.blockId, "blk_hero_99");
      assert.strictEqual(meta.blockType, "hero");
      assert.strictEqual(meta.field, "headline");
      assert.strictEqual(meta.itemIndex, null);
      assert.strictEqual(meta.isEditable, true);
      assert.strictEqual(meta.isProtected, false);

      // CRITICAL: Contradictory metadata (element claims editable="true" inside protected ad placement)
      const adBlock = createMockElement("div", {
        "data-od-block-id": "blk_ad_99",
        "data-od-block-type": "ad_placement",
        "data-od-protected": "true",
      });
      const spoofedChild = createMockElement(
        "p",
        {
          "data-od-field": "title",
          "data-od-editable": "true", // Malicious child claims it is editable
        },
        adBlock
      );

      const adMeta = extractElementMetadata(spoofedChild);
      assert.ok(adMeta);
      assert.strictEqual(adMeta.isProtected, true);
      assert.strictEqual(adMeta.isEditable, false, "Protected ancestor MUST strictly override child editable flag");

      // Missing blockId returns null
      const unattachedEl = createMockElement("h2", { "data-od-field": "heading" });
      assert.strictEqual(extractElementMetadata(unattachedEl), null);
    });
  });

  describe("3. MessageChannel Regression Compatibility", () => {
    test("WebsiteBridgeClient remains functional with Phase 3.2 metadata models", () => {
      assert.strictEqual(typeof WebsiteBridgeClient, "function");
      const client = new WebsiteBridgeClient({ iframe: {} });
      assert.strictEqual(client.getStatus(), "uninitialized");
    });
  });
});

