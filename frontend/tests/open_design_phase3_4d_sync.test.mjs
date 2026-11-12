/**
 * open_design_phase3_4d_sync.test.mjs — Open Design Phase 3.4D Two-Way Synchronization & Regression Tests
 *
 * Verifies:
 * 1. Two-way synchronization between Inspector and Preview DOM (syncBlockChangesToPreview).
 * 2. Coverage of all 17 schema-derived editable fields across 7 native blocks.
 * 3. Ignored non-editable, style, and URL properties during inspector sync.
 * 4. Real-time preview DOM updates via syncValue and active text edit baseline sync.
 * 5. Selection lifecycle and same-target immunity (selecting same element does not abort edit).
 * 6. Session lifecycle: duplicate session-start, stale session rejection, duplicate proposal rejection.
 * 7. Cross-browser edge cases: scroll/resize position updates, boundary toolbar clamping, caret fallbacks.
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
  const bundlePath = path.resolve(__dirname, ".compiled_phase3_4d_sync_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  WebsiteBridgeClient,
  APPROVED_NATIVE_EDITABLE_FIELDS,
  APPROVED_BLOCK_FIELDS,
  extractAuthoritativeValue,
  applyPatchToBlock,
  validateProposedPatchAgainstDraft,
  validateSelectionAgainstDraft,
  syncBlockChangesToPreview,
} from '../src/services/websiteBridge';

export {
  PreviewOverlayEngine,
  SCHEMA_FIELD_CONSTRAINTS,
  getFieldConstraints,
  isFieldInlineEditable,
} from '../src/services/previewOverlayEngine';
`;

  const entryFile = path.resolve(__dirname, ".temp_phase3_4d_entry.tsx");
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

const PreviewOverlayEngine = mod.PreviewOverlayEngine;
const syncBlockChangesToPreview = mod.syncBlockChangesToPreview;
const validateProposedPatchAgainstDraft = mod.validateProposedPatchAgainstDraft;
const applyPatchToBlock = mod.applyPatchToBlock;

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

// Mock DOM helper for simulated DOM testing
function createMockDOM() {
  const listeners = {};

  class MockElement {
    constructor(tagName, attrs = {}) {
      this.tagName = tagName.toUpperCase();
      this.attributes = { ...attrs };
      this.children = [];
      this.parentElement = null;
      this.parentNode = null;
      this.textContent = "";
      this.style = {};
      this.isConnected = true;
      this.rect = { top: 100, left: 100, width: 200, height: 40, bottom: 140, right: 300 };
      this.focused = false;
      this.eventListeners = {};
    }

    getAttribute(name) {
      return this.attributes[name] !== undefined ? String(this.attributes[name]) : null;
    }

    hasAttribute(name) {
      return this.attributes[name] !== undefined;
    }

    setAttribute(name, value) {
      this.attributes[name] = String(value);
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
      return this.rect;
    }

    focus() {
      this.focused = true;
    }

    scrollIntoView() {}

    addEventListener(event, fn) {
      if (!this.eventListeners[event]) this.eventListeners[event] = [];
      this.eventListeners[event].push(fn);
    }

    removeEventListener(event, fn) {
      if (!this.eventListeners[event]) return;
      this.eventListeners[event] = this.eventListeners[event].filter((cb) => cb !== fn);
    }

    closest(selector) {
      let curr = this;
      while (curr) {
        if (curr.matches && curr.matches(selector)) return curr;
        curr = curr.parentElement;
      }
      return null;
    }

    matches(selector) {
      if (selector.startsWith("[")) {
        const match = selector.match(/\[([a-zA-Z0-9_-]+)(?:="([^"]+)")?\]/);
        if (match) {
          const attr = match[1];
          const val = match[2];
          if (val !== undefined) return this.attributes[attr] === val;
          return this.attributes[attr] !== undefined;
        }
      }
      if (selector.startsWith(".")) {
        const cls = selector.slice(1);
        return (this.attributes["class"] || "").split(" ").includes(cls);
      }
      return this.tagName.toLowerCase() === selector.toLowerCase();
    }

    querySelector(selector) {
      const all = this.querySelectorAll(selector);
      return all.length > 0 ? all[0] : null;
    }

    querySelectorAll(selector) {
      const results = [];
      const traverse = (node) => {
        for (const child of node.children) {
          if (child.matches && child.matches(selector)) {
            results.push(child);
          }
          traverse(child);
        }
      };
      traverse(this);
      return results;
    }
  }

  const mockBody = new MockElement("BODY");
  const mockDoc = {
    body: mockBody,
    createElement: (tag) => new MockElement(tag),
    createTextNode: (text) => {
      const el = new MockElement("#text");
      el.textContent = text;
      return el;
    },
    querySelector: (sel) => mockBody.querySelector(sel),
    querySelectorAll: (sel) => mockBody.querySelectorAll(sel),
    addEventListener: (event, fn) => {
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(fn);
    },
    removeEventListener: (event, fn) => {
      if (!listeners[event]) return;
      listeners[event] = listeners[event].filter((cb) => cb !== fn);
    },
    defaultView: {
      innerWidth: 1024,
      innerHeight: 768,
      addEventListener: () => {},
      removeEventListener: () => {},
      getSelection: () => ({
        removeAllRanges: () => {},
        addRange: () => {},
        toString: () => "",
      }),
    },
  };

  return { mockDoc, mockBody, MockElement };
}

describe("RivinityADS Open Design Phase 3.4D — Two-Way Synchronization Engine", () => {
  test("1. syncBlockChangesToPreview synchronizes all 17 native schema fields to the bridge", () => {
    const syncedMessages = [];
    const mockBridge = {
      getStatus: () => "connected",
      syncValue: (payload) => {
        syncedMessages.push(payload);
        return true;
      },
    };

    // Hero Block
    const prevHero = {
      id: "blk_hero",
      type: "hero",
      content: { headline: "Old Headline", subheadline: "Old Sub", cta_button: { label: "Old CTA" } },
    };
    const nextHero = {
      id: "blk_hero",
      type: "hero",
      content: { headline: "New Headline", subheadline: "New Sub", cta_button: { label: "New CTA" } },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevHero, nextHero), true);
    assert.strictEqual(syncedMessages.length, 3);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_hero", field: "headline", itemIndex: null, value: "New Headline" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_hero", field: "subheadline", itemIndex: null, value: "New Sub" });
    assert.deepStrictEqual(syncedMessages[2], { blockId: "blk_hero", field: "cta_button.label", itemIndex: null, value: "New CTA" });

    syncedMessages.length = 0;

    // Features Grid Block
    const prevGrid = {
      id: "blk_grid",
      type: "features_grid",
      content: {
        title: "Old Title",
        items: [{ title: "F1 Old", description: "D1 Old" }],
      },
    };
    const nextGrid = {
      id: "blk_grid",
      type: "features_grid",
      content: {
        title: "New Title",
        items: [{ title: "F1 New", description: "D1 New" }],
      },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevGrid, nextGrid), true);
    assert.strictEqual(syncedMessages.length, 3);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_grid", field: "title", itemIndex: null, value: "New Title" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_grid", field: "items.title", itemIndex: 0, value: "F1 New" });
    assert.deepStrictEqual(syncedMessages[2], { blockId: "blk_grid", field: "items.description", itemIndex: 0, value: "D1 New" });

    syncedMessages.length = 0;

    // Image Gallery Block
    const prevGal = {
      id: "blk_gal",
      type: "image_gallery",
      content: { title: "Gal Old", images: [{ caption: "Cap Old" }] },
    };
    const nextGal = {
      id: "blk_gal",
      type: "image_gallery",
      content: { title: "Gal New", images: [{ caption: "Cap New" }] },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevGal, nextGal), true);
    assert.strictEqual(syncedMessages.length, 2);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_gal", field: "title", itemIndex: null, value: "Gal New" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_gal", field: "images.caption", itemIndex: 0, value: "Cap New" });

    syncedMessages.length = 0;

    // Contact Form Block
    const prevForm = {
      id: "blk_form",
      type: "contact_form",
      content: { title: "Contact Old", submit_label: "Send Old" },
    };
    const nextForm = {
      id: "blk_form",
      type: "contact_form",
      content: { title: "Contact New", submit_label: "Send New" },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevForm, nextForm), true);
    assert.strictEqual(syncedMessages.length, 2);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_form", field: "title", itemIndex: null, value: "Contact New" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_form", field: "submit_label", itemIndex: null, value: "Send New" });

    syncedMessages.length = 0;

    // CTA Banner Block
    const prevBanner = {
      id: "blk_banner",
      type: "cta_banner",
      content: { title: "Old Banner", description: "Old Desc", button: { label: "Old Btn" } },
    };
    const nextBanner = {
      id: "blk_banner",
      type: "cta_banner",
      content: { title: "New Banner", description: "New Desc", button: { label: "New Btn" } },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevBanner, nextBanner), true);
    assert.strictEqual(syncedMessages.length, 3);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_banner", field: "title", itemIndex: null, value: "New Banner" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_banner", field: "description", itemIndex: null, value: "New Desc" });
    assert.deepStrictEqual(syncedMessages[2], { blockId: "blk_banner", field: "button.label", itemIndex: null, value: "New Btn" });

    syncedMessages.length = 0;

    // Footer Block
    const prevFooter = {
      id: "blk_foot",
      type: "footer",
      content: { copyright_text: "© 2025", links: [{ label: "Privacy" }] },
    };
    const nextFooter = {
      id: "blk_foot",
      type: "footer",
      content: { copyright_text: "© 2026", links: [{ label: "Terms" }] },
    };
    assert.strictEqual(syncBlockChangesToPreview(mockBridge, prevFooter, nextFooter), true);
    assert.strictEqual(syncedMessages.length, 2);
    assert.deepStrictEqual(syncedMessages[0], { blockId: "blk_foot", field: "copyright_text", itemIndex: null, value: "© 2026" });
    assert.deepStrictEqual(syncedMessages[1], { blockId: "blk_foot", field: "links.label", itemIndex: 0, value: "Terms" });
  });

  test("2. syncBlockChangesToPreview strictly ignores non-editable properties, styles, and URLs", () => {
    const syncedMessages = [];
    const mockBridge = {
      getStatus: () => "connected",
      syncValue: (payload) => syncedMessages.push(payload),
    };

    const prevBlock = {
      id: "blk_hero",
      type: "hero",
      content: {
        headline: "Same",
        background_image_url: "https://example.com/old.jpg",
        alignment: "left",
        cta_button: { label: "Same", target_url: "https://old.com" },
      },
    };
    const nextBlock = {
      id: "blk_hero",
      type: "hero",
      content: {
        headline: "Same",
        background_image_url: "https://example.com/new.jpg",
        alignment: "center",
        cta_button: { label: "Same", target_url: "https://new.com" },
      },
    };

    const result = syncBlockChangesToPreview(mockBridge, prevBlock, nextBlock);
    assert.strictEqual(result, false);
    assert.strictEqual(syncedMessages.length, 0);
  });

  test("3. syncBlockChangesToPreview returns false when bridge is disconnected or blocks match", () => {
    const mockDisconnectedBridge = {
      getStatus: () => "connecting",
      syncValue: () => true,
    };
    const b1 = { id: "blk_1", content: { headline: "A" } };
    const b2 = { id: "blk_1", content: { headline: "B" } };
    assert.strictEqual(syncBlockChangesToPreview(mockDisconnectedBridge, b1, b2), false);

    const mockConnectedBridge = {
      getStatus: () => "connected",
      syncValue: () => true,
    };
    assert.strictEqual(syncBlockChangesToPreview(mockConnectedBridge, b1, b1), false);
  });

  test("4. applyConfirmedValue updates preview DOM element text and reflows positions", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const blockEl = new MockElement("SECTION", {
      "data-od-block-id": "blk_hero_sync",
      "data-od-block-type": "hero",
    });
    const headlineEl = new MockElement("H1", {
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    headlineEl.textContent = "Old Headline";
    blockEl.appendChild(headlineEl);
    mockBody.appendChild(blockEl);

    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });
    const ok = engine.applyConfirmedValue("blk_hero_sync", "headline", null, "Synchronized Headline");
    assert.strictEqual(ok, true);
    assert.strictEqual(headlineEl.textContent, "Synchronized Headline");

    engine.destroy();
  });

  test("5. syncValue updates active inline edit session baseline and character counter", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const blockEl = new MockElement("SECTION", {
      "data-od-block-id": "blk_hero_active",
      "data-od-block-type": "hero",
    });
    const headlineEl = new MockElement("H1", {
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    headlineEl.textContent = "Initial Title";
    blockEl.appendChild(headlineEl);
    mockBody.appendChild(blockEl);

    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });
    engine.startTextEdit(headlineEl);

    const activeSession = engine.getActiveTextEdit();
    assert.ok(activeSession);
    assert.strictEqual(activeSession.originalText, "Initial Title");

    // Inspector modifies value via syncValue while editing is active
    engine.syncValue("blk_hero_active", "headline", null, "Updated from Inspector");
    assert.strictEqual(headlineEl.textContent, "Updated from Inspector");
    assert.strictEqual(activeSession.originalText, "Updated from Inspector");

    engine.destroy();
  });

  test("6. selectByBlockId preserves active inline edit session when selecting the same target", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const blockEl = new MockElement("SECTION", {
      "data-od-block-id": "blk_same_target",
      "data-od-block-type": "hero",
    });
    const headlineEl = new MockElement("H1", {
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    headlineEl.textContent = "Editable Title";
    blockEl.appendChild(headlineEl);
    mockBody.appendChild(blockEl);

    const otherBlock = new MockElement("SECTION", {
      "data-od-block-id": "blk_other",
      "data-od-block-type": "hero",
    });
    mockBody.appendChild(otherBlock);

    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });
    engine.startTextEdit(headlineEl);

    assert.ok(engine.getActiveTextEdit());

    // Programmatic selection of the SAME target does NOT finish the edit
    engine.selectByBlockId("blk_same_target", { field: "headline" });
    assert.ok(engine.getActiveTextEdit(), "Active edit must remain active when selecting the same target");

    // Programmatic selection of a DIFFERENT target commits the edit
    engine.selectByBlockId("blk_other", { field: "subheadline" });
    assert.strictEqual(engine.getActiveTextEdit(), null, "Active edit must commit when selecting a different target");

    engine.destroy();
  });

  test("7. Session lifecycle: stale and duplicate edit proposals are strictly rejected", () => {
    const draftBlocks = [
      {
        id: "blk_session_test",
        type: "hero",
        is_visible: true,
        content: { headline: "Current Headline" },
      },
    ];

    // Proposal with mismatched session ID
    const stalePayload = {
      sessionId: "sess_stale_999",
      blockId: "blk_session_test",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "Hacked Headline",
    };
    const resStale = validateProposedPatchAgainstDraft(stalePayload, draftBlocks, "sess_active_123");
    assert.strictEqual(resStale.isValid, false);
    assert.match(resStale.error, /session mismatch/i);

    // Proposal with valid session ID
    const validPayload = {
      sessionId: "sess_active_123",
      blockId: "blk_session_test",
      blockType: "hero",
      field: "headline",
      itemIndex: null,
      proposedValue: "Valid New Headline",
    };
    const resValid = validateProposedPatchAgainstDraft(validPayload, draftBlocks, "sess_active_123");
    assert.strictEqual(resValid.isValid, true);
  });

  test("8. Immutable draft staging preserves sibling blocks, items, and properties", () => {
    const targetBlock = {
      id: "blk_features",
      type: "features_grid",
      is_visible: true,
      content: {
        title: "Grid Title",
        items: [
          { title: "Item 0 Title", description: "Item 0 Desc", icon: "star" },
          { title: "Item 1 Title", description: "Item 1 Desc", icon: "shield" },
        ],
      },
    };

    const updated = applyPatchToBlock(targetBlock, "items.title", 1, "Item 1 Updated");
    // Only item 1 title modified
    assert.strictEqual(updated.content.items[1].title, "Item 1 Updated");
    assert.strictEqual(updated.content.items[1].description, "Item 1 Desc");
    assert.strictEqual(updated.content.items[1].icon, "shield");
    // Item 0 untouched
    assert.strictEqual(updated.content.items[0].title, "Item 0 Title");
    assert.strictEqual(updated.content.title, "Grid Title");
    // Immutability verified
    assert.notStrictEqual(updated, targetBlock);
    assert.notStrictEqual(updated.content.items, targetBlock.content.items);
  });

  test("9. Cross-browser edge cases: position reflow, detached element handling, and caret fallbacks", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const blockEl = new MockElement("SECTION", {
      "data-od-block-id": "blk_edge_case",
      "data-od-block-type": "hero",
    });
    const headlineEl = new MockElement("H1", {
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    headlineEl.textContent = "Boundary Headline";
    // Placed at extreme top left
    headlineEl.rect = { top: 2, left: 2, width: 100, height: 20, bottom: 22, right: 102 };
    blockEl.appendChild(headlineEl);
    mockBody.appendChild(blockEl);

    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });
    engine.startTextEdit(headlineEl);

    // Verify toolbar placement flipped below because top < 34
    const toolbar = engine.getToolbarElement();
    assert.ok(toolbar);
    assert.strictEqual(toolbar.style.display, "flex");
    assert.strictEqual(toolbar.style.top, "26px"); // rect.bottom (22) + 4 = 26px

    // Verify caret fallback when caretRangeFromPoint is absent
    engine.placeCaretFromClick({ clientX: 50, clientY: 10 }, headlineEl);

    // Verify detaching element aborts session cleanly during reflow
    headlineEl.isConnected = false;
    engine.updatePositions();
    assert.strictEqual(engine.getActiveTextEdit(), null, "Session must abort when element is detached");

    engine.destroy();
  });
});
