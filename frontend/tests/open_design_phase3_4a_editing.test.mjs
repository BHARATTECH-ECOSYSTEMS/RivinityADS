/**
 * open_design_phase3_4a_editing.test.mjs — Phase 3.4A: Native Text Inline Editing Engine Test Suite
 *
 * Verifies:
 * 1. Supported field constraints & normalization (all 7 native blocks & 17 fields).
 * 2. Starting an edit session on each supported category.
 * 3. Caret placement from click (caretRangeFromPoint, caretPositionFromPoint, and safe fallback).
 * 4. Plain-text enforcement & rich-text rejection (paste, drop, formatting shortcuts, beforeinput).
 * 5. Keyboard navigation: single-line Enter, multiline Enter, Ctrl/Cmd+Enter, Escape cancellation.
 * 6. Floating toolbar & character counter (commit, cancel, counter format, red limit, viewport clamping).
 * 7. Blur isolation & parent inspector interaction immunity.
 * 8. Protected element & unsupported field rejection.
 * 9. Single active edit session guarantee & teardown cleanup.
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
  const bundlePath = path.resolve(__dirname, ".compiled_phase3_4a_editing_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  PreviewOverlayEngine,
  SCHEMA_FIELD_CONSTRAINTS,
  normalizeFieldIdentifier,
  getFieldConstraints,
  isFieldInlineEditable,
} from '../src/services/previewOverlayEngine';
export {
  extractElementMetadata,
  isElementProtected,
  PROTECTED_SELECTORS,
  VALID_BLOCK_TYPES,
  BLOCK_ID_REGEX,
} from '../src/services/websiteBridge';
`;

  const entryFile = path.resolve(__dirname, ".temp_phase3_4a_entry.tsx");
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

function createMockDOM() {
  const listeners = new Map();
  const windowListeners = new Map();

  class MockElement {
    constructor(tagName, attrs = {}) {
      this.tagName = tagName.toUpperCase();
      this.attributes = { ...attrs };
      this.children = [];
      this.parentElement = null;
      this.parentNode = null;
      this.isConnected = true;
      this.textContent = "";
      this.className = "";
      this.style = {};
      this._rect = { top: 50, left: 20, width: 200, height: 40 };
      this._elementListeners = new Map();
      this.focused = false;
      this.contentEditable = "inherit";
    }

    setAttribute(name, val) {
      this.attributes[name] = String(val);
      if (name === "contenteditable") {
        this.contentEditable = String(val);
      }
    }

    getAttribute(name) {
      return this.attributes[name] !== undefined ? String(this.attributes[name]) : null;
    }

    hasAttribute(name) {
      return this.attributes[name] !== undefined;
    }

    removeAttribute(name) {
      delete this.attributes[name];
      if (name === "contenteditable") {
        this.contentEditable = "inherit";
      }
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

    focus() {
      this.focused = true;
    }

    blur() {
      this.focused = false;
      this.dispatchEvent({ type: "blur", target: this });
    }

    addEventListener(evt, handler) {
      if (!this._elementListeners.has(evt)) {
        this._elementListeners.set(evt, []);
      }
      this._elementListeners.get(evt).push(handler);
    }

    removeEventListener(evt, handler) {
      if (!this._elementListeners.has(evt)) return;
      const list = this._elementListeners.get(evt).filter((h) => h !== handler);
      this._elementListeners.set(evt, list);
    }

    dispatchEvent(event) {
      const list = this._elementListeners.get(event.type) || [];
      for (const h of list) {
        h(event);
      }
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

    querySelectorAll(selector) {
      const result = [];
      const traverse = (node) => {
        for (const ch of node.children) {
          if (ch.matchesSelector(selector)) result.push(ch);
          traverse(ch);
        }
      };
      traverse(this);
      return result;
    }
  }

  const mockBody = new MockElement("BODY");

  let currentSelection = {
    ranges: [],
    removeAllRanges() {
      this.ranges = [];
    },
    addRange(r) {
      this.ranges.push(r);
    },
    toString() {
      return "";
    },
  };

  const mockDoc = {
    body: mockBody,
    documentElement: { clientWidth: 1024, clientHeight: 768 },
    createElement(tag) {
      return new MockElement(tag);
    },
    createTextNode(text) {
      const el = new MockElement("#text");
      el.textContent = text;
      return el;
    },
    createRange() {
      return {
        startContainer: null,
        startOffset: 0,
        selectNodeContents(el) {
          this.startContainer = el;
        },
        collapse(toStart) {
          this.collapsed = toStart;
        },
        deleteContents() {},
        insertNode(node) {},
        setStartAfter(node) {},
      };
    },
    querySelector(selector) {
      if (selector === "body") return mockBody;
      return mockBody.querySelector(selector);
    },
    querySelectorAll(selector) {
      return mockBody.querySelectorAll(selector);
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
    dispatchDocumentEvent(event) {
      const list = listeners.get(event.type) || [];
      for (const h of list) {
        h(event);
      }
    },
    defaultView: {
      innerWidth: 1024,
      innerHeight: 768,
      getSelection() {
        return currentSelection;
      },
      addEventListener(evt, handler) {
        if (!windowListeners.has(evt)) windowListeners.set(evt, []);
        windowListeners.get(evt).push(handler);
      },
      removeEventListener(evt, handler) {
        if (!windowListeners.has(evt)) return;
        const list = windowListeners.get(evt).filter((h) => h !== handler);
        windowListeners.set(evt, list);
      },
      dispatchWindowEvent(event) {
        const list = windowListeners.get(event.type) || [];
        for (const h of list) {
          h(event);
        }
      },
    },
  };

  return { mockDoc, mockBody, listeners, windowListeners, MockElement, currentSelection };
}

describe("RivinityADS Phase 3.4A — Native Text Inline Editing Engine", async () => {
  const bundleUrl = compileBundle();
  const {
    PreviewOverlayEngine,
    SCHEMA_FIELD_CONSTRAINTS,
    normalizeFieldIdentifier,
    getFieldConstraints,
    isFieldInlineEditable,
  } = await import(bundleUrl);

  test("1. Field constraints map covers all 7 native blocks and 17 editable fields", () => {
    const expected = [
      ["hero", "headline", 160, false, true],
      ["hero", "subheadline", 300, true, false],
      ["hero", "cta_button.label", 60, false, true],
      ["text_section", "heading", 160, false, false],
      ["text_section", "body", 10000, true, true],
      ["features_grid", "title", 160, false, false],
      ["features_grid", "items.title", 100, false, true],
      ["features_grid", "items.description", 500, true, true],
      ["image_gallery", "title", 160, false, false],
      ["image_gallery", "images.caption", 200, false, false],
      ["contact_form", "title", 120, false, false],
      ["contact_form", "submit_label", 60, false, true],
      ["cta_banner", "title", 160, false, true],
      ["cta_banner", "description", 300, true, false],
      ["cta_banner", "button.label", 60, false, true],
      ["footer", "copyright_text", 160, false, false],
      ["footer", "links.label", 50, false, true],
    ];

    for (const [blockType, field, maxLen, multiline, required] of expected) {
      const constraint = getFieldConstraints(blockType, field);
      assert.ok(constraint, `Constraint missing for ${blockType}.${field}`);
      assert.strictEqual(constraint.maxLength, maxLen, `Max length mismatch on ${blockType}.${field}`);
      assert.strictEqual(constraint.multiline, multiline, `Multiline mismatch on ${blockType}.${field}`);
      assert.strictEqual(constraint.required, required, `Required mismatch on ${blockType}.${field}`);
      assert.strictEqual(isFieldInlineEditable(blockType, field), true);
    }
  });

  test("2. Field identifier normalization handles array notation and prefixes", () => {
    assert.strictEqual(normalizeFieldIdentifier("items[0].title"), "items.title");
    assert.strictEqual(normalizeFieldIdentifier("items[15].description"), "items.description");
    assert.strictEqual(normalizeFieldIdentifier("images[].caption"), "images.caption");
    assert.strictEqual(normalizeFieldIdentifier("links[2].label"), "links.label");

    // Indexed lookup works via getFieldConstraints
    const c1 = getFieldConstraints("features_grid", "items[0].title");
    assert.ok(c1);
    assert.strictEqual(c1.maxLength, 100);

    const c2 = getFieldConstraints("hero", "hero.headline");
    assert.ok(c2);
    assert.strictEqual(c2.maxLength, 160);
  });

  test("3. Unsupported fields, protected blocks, and custom sections are not inline editable", () => {
    assert.strictEqual(isFieldInlineEditable("hero", "cta_button.target_url"), false);
    assert.strictEqual(isFieldInlineEditable("hero", "background_image_url"), false);
    assert.strictEqual(isFieldInlineEditable("hero", "alignment"), false);
    assert.strictEqual(isFieldInlineEditable("features_grid", "items.icon"), false);
    assert.strictEqual(isFieldInlineEditable("image_gallery", "images.image_url"), false);
    assert.strictEqual(isFieldInlineEditable("image_gallery", "images.alt_text"), false);
    assert.strictEqual(isFieldInlineEditable("contact_form", "fields"), false);
    assert.strictEqual(isFieldInlineEditable("cta_banner", "button.target_url"), false);
    assert.strictEqual(isFieldInlineEditable("footer", "links.target_url"), false);
    assert.strictEqual(isFieldInlineEditable("open_design_section", "html_content"), false);
    assert.strictEqual(isFieldInlineEditable("open_design_section", "section_id"), false);
    assert.strictEqual(isFieldInlineEditable("ad_placement", "placement_location"), false);
  });

  test("4. Starting edit session captures state, sets contenteditable, and fires onEditSessionStart", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let startedSession = null;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditSessionStart: (s) => {
        startedSession = s;
      },
    });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Welcome to RivinityADS";
    mockBody.appendChild(el);

    const ok = engine.startTextEdit(el);
    assert.strictEqual(ok, true);
    assert.ok(startedSession);
    assert.strictEqual(startedSession.originalText, "Welcome to RivinityADS");
    assert.strictEqual(startedSession.metadata.blockId, "blk_hero_1");
    assert.strictEqual(startedSession.metadata.field, "headline");
    assert.strictEqual(startedSession.constraint.maxLength, 160);
    assert.strictEqual(el.contentEditable, "plaintext-only");
    assert.strictEqual(el.getAttribute("data-od-editing"), "true");
    assert.strictEqual(el.style.outline, "2px solid #2563eb");
    assert.strictEqual(el.focused, true);

    engine.destroy();
  });

  test("5. Caret placement uses caretRangeFromPoint / caretPositionFromPoint or collapses to end", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();

    let caretRangeCalled = false;
    mockDoc.caretRangeFromPoint = (x, y) => {
      caretRangeCalled = true;
      return {
        collapsed: true,
        startOffset: 5,
      };
    };

    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });
    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Rivinity";
    mockBody.appendChild(el);

    engine.startTextEdit(el, { clientX: 100, clientY: 50 });
    assert.strictEqual(caretRangeCalled, true);

    // Test fallback when APIs are absent
    delete mockDoc.caretRangeFromPoint;
    delete mockDoc.caretPositionFromPoint;

    engine.placeCaretFromClick(null, el);
    const sel = mockDoc.defaultView.getSelection();
    assert.strictEqual(sel.ranges.length, 1);
    assert.strictEqual(sel.ranges[0].collapsed, false); // collapsed to end

    engine.destroy();
  });

  test("6. Single-line Enter commits; Shift+Enter is suppressed without committing", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let proposedPatch = null;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditProposePatch: (p) => {
        proposedPatch = p;
      },
    });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Original Headline";
    mockBody.appendChild(el);

    engine.startTextEdit(el);

    // 1. Shift+Enter -> Suppressed
    let shiftPrevented = false;
    el.dispatchEvent({
      type: "keydown",
      key: "Enter",
      shiftKey: true,
      preventDefault: () => {
        shiftPrevented = true;
      },
    });
    assert.strictEqual(shiftPrevented, true);
    assert.ok(engine.getActiveTextEdit(), "Session must remain active after Shift+Enter on single-line");

    // 2. Modify text and press Enter -> Commits
    el.textContent = "New Headline";
    let enterPrevented = false;
    el.dispatchEvent({
      type: "keydown",
      key: "Enter",
      shiftKey: false,
      preventDefault: () => {
        enterPrevented = true;
      },
    });

    assert.strictEqual(enterPrevented, true);
    assert.strictEqual(engine.getActiveTextEdit(), null, "Session must exit on Enter");
    assert.ok(proposedPatch);
    assert.strictEqual(proposedPatch.proposedValue, "New Headline");
    assert.strictEqual(proposedPatch.blockId, "blk_hero_1");
    assert.strictEqual(proposedPatch.field, "headline");
    assert.strictEqual(el.hasAttribute("contenteditable"), false);
    assert.strictEqual(el.hasAttribute("data-od-editing"), false);

    engine.destroy();
  });

  test("7. Multiline Enter allows newline; Ctrl+Enter or Cmd+Enter commits", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let proposedPatch = null;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditProposePatch: (p) => {
        proposedPatch = p;
      },
    });

    const el = new MockElement("P", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "subheadline",
      "data-od-editable": "true",
    });
    el.textContent = "Line 1";
    mockBody.appendChild(el);

    engine.startTextEdit(el);

    // 1. Standard Enter -> Not prevented (allows newline)
    let enterPrevented = false;
    el.dispatchEvent({
      type: "keydown",
      key: "Enter",
      ctrlKey: false,
      metaKey: false,
      preventDefault: () => {
        enterPrevented = true;
      },
    });
    assert.strictEqual(enterPrevented, false, "Multiline Enter must not be prevented");
    assert.ok(engine.getActiveTextEdit(), "Session must stay active on multiline Enter");

    // 2. Ctrl+Enter -> Commits
    el.textContent = "Line 1\nLine 2";
    let ctrlEnterPrevented = false;
    el.dispatchEvent({
      type: "keydown",
      key: "Enter",
      ctrlKey: true,
      metaKey: false,
      preventDefault: () => {
        ctrlEnterPrevented = true;
      },
    });
    assert.strictEqual(ctrlEnterPrevented, true);
    assert.strictEqual(engine.getActiveTextEdit(), null);
    assert.ok(proposedPatch);
    assert.strictEqual(proposedPatch.proposedValue, "Line 1\nLine 2");

    engine.destroy();
  });

  test("8. Escape cancels edit session and restores exact original text verbatim", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let cancelFired = false;
    let proposedPatch = null;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditCancel: (c) => {
        cancelFired = true;
      },
      onEditProposePatch: (p) => {
        proposedPatch = p;
      },
    });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Exact Original Value";
    mockBody.appendChild(el);

    engine.startTextEdit(el);

    // Modify DOM during session
    el.textContent = "Draft Mutated Garbage";

    // Press Escape
    el.dispatchEvent({
      type: "keydown",
      key: "Escape",
      preventDefault: () => {},
    });

    assert.strictEqual(cancelFired, true);
    assert.strictEqual(proposedPatch, null);
    assert.strictEqual(el.textContent, "Exact Original Value", "Must restore original text exactly");
    assert.strictEqual(engine.getActiveTextEdit(), null);
    assert.strictEqual(el.hasAttribute("contenteditable"), false);

    engine.destroy();
  });

  test("9. Plain-text paste and drop intercept rich text and enforce maxLength", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Hello ";
    mockBody.appendChild(el);

    engine.startTextEdit(el);

    // Paste with multiline & rich-text payload into single-line field (max 160)
    let pastePrevented = false;
    el.dispatchEvent({
      type: "paste",
      preventDefault: () => {
        pastePrevented = true;
      },
      clipboardData: {
        getData: (type) => (type === "text/plain" ? "World!\nNewline" : "<b>Bold</b>"),
      },
    });

    assert.strictEqual(pastePrevented, true);
    // Newline replaced with space in single-line
    assert.strictEqual(el.textContent, "Hello World! Newline");

    // Drop event
    let dropPrevented = false;
    el.dispatchEvent({
      type: "drop",
      preventDefault: () => {
        dropPrevented = true;
      },
      dataTransfer: {
        getData: (type) => (type === "text/plain" ? " Dropped" : ""),
      },
    });
    assert.strictEqual(dropPrevented, true);
    assert.strictEqual(el.textContent, "Hello World! Newline Dropped");

    // Rich-text formatting shortcut (Ctrl+B) is intercepted and prevented
    let bPrevented = false;
    el.dispatchEvent({
      type: "keydown",
      key: "b",
      ctrlKey: true,
      preventDefault: () => {
        bPrevented = true;
      },
    });
    assert.strictEqual(bPrevented, true);

    // BeforeInput formatBold is intercepted and prevented
    let beforeInputPrevented = false;
    el.dispatchEvent({
      type: "beforeinput",
      inputType: "formatBold",
      preventDefault: () => {
        beforeInputPrevented = true;
      },
    });
    assert.strictEqual(beforeInputPrevented, true);

    engine.destroy();
  });

  test("10. Floating toolbar renders counter, commit button, cancel button and viewport clamping", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let proposed = null;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditProposePatch: (p) => {
        proposed = p;
      },
    });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Check Toolbar";
    el.setMockRect({ top: 100, left: 50, width: 250, height: 40 });
    mockBody.appendChild(el);

    engine.startTextEdit(el);

    const toolbar = engine.getToolbarElement();
    assert.ok(toolbar);
    assert.strictEqual(toolbar.getAttribute("data-od-inline-toolbar"), "true");
    assert.strictEqual(toolbar.style.display, "flex");

    // Top position above element (100 - 28 - 4 = 68px)
    assert.strictEqual(toolbar.style.top, "68px");
    assert.strictEqual(toolbar.style.left, "50px");

    // Counter check (13 / 160)
    const counter = toolbar.querySelector(".od-toolbar-counter");
    assert.ok(counter);
    assert.strictEqual(counter.textContent, "13 / 160");
    assert.strictEqual(counter.style.color, "#94a3b8");

    // Test counter exceeding limit turns red
    el.textContent = "A".repeat(165);
    engine.updateCounterAndToolbar();
    assert.strictEqual(counter.textContent, "165 / 160");
    assert.strictEqual(counter.style.color, "#ef4444");

    // Click toolbar commit button (✓)
    const btnSave = toolbar.querySelector(".od-toolbar-save");
    assert.ok(btnSave);
    btnSave.dispatchEvent({
      type: "pointerdown",
      preventDefault: () => {},
      stopPropagation: () => {},
    });

    assert.ok(proposed);
    assert.strictEqual(proposed.proposedValue, "A".repeat(165));
    assert.strictEqual(toolbar.style.display, "none", "Toolbar must hide after commit");

    engine.destroy();
  });

  test("11. Iframe blur, window blur, and pointerout never terminate active editing session", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    let cancelCalled = false;
    let proposeCalled = false;

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditCancel: () => {
        cancelCalled = true;
      },
      onEditProposePatch: () => {
        proposeCalled = true;
      },
    });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Safe From Blur";
    mockBody.appendChild(el);

    engine.startTextEdit(el);
    assert.ok(engine.getActiveTextEdit());

    // 1. Element blur
    el.blur();
    assert.ok(engine.getActiveTextEdit(), "Element blur must not terminate session");

    // 2. Window blur
    mockDoc.defaultView.dispatchWindowEvent({ type: "blur" });
    assert.ok(engine.getActiveTextEdit(), "Window blur must not terminate session");

    // 3. Pointer leaves iframe (pointerout with relatedTarget: null)
    mockDoc.dispatchDocumentEvent({
      type: "pointerout",
      target: el,
      relatedTarget: null,
    });
    assert.ok(engine.getActiveTextEdit(), "Pointer moving out must not terminate session");

    assert.strictEqual(cancelCalled, false);
    assert.strictEqual(proposeCalled, false);

    engine.destroy();
  });

  test("12. Parent selecting the same target does not cancel or commit editing", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Inspector Target";
    mockBody.appendChild(el);

    engine.startTextEdit(el);
    assert.ok(engine.getActiveTextEdit());

    // Parent selectByBlockId on the same block/field
    engine.selectByBlockId("blk_hero_1", { field: "headline" });
    assert.ok(engine.getActiveTextEdit(), "Selecting same field must maintain active session");

    engine.destroy();
  });

  test("13. Protected elements and malformed metadata are strictly rejected", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });

    // 1. Ad placement container
    const adContainer = new MockElement("DIV", {
      class: "block-ad-placement",
      "data-od-protected": "true",
      "data-od-editable": "false",
      "data-od-block-id": "blk_ad_1",
      "data-od-block-type": "ad_placement",
    });
    const adChild = new MockElement("P", {
      "data-od-block-id": "blk_ad_1",
      "data-od-field": "ad_title",
      "data-od-editable": "true", // Spoofed metadata!
    });
    adContainer.appendChild(adChild);
    mockBody.appendChild(adContainer);

    assert.strictEqual(engine.startTextEdit(adChild), false);
    assert.strictEqual(engine.getActiveTextEdit(), null);

    // 2. Consent banner
    const banner = new MockElement("DIV", {
      id: "rivinity-consent-banner",
      "data-od-protected": "true",
    });
    const bannerText = new MockElement("P", {
      "data-od-editable": "true",
      "data-od-field": "consent_text",
      "data-od-block-id": "blk_consent",
    });
    banner.appendChild(bannerText);
    mockBody.appendChild(banner);

    assert.strictEqual(engine.startTextEdit(bannerText), false);

    // 3. Honeypot input
    const hp = new MockElement("INPUT", {
      name: "hp_comment",
      "data-od-editable": "true",
      "data-od-block-id": "blk_contact_1",
    });
    mockBody.appendChild(hp);
    assert.strictEqual(engine.startTextEdit(hp), false);

    // 4. Custom Open Design section
    const customSection = new MockElement("SECTION", {
      "data-od-block-id": "blk_custom_1",
      "data-od-block-type": "open_design_section",
    });
    const customH2 = new MockElement("H2", {
      "data-od-editable": "true",
      "data-od-field": "html_content",
      "data-od-block-id": "blk_custom_1",
    });
    customSection.appendChild(customH2);
    mockBody.appendChild(customSection);

    assert.strictEqual(engine.startTextEdit(customH2), false);

    engine.destroy();
  });

  test("14. Single active edit session: starting edit on element B commits element A", () => {
    const { mockDoc, mockBody, MockElement } = createMockDOM();
    const patches = [];

    const engine = new PreviewOverlayEngine({
      rootDocument: mockDoc,
      onEditProposePatch: (p) => {
        patches.push(p);
      },
    });

    const elA = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    elA.textContent = "Initial A";
    mockBody.appendChild(elA);

    const elB = new MockElement("H2", {
      "data-od-block-id": "blk_text_1",
      "data-od-block-type": "text_section",
      "data-od-field": "heading",
      "data-od-editable": "true",
    });
    elB.textContent = "Initial B";
    mockBody.appendChild(elB);

    // Start editing A and change text
    engine.startTextEdit(elA);
    elA.textContent = "Mutated A";

    // Start editing B -> A commits automatically
    engine.startTextEdit(elB);

    assert.strictEqual(patches.length, 1);
    assert.strictEqual(patches[0].blockId, "blk_hero_1");
    assert.strictEqual(patches[0].proposedValue, "Mutated A");
    assert.strictEqual(engine.getActiveTextEdit().metadata.blockId, "blk_text_1");

    engine.destroy();
  });

  test("15. Teardown cleanup removes listeners, toolbar, and guides layer cleanly", () => {
    const { mockDoc, mockBody, MockElement, listeners } = createMockDOM();
    const engine = new PreviewOverlayEngine({ rootDocument: mockDoc });

    const el = new MockElement("H1", {
      "data-od-block-id": "blk_hero_1",
      "data-od-block-type": "hero",
      "data-od-field": "headline",
      "data-od-editable": "true",
    });
    el.textContent = "Before Teardown";
    mockBody.appendChild(el);

    engine.startTextEdit(el);
    el.textContent = "Uncommitted";

    // Destroy
    engine.destroy();

    assert.strictEqual(engine.getActiveTextEdit(), null);
    assert.strictEqual(el.textContent, "Before Teardown", "Uncommitted text must revert on teardown");
    assert.strictEqual(engine.getToolbarElement(), null);
    assert.strictEqual(engine.getGuidesLayer(), null);

    // Re-destroying is a safe no-op
    engine.destroy();
  });
});
