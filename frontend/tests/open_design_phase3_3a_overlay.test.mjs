/**
 * open_design_phase3_3a_overlay.test.mjs — Phase 3.3A: Bridge Overlay & DOM Interaction Engine Test Suite
 *
 * Verifies:
 * 1. Overlay creation and duplicate initialization prevention (`ensureGuidesLayer`).
 * 2. Overlay styling and pointer-events: none behavior (never intercepts clicks).
 * 3. Editable element discovery for native blocks (hero, text, features, etc.).
 * 4. Invalid, malformed, and missing metadata rejection.
 * 5. Protected ancestor detection and immunity (ad placements, banners, honeypots).
 * 6. Overlay self-exclusion (elements inside [data-od-edit-guides-layer] rejected).
 * 7. Hover outline creation, badge formatting, and clearing.
 * 8. Selected-element outline, 4 corner handles, and clearing.
 * 9. Scroll and resize repositioning (`updatePositions`).
 * 10. Engine teardown and listener cleanup (`destroy`).
 * 11. Custom-section (open_design_section) outer wrapper discovery.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_phase3_3a_overlay_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export { PreviewOverlayEngine } from '../src/services/previewOverlayEngine';
export {
  extractElementMetadata,
  isElementProtected,
  PROTECTED_SELECTORS,
  VALID_BLOCK_TYPES,
  BLOCK_ID_REGEX,
} from '../src/services/websiteBridge';
`;

  const entryFile = path.resolve("./tests/.temp_phase3_3a_entry.tsx");
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

// Helper to construct mock DOM tree
function createMockDOM() {
  const listeners = new Map();
  const windowListeners = new Map();

  class MockElement {
    constructor(tagName, attrs = {}) {
      this.tagName = tagName.toUpperCase();
      this.attributes = { ...attrs };
      this.children = [];
      this.parentElement = null;
      this.isConnected = true;
      this.textContent = "";
      this.className = "";
      this.style = {};
      this._rect = { top: 50, left: 20, width: 200, height: 40 };
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
      child.parentNode = this;
      this.children.push(child);
      return child;
    }

    removeChild(child) {
      const idx = this.children.indexOf(child);
      if (idx !== -1) {
        this.children.splice(idx, 1);
        child.parentElement = null;
        child.parentNode = null;
        child.isConnected = false;
      }
      return child;
    }

    getBoundingClientRect() {
      return {
        top: this._rect.top,
        left: this._rect.left,
        width: this._rect.width,
        height: this._rect.height,
        bottom: this._rect.top + this._rect.height,
        right: this._rect.left + this._rect.width,
      };
    }

    setMockRect(rect) {
      this._rect = { ...this._rect, ...rect };
    }

    closest(selector) {
      let curr = this;
      while (curr) {
        if (curr.matchesSelector(selector)) return curr;
        curr = curr.parentElement;
      }
      return null;
    }

    contains(el) {
      let curr = el;
      while (curr) {
        if (curr === this) return true;
        curr = curr.parentElement;
      }
      return false;
    }

    matchesSelector(sel) {
      const parts = sel.split(",").map((s) => s.trim());
      for (const part of parts) {
        if (part.startsWith(".") && this.className && this.className.includes(part.slice(1))) {
          return true;
        }
        if (part.startsWith("#") && this.attributes.id === part.slice(1)) {
          return true;
        }
        if (part.startsWith("[") && part.endsWith("]")) {
          const inner = part.slice(1, -1);
          if (inner.includes("=")) {
            const [attrName, rawVal] = inner.split("=");
            const val = rawVal.replace(/['"]/g, "");
            if (this.getAttribute(attrName.trim()) === val) return true;
          } else {
            if (this.hasAttribute(inner.trim())) return true;
          }
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

  const mockBody = new MockElement("BODY");

  const mockDoc = {
    body: mockBody,
    createElement(tag) {
      return new MockElement(tag);
    },
    querySelector(selector) {
      if (selector === "body") return mockBody;
      return mockBody.querySelector(selector);
    },
    addEventListener(evt, handler) {
      if (!listeners.has(evt)) listeners.set(evt, []);
      listeners.get(evt).push(handler);
    },
    removeEventListener(evt, handler) {
      if (!listeners.has(evt)) return;
      const list = listeners.get(evt).filter((h) => h !== handler);
      listeners.set(evt, list);
    },
    defaultView: {
      addEventListener(evt, handler) {
        if (!windowListeners.has(evt)) windowListeners.set(evt, []);
        windowListeners.get(evt).push(handler);
      },
      removeEventListener(evt, handler) {
        if (!windowListeners.has(evt)) return;
        const list = windowListeners.get(evt).filter((h) => h !== handler);
        windowListeners.set(evt, list);
      },
    },
  };

  return { mockDoc, mockBody, listeners, windowListeners, MockElement };
}

describe("RivinityADS Open Design Phase 3.3A — Bridge Overlay & DOM Interaction Engine", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const { PreviewOverlayEngine, isElementProtected } = mod;

  describe("1. Overlay Guide Layer Creation & Duplicate Prevention", () => {
    test("ensureGuidesLayer creates single container with data-od-edit-guides-layer='true'", () => {
      const { mockDoc, mockBody } = createMockDOM();
      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });

      const layer = engine.ensureGuidesLayer();
      assert.ok(layer, "Guides layer must be created");
      assert.strictEqual(layer.getAttribute("data-od-edit-guides-layer"), "true");
      assert.strictEqual(layer.getAttribute("aria-hidden"), "true");
      assert.strictEqual(layer.style.position, "fixed");
      assert.strictEqual(layer.style.pointerEvents, "none");
      assert.strictEqual(layer.style.zIndex, "999999");
      assert.strictEqual(mockBody.children.length, 1);
    });

    test("Calling ensureGuidesLayer multiple times reuses existing layer without creating duplicates", () => {
      const { mockDoc, mockBody } = createMockDOM();
      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });

      const layer1 = engine.ensureGuidesLayer();
      const layer2 = engine.ensureGuidesLayer();
      assert.strictEqual(layer1, layer2, "Must return exact same container instance");
      assert.strictEqual(mockBody.children.length, 1, "Must never duplicate layer in DOM");
    });
  });

  describe("2. DOM Discovery & Metadata Extraction", () => {
    test("Discovers native editable elements with valid metadata", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const section = new MockElement("SECTION", {
        "data-od-block-id": "blk_hero_1",
        "data-od-block-type": "hero",
      });
      const h1 = new MockElement("H1", {
        "data-od-block-id": "blk_hero_1",
        "data-od-block-type": "hero",
        "data-od-field": "headline",
        "data-od-editable": "true",
      });
      section.appendChild(h1);
      mockBody.appendChild(section);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const target = engine.findSemanticTarget(h1);

      assert.strictEqual(target, h1, "Should discover h1 as semantic target");
    });

    test("Bubbles to nearest block container when element has no field attribute", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const section = new MockElement("SECTION", {
        "data-od-block-id": "blk_hero_2",
        "data-od-block-type": "hero",
      });
      const div = new MockElement("DIV", { class: "container" });
      section.appendChild(div);
      mockBody.appendChild(section);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const target = engine.findSemanticTarget(div);

      assert.strictEqual(target, section, "Should bubble up to nearest block container");
    });

    test("Rejects malformed block IDs and missing block types", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const invalidSection = new MockElement("SECTION", {
        "data-od-block-id": "invalid id with spaces!!",
        "data-od-block-type": "hero",
      });
      const p = new MockElement("P", { "data-od-field": "headline" });
      invalidSection.appendChild(p);
      mockBody.appendChild(invalidSection);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const target = engine.findSemanticTarget(p);
      assert.strictEqual(target, null, "Should reject malformed block IDs");
    });
  });

  describe("3. Protected Element Immunity & Self-Exclusion", () => {
    test("Overlay elements strictly reject themselves from semantic discovery", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const layer = engine.ensureGuidesLayer();

      const innerBox = new MockElement("DIV", { class: "od-guide-box" });
      layer.appendChild(innerBox);

      assert.strictEqual(engine.findSemanticTarget(layer), null, "Layer must reject itself");
      assert.strictEqual(engine.findSemanticTarget(innerBox), null, "Layer children must be rejected");
    });

    test("Ad placements return block container with isProtected=true and isEditable=false", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const adSection = new MockElement("DIV", {
        class: "block-ad-placement",
        "data-od-block-id": "blk_ad_1",
        "data-od-block-type": "ad_placement",
        "data-od-protected": "true",
        "data-od-editable": "false",
      });
      const placeholder = new MockElement("DIV", { class: "rivinity-ad-placeholder" });
      const badge = new MockElement("SPAN", { class: "ad-badge" });
      placeholder.appendChild(badge);
      adSection.appendChild(placeholder);
      mockBody.appendChild(adSection);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const target = engine.findSemanticTarget(badge);

      assert.strictEqual(target, adSection, "Ad placeholder click bubbles to ad block container");

      const success = engine.setHoverTarget(target);
      assert.strictEqual(success, true);
      const active = engine.getActiveHoverTarget();
      assert.ok(active);
      assert.strictEqual(active.metadata.isProtected, true, "Must be flagged protected");
      assert.strictEqual(active.metadata.isEditable, false, "Protected element must not be editable");
    });

    test("Platform notices and honeypot are strictly excluded from discovery", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const banner = new MockElement("DIV", { class: "preview-banner" });
      const consent = new MockElement("ASIDE", { id: "rivinity-consent-banner" });
      const honeypot = new MockElement("INPUT", { name: "hp_comment" });
      mockBody.appendChild(banner);
      mockBody.appendChild(consent);
      mockBody.appendChild(honeypot);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });

      assert.strictEqual(engine.findSemanticTarget(banner), null, "Preview banner must not be discoverable");
      assert.strictEqual(engine.findSemanticTarget(consent), null, "Consent banner must not be discoverable");
      assert.strictEqual(engine.findSemanticTarget(honeypot), null, "Honeypot input must not be discoverable");
    });
  });

  describe("4. Hover & Selection Visual Overlays", () => {
    test("Hover outline is created and positioned without mutating target inline styles", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const h1 = new MockElement("H1", {
        "data-od-block-id": "blk_h1",
        "data-od-block-type": "hero",
        "data-od-field": "headline",
        "data-od-editable": "true",
      });
      h1.setMockRect({ top: 100, left: 40, width: 300, height: 60 });
      mockBody.appendChild(h1);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const ok = engine.setHoverTarget(h1);

      assert.strictEqual(ok, true);
      assert.strictEqual(h1.style.outline, undefined, "Target inline style.outline must never be mutated");

      const layer = engine.getGuidesLayer();
      const hoverBox = layer.querySelector(".od-guide-hover");
      assert.ok(hoverBox, "Hover box must be added to guides layer");
      assert.strictEqual(hoverBox.style.top, "100px");
      assert.strictEqual(hoverBox.style.left, "40px");
      assert.strictEqual(hoverBox.style.width, "300px");
      assert.strictEqual(hoverBox.style.height, "60px");
      assert.strictEqual(hoverBox.style.display, "block");

      const badge = hoverBox.querySelector(".od-guide-badge");
      assert.ok(badge, "Badge must be rendered");
      assert.strictEqual(badge.textContent, "Hero • headline");

      // Clear hover
      engine.clearHover();
      assert.strictEqual(hoverBox.style.display, "none", "Hover box must be hidden on clear");
      assert.strictEqual(engine.getActiveHoverTarget(), null);
    });

    test("Selected outline renders solid border, badge, and 4 corner handles", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const featCard = new MockElement("DIV", {
        "data-od-block-id": "blk_feat_1",
        "data-od-block-type": "features_grid",
        "data-od-field": "items.title",
        "data-od-item-index": "2",
        "data-od-editable": "true",
      });
      featCard.setMockRect({ top: 250, left: 60, width: 220, height: 120 });
      mockBody.appendChild(featCard);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const ok = engine.setSelectedTarget(featCard);

      assert.strictEqual(ok, true);
      const layer = engine.getGuidesLayer();
      const selBox = layer.querySelector(".od-guide-selected");
      assert.ok(selBox, "Selected box must be rendered");
      assert.strictEqual(selBox.style.top, "250px");
      assert.strictEqual(selBox.style.left, "60px");

      const badge = selBox.querySelector(".od-guide-badge");
      assert.ok(badge);
      assert.strictEqual(badge.textContent, "Features Grid • items.title[2]");

      const handles = selBox.children.filter((c) => c.className && c.className.includes("od-guide-handle"));
      assert.strictEqual(handles.length, 4, "Must contain exactly 4 corner handles");

      engine.clearSelected();
      assert.strictEqual(selBox.style.display, "none");
    });
  });

  describe("5. Scroll / Resize Repositioning & Teardown", () => {
    test("updatePositions recalculates bounding box coordinates smoothly", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const btn = new MockElement("A", {
        "data-od-block-id": "blk_cta_1",
        "data-od-block-type": "cta_banner",
        "data-od-field": "button.label",
        "data-od-editable": "true",
      });
      btn.setMockRect({ top: 300, left: 100, width: 140, height: 45 });
      mockBody.appendChild(btn);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      engine.setSelectedTarget(btn);

      const layer = engine.getGuidesLayer();
      const selBox = layer.querySelector(".od-guide-selected");
      assert.strictEqual(selBox.style.top, "300px");

      // Simulate viewport scroll or resize reflow
      btn.setMockRect({ top: 150, left: 80, width: 140, height: 45 });
      engine.updatePositions();

      assert.strictEqual(selBox.style.top, "150px", "Selected box top must update on reflow");
      assert.strictEqual(selBox.style.left, "80px", "Selected box left must update on reflow");
    });

    test("destroy() removes guides layer and cleans up event listeners", () => {
      const { mockDoc, mockBody, listeners, windowListeners } = createMockDOM();
      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: true });

      assert.ok(mockBody.children.length > 0, "Guides layer exists");
      assert.ok(listeners.get("pointerover")?.length > 0);
      assert.ok(windowListeners.get("scroll")?.length > 0);

      engine.destroy();

      assert.strictEqual(mockBody.children.length, 0, "Guides layer must be removed from body");
      assert.strictEqual(listeners.get("pointerover")?.length, 0, "Listeners must be detached");
      assert.strictEqual(windowListeners.get("scroll")?.length, 0, "Window listeners must be detached");
    });
  });

  describe("6. Custom Section (open_design_section) Discovery", () => {
    test("Inner user-authored HTML bubbles strictly to outer wrapper element", () => {
      const { mockDoc, mockBody, MockElement } = createMockDOM();
      const wrapper = new MockElement("DIV", {
        class: "block-open-design-wrapper",
        "data-od-block-id": "blk_od_custom",
        "data-od-block-type": "open_design_section",
      });
      const section = new MockElement("SECTION", {
        class: "od-section",
        "data-od-block-id": "blk_od_custom",
        "data-od-block-type": "open_design_section",
      });
      const innerCard = new MockElement("DIV", { class: "inner-card" });
      const innerHeading = new MockElement("H3", {});
      innerCard.appendChild(innerHeading);
      section.appendChild(innerCard);
      wrapper.appendChild(section);
      mockBody.appendChild(wrapper);

      const engine = new PreviewOverlayEngine({ rootDocument: mockDoc, enablePointerEvents: false });
      const target = engine.findSemanticTarget(innerHeading);

      assert.strictEqual(target, section, "Inner click must resolve to trusted outer section wrapper");

      engine.setHoverTarget(target);
      const active = engine.getActiveHoverTarget();
      assert.ok(active);
      assert.strictEqual(active.metadata.blockType, "open_design_section");
      assert.strictEqual(active.metadata.field, null);
    });
  });
});
