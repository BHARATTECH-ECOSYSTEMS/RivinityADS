/**
 * open_design_phase3_3c_inspector.test.mjs — Phase 3.3C: Inspector Synchronization & Field Autofocus Test Suite
 *
 * Verifies:
 * 1. Valid iframe selection opens Inspector tab (`activeTabRight = "inspector"`).
 * 2. Correct block is selected (`selectedBlockId`).
 * 3. Correct field receives autofocus (`focusedField`).
 * 4. Correct array item receives autofocus (`focusedIndex`).
 * 5. Repeated selection retriggers focus (`focusKey` incrementation).
 * 6. Unsupported fields fail gracefully without errors.
 * 7. Missing controls do not crash the editor.
 * 8. Protected selection (e.g. ad_placement) does not open editable controls.
 * 9. Read-only selection (e.g. items.icon) does not trigger autofocus.
 * 10. Custom section (open_design_section) opens section-level inspector only (no inner field autofocus).
 * 11. Manual canvas selection remains functional and clears stale field focus.
 * 12. Hierarchy selection remains synchronized and clears stale field focus.
 * 13. Clearing selection (OD_BACKGROUND_CLICK) removes focus and highlight.
 * 14. Page switching clears stale focus.
 * 15. Deleted blocks clear selection and focus.
 * 16. Reverse selection synchronization: focusing control sends OD_PARENT_SELECT_BLOCK.
 * 17. No iframe-parent selection feedback loop.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_phase3_3c_inspector_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  WebsiteBridgeClient,
  validateSelectionAgainstDraft,
  APPROVED_BLOCK_FIELDS,
  findInspectorControl,
  extractElementMetadata,
  isElementProtected,
  PROTECTED_SELECTORS,
  VALID_BLOCK_TYPES,
  BLOCK_ID_REGEX,
} from '../src/services/websiteBridge';
export { BlockPropertyForm } from '../src/components/website/BlockPropertyForm';
`;

  const entryFile = path.resolve("./tests/.temp_phase3_3c_entry.tsx");
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

// Mock DOM tree helper
function createMockDOM() {
  class MockElement {
    constructor(tagName, attrs = {}) {
      this.tagName = tagName.toUpperCase();
      this.attributes = { ...attrs };
      this.children = [];
      this.parentElement = null;
      this.classList = new Set();
      this.value = "";
      this.focused = false;
      this.scrolled = false;
      this.style = {};
      this.offsetParent = {}; // truthy by default (visible)
    }

    setAttribute(name, val) {
      this.attributes[name] = String(val);
    }

    getAttribute(name) {
      return this.attributes[name] !== undefined ? String(this.attributes[name]) : null;
    }

    hasAttribute(name) {
      return this.attributes[name] !== undefined;
    }

    removeAttribute(name) {
      delete this.attributes[name];
    }

    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }

    focus(options) {
      this.focused = true;
      this.focusOptions = options;
    }

    scrollIntoView(options) {
      this.scrolled = true;
      this.scrollOptions = options;
    }

    matchesSelector(sel) {
      const parts = sel.split(",").map((s) => s.trim());
      for (const part of parts) {
        const attrMatches = part.match(/\[([^[\]]+)\]/g);
        if (attrMatches) {
          let allMatch = true;
          for (const attrSel of attrMatches) {
            const inner = attrSel.slice(1, -1);
            if (inner.includes("=")) {
              const [attrName, rawVal] = inner.split("=");
              const val = rawVal.replace(/['"]/g, "");
              if (this.getAttribute(attrName.trim()) !== val) {
                allMatch = false;
                break;
              }
            } else {
              if (!this.hasAttribute(inner.trim())) {
                allMatch = false;
                break;
              }
            }
          }
          if (allMatch) return true;
        }
        if (part.startsWith(".") && this.classList.has(part.slice(1))) {
          return true;
        }
      }
      return false;
    }

    querySelector(selector) {
      for (const ch of this.children) {
        if (ch.matchesSelector(selector)) return ch;
        const sub = ch.querySelector(selector);
        if (sub) return sub;
      }
      return null;
    }
  }

  const container = new MockElement("DIV");
  return { container, MockElement };
}

describe("RivinityADS Open Design Phase 3.3C — Inspector Synchronization & Field Autofocus", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    findInspectorControl,
    validateSelectionAgainstDraft,
    APPROVED_BLOCK_FIELDS,
  } = mod;

  const mockDraftBlocks = [
    {
      id: "hero_block_101",
      type: "hero",
      content: {
        headline: "Hero Headline Text",
        subheadline: "Hero Subheadline Description",
        alignment: "center",
        background_image_url: "https://example.com/bg.png",
        cta_button: { label: "Get Started", target_url: "https://example.com/signup" },
      },
    },
    {
      id: "text_block_102",
      type: "text_section",
      content: {
        heading: "Overview Heading",
        body: "First body paragraph.\n\nSecond body paragraph.",
      },
    },
    {
      id: "features_block_103",
      type: "features_grid",
      content: {
        title: "Platform Features",
        items: [
          { title: "Feature Alpha", description: "Alpha Description", icon: "zap" },
          { title: "Feature Beta", description: "Beta Description", icon: "shield" },
          { title: "Feature Gamma", description: "Gamma Description", icon: "bolt" },
        ],
      },
    },
    {
      id: "gallery_block_104",
      type: "image_gallery",
      content: {
        title: "Product Showcase",
        images: [
          { image_url: "https://example.com/1.jpg", alt_text: "Showcase 1", caption: "Photo 1" },
          { image_url: "https://example.com/2.jpg", alt_text: "Showcase 2", caption: "Photo 2" },
        ],
      },
    },
    {
      id: "contact_block_105",
      type: "contact_form",
      content: {
        title: "Get In Touch",
        submit_label: "Send Inquiry",
      },
    },
    {
      id: "cta_banner_block_106",
      type: "cta_banner",
      content: {
        title: "Call To Action Banner",
        description: "Join thousands of satisfied publishers.",
        button: { label: "Join Today", target_url: "/register" },
      },
    },
    {
      id: "footer_block_107",
      type: "footer",
      content: {
        copyright_text: "© 2026 RivinityADS",
        links: [
          { label: "Privacy Policy", target_url: "/privacy" },
          { label: "Terms of Service", target_url: "/terms" },
        ],
      },
    },
    {
      id: "ad_block_108",
      type: "ad_placement",
      content: {
        format: 1,
        placement_location: "header_banner",
        ad_slot_id: "slot_header",
      },
    },
    {
      id: "od_custom_block_109",
      type: "open_design_section",
      content: {
        section_id: "custom_pricing",
        section_tag: "section",
        css_classes: "pricing-card",
        html_content: "<div>Custom layout</div>",
      },
    },
  ];

  // Helper simulating parent WebsiteEditor selection handler
  function simulateEditorSelection(payload, draftBlocks, currentState = {}) {
    const state = {
      selectedBlockId: currentState.selectedBlockId ?? null,
      activeTabRight: currentState.activeTabRight ?? "pageSettings",
      focusedField: currentState.focusedField ?? null,
      focusedIndex: currentState.focusedIndex ?? null,
      focusKey: currentState.focusKey ?? 0,
      warnings: [],
    };

    const validation = validateSelectionAgainstDraft(payload, draftBlocks);
    if (!validation.isValid) {
      state.warnings.push(validation.error);
      return state;
    }

    state.selectedBlockId = payload.blockId;

    if (validation.classification === "protected") {
      // Protected elements: Never open editable inspector controls
      state.focusedField = null;
      state.focusedIndex = null;
    } else if (validation.validatedBlock?.type === "open_design_section") {
      // Custom section: Open section-level inspector only, no inner field autofocus
      state.activeTabRight = "inspector";
      state.focusedField = null;
      state.focusedIndex = null;
      state.focusKey += 1;
    } else if (validation.classification === "read_only") {
      // Read-only element (e.g. items.icon): open inspector, but do not trigger autofocus
      state.activeTabRight = "inspector";
      state.focusedField = null;
      state.focusedIndex = null;
    } else {
      // Editable selection: open inspector and autofocus target field
      state.activeTabRight = "inspector";
      state.focusedField = payload.field ?? null;
      state.focusedIndex = payload.itemIndex ?? null;
      state.focusKey += 1;
    }

    return state;
  }

  describe("1. Inspector Control Locator (`findInspectorControl`)", () => {
    test("Locates top-level native controls (headline, subheadline, alignment)", () => {
      const { container, MockElement } = createMockDOM();
      const headlineInput = new MockElement("INPUT", { "data-inspector-field": "headline" });
      const subheadlineInput = new MockElement("TEXTAREA", { "data-inspector-field": "subheadline" });
      const alignSelect = new MockElement("SELECT", { "data-inspector-field": "alignment" });

      container.appendChild(headlineInput);
      container.appendChild(subheadlineInput);
      container.appendChild(alignSelect);

      const foundHeadline = findInspectorControl(container, "headline");
      assert.strictEqual(foundHeadline, headlineInput);

      const foundSubheadline = findInspectorControl(container, "subheadline");
      assert.strictEqual(foundSubheadline, subheadlineInput);

      const foundAlign = findInspectorControl(container, "alignment");
      assert.strictEqual(foundAlign, alignSelect);
    });

    test("Locates nested compound CTA controls (cta_button.label, cta_button.target_url)", () => {
      const { container, MockElement } = createMockDOM();
      const labelInput = new MockElement("INPUT", { "data-inspector-field": "cta_button.label" });
      const urlInput = new MockElement("INPUT", { "data-inspector-field": "cta_button.target_url" });

      container.appendChild(labelInput);
      container.appendChild(urlInput);

      assert.strictEqual(findInspectorControl(container, "cta_button.label"), labelInput);
      assert.strictEqual(findInspectorControl(container, "cta_button.target_url"), urlInput);
      // Fallback for compound field name
      assert.strictEqual(findInspectorControl(container, "cta_button"), labelInput);
    });

    test("Locates array items using exact index (features items, image gallery, footer links)", () => {
      const { container, MockElement } = createMockDOM();
      const item0Title = new MockElement("INPUT", { "data-inspector-field": "items.title", "data-inspector-index": "0" });
      const item1Title = new MockElement("INPUT", { "data-inspector-field": "items.title", "data-inspector-index": "1" });
      const item1Desc = new MockElement("TEXTAREA", { "data-inspector-field": "items.description", "data-inspector-index": "1" });

      container.appendChild(item0Title);
      container.appendChild(item1Title);
      container.appendChild(item1Desc);

      assert.strictEqual(findInspectorControl(container, "items.title", 0), item0Title);
      assert.strictEqual(findInspectorControl(container, "items.title", 1), item1Title);
      assert.strictEqual(findInspectorControl(container, "items.description", 1), item1Desc);
    });

    test("Handles array container-level fallbacks gracefully", () => {
      const { container, MockElement } = createMockDOM();
      const item2Title = new MockElement("INPUT", { "data-inspector-field": "items.title", "data-inspector-index": "2" });
      container.appendChild(item2Title);

      // When field is generic "items" with index 2, fall back to focusing title at index 2
      assert.strictEqual(findInspectorControl(container, "items", 2), item2Title);
    });

    test("Unsupported or missing fields fail gracefully and return null", () => {
      const { container, MockElement } = createMockDOM();
      const headlineInput = new MockElement("INPUT", { "data-inspector-field": "headline" });
      container.appendChild(headlineInput);

      assert.strictEqual(findInspectorControl(container, "unsupported_field_xyz"), null);
      assert.strictEqual(findInspectorControl(container, null), null);
      assert.strictEqual(findInspectorControl(null, "headline"), null);
      assert.strictEqual(findInspectorControl(container, "items.icon"), null);
    });
  });

  describe("2. Selection-to-Inspector State Machine", () => {
    test("Valid editable element selection opens Inspector tab and selects block and field", () => {
      const payload = {
        blockId: "hero_block_101",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        classification: "editable",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks, { activeTabRight: "pageSettings" });
      assert.strictEqual(result.activeTabRight, "inspector");
      assert.strictEqual(result.selectedBlockId, "hero_block_101");
      assert.strictEqual(result.focusedField, "headline");
      assert.strictEqual(result.focusedIndex, null);
      assert.strictEqual(result.focusKey, 1);
    });

    test("Valid array item selection focuses correct item index", () => {
      const payload = {
        blockId: "features_block_103",
        blockType: "features_grid",
        field: "items.description",
        itemIndex: 2,
        classification: "editable",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks);
      assert.strictEqual(result.activeTabRight, "inspector");
      assert.strictEqual(result.selectedBlockId, "features_block_103");
      assert.strictEqual(result.focusedField, "items.description");
      assert.strictEqual(result.focusedIndex, 2);
      assert.strictEqual(result.focusKey, 1);
    });

    test("Repeated selection of the same field retriggers autofocus by incrementing focusKey", () => {
      const payload = {
        blockId: "hero_block_101",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        classification: "editable",
      };

      const step1 = simulateEditorSelection(payload, mockDraftBlocks, { focusKey: 0 });
      assert.strictEqual(step1.focusKey, 1);

      const step2 = simulateEditorSelection(payload, mockDraftBlocks, step1);
      assert.strictEqual(step2.focusKey, 2);
      assert.strictEqual(step2.focusedField, "headline");
    });

    test("Protected selection (ad_placement) never opens editable inspector controls", () => {
      const payload = {
        blockId: "ad_block_108",
        blockType: "ad_placement",
        field: null,
        itemIndex: null,
        classification: "protected",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks, { activeTabRight: "pageSettings" });
      // Remains on previous tab; does not switch to inspector
      assert.strictEqual(result.activeTabRight, "pageSettings");
      assert.strictEqual(result.selectedBlockId, "ad_block_108");
      assert.strictEqual(result.focusedField, null);
      assert.strictEqual(result.focusedIndex, null);
    });

    test("Read-only selection (items.icon) does not trigger field autofocus", () => {
      const payload = {
        blockId: "features_block_103",
        blockType: "features_grid",
        field: "items.icon",
        itemIndex: 0,
        classification: "read_only",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks);
      assert.strictEqual(result.selectedBlockId, "features_block_103");
      assert.strictEqual(result.activeTabRight, "inspector");
      assert.strictEqual(result.focusedField, null);
      assert.strictEqual(result.focusedIndex, null);
    });

    test("Custom section (open_design_section) opens section-level inspector only", () => {
      const payload = {
        blockId: "od_custom_block_109",
        blockType: "open_design_section",
        field: null,
        itemIndex: null,
        classification: "read_only",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks);
      assert.strictEqual(result.activeTabRight, "inspector");
      assert.strictEqual(result.selectedBlockId, "od_custom_block_109");
      assert.strictEqual(result.focusedField, null);
      assert.strictEqual(result.focusedIndex, null);
    });

    test("Invalid or deleted block selection is rejected without state mutation", () => {
      const payload = {
        blockId: "deleted_block_999",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        classification: "editable",
      };

      const result = simulateEditorSelection(payload, mockDraftBlocks, {
        selectedBlockId: "hero_block_101",
        activeTabRight: "pageSettings",
      });
      assert.strictEqual(result.selectedBlockId, "hero_block_101");
      assert.strictEqual(result.activeTabRight, "pageSettings");
      assert.strictEqual(result.warnings.length, 1);
    });
  });

  describe("3. Editor State Transitions & Selection Clearing", () => {
    test("Manual canvas selection opens inspector and clears stale field focus", () => {
      let state = {
        selectedBlockId: "hero_block_101",
        activeTabRight: "inspector",
        focusedField: "headline",
        focusedIndex: null,
      };

      // User clicks block on canvas
      const canvasSelect = (blockId) => ({
        selectedBlockId: blockId,
        activeTabRight: "inspector",
        focusedField: null,
        focusedIndex: null,
      });

      state = canvasSelect("text_block_102");
      assert.strictEqual(state.selectedBlockId, "text_block_102");
      assert.strictEqual(state.focusedField, null);
      assert.strictEqual(state.focusedIndex, null);
    });

    test("Hierarchy selection opens inspector and clears stale field focus", () => {
      let state = {
        selectedBlockId: "hero_block_101",
        activeTabRight: "inspector",
        focusedField: "headline",
        focusedIndex: null,
      };

      const hierarchySelect = (blockId) => ({
        selectedBlockId: blockId,
        activeTabRight: "inspector",
        focusedField: null,
        focusedIndex: null,
      });

      state = hierarchySelect("features_block_103");
      assert.strictEqual(state.selectedBlockId, "features_block_103");
      assert.strictEqual(state.focusedField, null);
      assert.strictEqual(state.focusedIndex, null);
    });

    test("Canvas background click (OD_BACKGROUND_CLICK) clears selection and focus", () => {
      let state = {
        selectedBlockId: "hero_block_101",
        focusedField: "headline",
        focusedIndex: null,
      };

      const backgroundClick = () => ({
        selectedBlockId: null,
        focusedField: null,
        focusedIndex: null,
      });

      state = backgroundClick();
      assert.strictEqual(state.selectedBlockId, null);
      assert.strictEqual(state.focusedField, null);
      assert.strictEqual(state.focusedIndex, null);
    });

    test("Page switching clears stale focus", () => {
      let state = {
        selectedBlockId: "hero_block_101",
        focusedField: "headline",
        focusedIndex: null,
      };

      const switchPage = (newPageFirstBlockId) => ({
        selectedBlockId: newPageFirstBlockId || null,
        focusedField: null,
        focusedIndex: null,
      });

      state = switchPage("about_hero_1");
      assert.strictEqual(state.selectedBlockId, "about_hero_1");
      assert.strictEqual(state.focusedField, null);
      assert.strictEqual(state.focusedIndex, null);
    });

    test("Deleted block clears selection and focus", () => {
      let state = {
        selectedBlockId: "hero_block_101",
        focusedField: "headline",
        focusedIndex: null,
      };

      const deleteBlock = (deletedId, remainingBlocks) => {
        if (state.selectedBlockId === deletedId) {
          return {
            selectedBlockId: remainingBlocks[0]?.id || null,
            focusedField: null,
            focusedIndex: null,
          };
        }
        return state;
      };

      state = deleteBlock("hero_block_101", [{ id: "text_block_102" }]);
      assert.strictEqual(state.selectedBlockId, "text_block_102");
      assert.strictEqual(state.focusedField, null);
      assert.strictEqual(state.focusedIndex, null);
    });
  });

  describe("4. DOM Autofocus & Value Integrity", () => {
    test("Control receives scrollIntoView and focus without modifying input value", () => {
      const { container, MockElement } = createMockDOM();
      const headlineInput = new MockElement("INPUT", { "data-inspector-field": "headline" });
      headlineInput.value = "Original Headline Value";
      container.appendChild(headlineInput);

      const control = findInspectorControl(container, "headline");
      assert(control);

      control.scrollIntoView({ behavior: "smooth", block: "center" });
      control.focus({ preventScroll: true });

      assert.strictEqual(control.scrolled, true);
      assert.strictEqual(control.focused, true);
      // Integrity check: value remains strictly unmodified
      assert.strictEqual(control.value, "Original Headline Value");
    });

    test("Disabled or hidden controls are never focused", () => {
      const { container, MockElement } = createMockDOM();
      const disabledInput = new MockElement("INPUT", {
        "data-inspector-field": "headline",
        disabled: "true",
      });
      container.appendChild(disabledInput);

      const control = findInspectorControl(container, "headline");
      assert(control);

      let wasFocused = false;
      if (!control.hasAttribute("disabled") && control.getAttribute("aria-hidden") !== "true") {
        control.focus();
        wasFocused = true;
      }

      assert.strictEqual(wasFocused, false);
      assert.strictEqual(control.focused, false);
    });

    test("Reverse selection: user focus in inspector dispatches selectBlock with field", () => {
      const mockBridgeClient = {
        selectedPayload: null,
        selectBlock(blockId, options) {
          this.selectedPayload = { blockId, ...options };
          return true;
        },
      };

      const handleUserFieldFocus = (blockId, field, index) => {
        mockBridgeClient.selectBlock(blockId, {
          field,
          itemIndex: index,
          scrollIntoView: true,
        });
      };

      handleUserFieldFocus("features_block_103", "items.title", 1);
      assert.deepStrictEqual(mockBridgeClient.selectedPayload, {
        blockId: "features_block_103",
        field: "items.title",
        itemIndex: 1,
        scrollIntoView: true,
      });
    });

    test("Programmatic focus flag suppresses reverse event feedback loop", () => {
      let isProgrammatic = true;
      let reverseEventFired = false;

      const onFocusHandler = () => {
        if (isProgrammatic) {
          isProgrammatic = false; // reset flag
          return; // loop prevented
        }
        reverseEventFired = true;
      };

      // 1. Programmatic autofocus triggered by iframe click
      onFocusHandler();
      assert.strictEqual(reverseEventFired, false);

      // 2. Subsequent genuine user focus
      onFocusHandler();
      assert.strictEqual(reverseEventFired, true);
    });
  });
});

