/**
 * website_editor_focus_mode.test.mjs
 *
 * Verifies Website Editor Focus Mode:
 * 1. Sidebar remains visible on Websites list and all other Publisher/Admin routes.
 * 2. Entering a specific site's editor (/publisher/websites/:id or /admin/websites/:id)
 *    auto-hides the sidebar and applies full-bleed layout (.content--editor-focus),
 *    while preserving the top console header bar.
 * 3. Editor toolbar renders an accessible sidebar toggle button next to "<- Sites",
 *    opening the slide-over navigation drawer and closing it on toggle click,
 *    backdrop click, and Escape key.
 * 4. Returning via "<- Sites", navigating to another route, or unmounting
 *    WebsiteEditor resets focus mode and restores the sidebar cleanly.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router's benign SSR useLayoutEffect warning in Node
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

// Minimal DOM implementation sufficient for React 18 createRoot + events
function createMiniDom() {
  const windowListeners = new Map();

  class MiniEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.key = init.key || "";
      this.button = init.button ?? 0;
      this.ctrlKey = Boolean(init.ctrlKey);
      this.metaKey = Boolean(init.metaKey);
      this.altKey = Boolean(init.altKey);
      this.shiftKey = Boolean(init.shiftKey);
      this.bubbles = init.bubbles ?? true;
      this.cancelable = init.cancelable ?? true;
      this.defaultPrevented = false;
      this.target = init.target || null;
      this.currentTarget = null;
      this._stopped = false;
    }
    preventDefault() {
      this.defaultPrevented = true;
    }
    stopPropagation() {
      this._stopped = true;
    }
  }

  class MiniNode {
    constructor(nodeType, nodeName) {
      this.nodeType = nodeType;
      this.nodeName = nodeName;
      this.parentNode = null;
      this.childNodes = [];
      this.ownerDocument = null;
      this._listeners = new Map();
    }
    get firstChild() {
      return this.childNodes[0] || null;
    }
    get nextSibling() {
      if (!this.parentNode) return null;
      const idx = this.parentNode.childNodes.indexOf(this);
      return idx >= 0 ? this.parentNode.childNodes[idx + 1] || null : null;
    }
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      child.ownerDocument = this.ownerDocument;
      this.childNodes.push(child);
      return child;
    }
    insertBefore(newNode, refNode) {
      if (!refNode) return this.appendChild(newNode);
      if (newNode.parentNode) newNode.parentNode.removeChild(newNode);
      const idx = this.childNodes.indexOf(refNode);
      newNode.parentNode = this;
      newNode.ownerDocument = this.ownerDocument;
      if (idx === -1) {
        this.childNodes.push(newNode);
      } else {
        this.childNodes.splice(idx, 0, newNode);
      }
      return newNode;
    }
    removeChild(child) {
      const idx = this.childNodes.indexOf(child);
      if (idx >= 0) {
        this.childNodes.splice(idx, 1);
        child.parentNode = null;
      }
      return child;
    }
    addEventListener(type, fn) {
      if (!this._listeners.has(type)) this._listeners.set(type, new Set());
      this._listeners.get(type).add(fn);
    }
    removeEventListener(type, fn) {
      this._listeners.get(type)?.delete(fn);
    }
    dispatchEvent(event) {
      if (!event.target) event.target = this;
      let cur = this;
      while (cur) {
        event.currentTarget = cur;
        const set = cur._listeners.get(event.type);
        if (set) {
          for (const fn of [...set]) fn.call(cur, event);
        }
        if (event._stopped || !event.bubbles) break;
        cur = cur.parentNode;
      }
      return !event.defaultPrevented;
    }
  }

  class MiniComment extends MiniNode {
    constructor(text, doc) {
      super(8, "#comment");
      this.data = String(text || "");
      this.nodeValue = this.data;
      this.ownerDocument = doc;
    }
  }

  class MiniText extends MiniNode {
    constructor(text, doc) {
      super(3, "#text");
      this.data = String(text);
      this.nodeValue = this.data;
      this.ownerDocument = doc;
    }
    set textContent(v) {
      this.data = String(v);
      this.nodeValue = this.data;
    }
    get textContent() {
      return this.data;
    }
  }

  class MiniElement extends MiniNode {
    constructor(tagName, doc) {
      super(1, tagName.toUpperCase());
      this.tagName = tagName.toUpperCase();
      this.ownerDocument = doc;
      this.attributes = new Map();
      this.style = {};
      this.namespaceURI = "http://www.w3.org/1999/xhtml";
    }
    contains(other) {
      let cur = other;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode;
      }
      return false;
    }
    getBoundingClientRect() {
      return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    }
    get className() {
      return this.attributes.get("class") || "";
    }
    set className(val) {
      this.attributes.set("class", String(val));
    }
    get id() {
      return this.attributes.get("id") || "";
    }
    set id(val) {
      this.attributes.set("id", String(val));
    }
    setAttribute(name, val) {
      this.attributes.set(String(name), String(val));
    }
    getAttribute(name) {
      return this.attributes.has(String(name)) ? this.attributes.get(String(name)) : null;
    }
    hasAttribute(name) {
      return this.attributes.has(String(name));
    }
    removeAttribute(name) {
      this.attributes.delete(String(name));
    }
    set textContent(v) {
      this.childNodes = [];
      if (v !== "" && v != null) {
        this.appendChild(new MiniText(v, this.ownerDocument));
      }
    }
    get textContent() {
      return this.childNodes.map((c) => c.textContent).join("");
    }
    set innerHTML(v) {
      this.childNodes = [];
      if (v) this.appendChild(new MiniText(v, this.ownerDocument));
    }
    get innerHTML() {
      return this.textContent;
    }
    focus() {}
    blur() {}
    get value() {
      return this.attributes.get("value") || "";
    }
    set value(v) {
      this.attributes.set("value", String(v ?? ""));
    }
    click() {
      this.dispatchEvent(new MiniEvent("click", { bubbles: true, cancelable: true, target: this }));
    }
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    }
    querySelectorAll(selector) {
      const results = [];
      const walk = (node) => {
        for (const c of node.childNodes) {
          if (c.nodeType === 1) {
            if (matchesSelector(c, selector)) results.push(c);
            walk(c);
          }
        }
      };
      walk(this);
      return results;
    }
  }

  function matchesSelector(el, selector) {
    if (selector.startsWith("#")) return el.id === selector.slice(1);
    if (selector.startsWith(".")) return el.className.split(/\s+/).includes(selector.slice(1));
    const attrMatch = selector.match(/^\[([^=\]]+)="([^"]+)"\]$/);
    if (attrMatch) return el.getAttribute(attrMatch[1]) === attrMatch[2];
    return el.tagName.toLowerCase() === selector.toLowerCase();
  }

  const doc = new MiniNode(9, "#document");
  doc.ownerDocument = doc;
  doc.documentElement = new MiniElement("html", doc);
  doc.head = new MiniElement("head", doc);
  doc.body = new MiniElement("body", doc);
  doc.documentElement.appendChild(doc.head);
  doc.documentElement.appendChild(doc.body);
  doc.createElement = (tag) => new MiniElement(tag, doc);
  doc.createElementNS = (_ns, tag) => new MiniElement(tag, doc);
  doc.createTextNode = (text) => new MiniText(text, doc);
  doc.createComment = (text) => new MiniComment(text, doc);
  doc.createDocumentFragment = () => new MiniNode(11, "#document-fragment");
  doc.activeElement = doc.body;
  doc.getElementById = (id) => doc.body.querySelector(`#${id}`);
  doc.querySelector = (sel) => doc.body.querySelector(sel);
  doc.querySelectorAll = (sel) => doc.body.querySelectorAll(sel);
  doc.addEventListener = (t, fn) => MiniNode.prototype.addEventListener.call(doc, t, fn);
  doc.removeEventListener = (t, fn) => MiniNode.prototype.removeEventListener.call(doc, t, fn);

  const win = {
    document: doc,
    location: { pathname: "/", search: "", hash: "", href: "http://localhost:5173/" },
    history: { replaceState: () => {}, pushState: () => {} },
    addEventListener(type, fn) {
      if (!windowListeners.has(type)) windowListeners.set(type, new Set());
      windowListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      windowListeners.get(type)?.delete(fn);
    },
    dispatchEvent(event) {
      const set = windowListeners.get(event.type);
      if (set) {
        for (const fn of [...set]) fn(event);
      }
      return true;
    },
    scrollTo: () => {},
    HTMLElement: MiniElement,
    HTMLIFrameElement: class HTMLIFrameElement extends MiniElement {},
    HTMLInputElement: class HTMLInputElement extends MiniElement {},
    HTMLTextAreaElement: class HTMLTextAreaElement extends MiniElement {},
    HTMLSelectElement: class HTMLSelectElement extends MiniElement {},
    HTMLButtonElement: class HTMLButtonElement extends MiniElement {},
    Node: MiniNode,
    Element: MiniElement,
    KeyboardEvent: MiniEvent,
    Event: MiniEvent,
    CustomEvent: MiniEvent,
  };
  doc.defaultView = win;

  return { doc, win, MiniEvent };
}

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_editor_focus_mode_bundle.mjs");
  const entryPath = path.resolve("./tests/.temp_editor_focus_mode_entry.tsx");
  cleanupFiles.push(bundlePath, entryPath);

  const wrapper = `
import React from 'react';
export { React };
export { act } from 'react';
export { createRoot } from 'react-dom/client';
export { MemoryRouter, Routes, Route, useNavigate } from 'react-router-dom';
export { default as Layout } from '../src/components/Layout';
export { EditorFocusContext, useEditorFocus, isWebsiteEditorPath } from '../src/components/EditorFocusContext';
export { AuthContext } from '../src/auth/AuthContext';
export { WebsiteList } from '../src/pages/website/WebsiteList';
export { WebsiteEditor } from '../src/pages/website/WebsiteEditor';
`;

  fs.writeFileSync(entryPath, wrapper, "utf8");
  esbuild.buildSync({
    entryPoints: [entryPath],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
    jsx: "automatic",
    external: ["react", "react-dom", "react-dom/client", "react-dom/server", "react-router-dom"],
  });

  return pathToFileURL(bundlePath).href;
}

// Install MiniDom BEFORE importing react-dom/client so React initializes with DOM support
const { doc: globalDoc, win: globalWin, MiniEvent } = createMiniDom();
const prevWindow = globalThis.window;
const prevDocument = globalThis.document;
const prevFetch = globalThis.fetch;
const prevActEnv = globalThis.IS_REACT_ACT_ENVIRONMENT;

globalThis.window = globalWin;
globalThis.document = globalDoc;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

after(() => {
  globalThis.window = prevWindow;
  globalThis.document = prevDocument;
  globalThis.fetch = prevFetch;
  globalThis.IS_REACT_ACT_ENVIRONMENT = prevActEnv;
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

const bundleUrl = compileBundle();
const mod = await import(bundleUrl);
const {
  React,
  act,
  createRoot,
  MemoryRouter,
  Routes,
  Route,
  useNavigate,
  Layout,
  useEditorFocus,
  isWebsiteEditorPath,
  AuthContext,
  WebsiteList,
  WebsiteEditor,
} = mod;

const publisherSession = {
  user: {
    id: 2,
    name: "Charlie Publisher",
    email: "charlie@news.com",
    role: "publisher",
    publisher_id: 2,
    email_verified: true,
  },
  token: "test_pub_token",
};

const adminSession = {
  user: {
    id: 1,
    name: "Alice Admin",
    email: "alice@rivinityads.com",
    role: "admin",
    email_verified: true,
  },
  token: "test_admin_token",
};

function createApiFetchMock() {
  const project = {
    id: 1,
    publisher_id: 2,
    name: "Tech Daily",
    slug: "tech-daily",
    status: 0,
    theme_config: {
      primary_color: "#2563eb",
      secondary_color: "#475569",
      background_color: "#ffffff",
      text_color: "#0f172a",
      font_family: "Inter",
      border_radius: "0.5rem",
    },
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
  const pageSummary = { id: 10, title: "Home", slug: "", is_home: true, page_order: 0 };
  const pageData = {
    ...pageSummary,
    project_id: 1,
    seo_title: "Home",
    seo_description: "",
    blocks: { schema_version: 2, blocks: [] },
  };

  return async (url) => {
    const u = String(url);
    const jsonResp = (data) => ({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => data,
      text: async () => JSON.stringify(data),
    });

    if (u.includes("/api/v1/notifications/unread-count")) return jsonResp({ unread_count: 0 });
    if (u.includes("/api/v1/notifications")) return jsonResp({ items: [], unread_count: 0 });
    if (u.includes("/api/v1/website/templates")) return jsonResp([]);
    if (u.includes("/api/v1/website/projects/1/pages/10")) return jsonResp(pageData);
    if (u.includes("/api/v1/website/projects/1/pages")) return jsonResp([pageSummary]);
    if (u.includes("/api/v1/website/projects/1/slots")) return jsonResp([]);
    if (u.includes("/api/v1/website/projects/1/preview-token")) {
      return jsonResp({ preview_url: "/preview/tok123", token: "tok123", expires_in_seconds: 900 });
    }
    if (u.includes("/api/v1/website/projects/1")) return jsonResp(project);
    if (u.includes("/api/v1/website/projects")) return jsonResp([project]);
    return jsonResp({});
  };
}

describe("Website Editor Focus Mode", () => {
  test("1. Route matcher distinguishes Website List vs active Website Editor routes", () => {
    assert.equal(isWebsiteEditorPath("/publisher/websites"), false);
    assert.equal(isWebsiteEditorPath("/admin/websites"), false);
    assert.equal(isWebsiteEditorPath("/publisher"), false);
    assert.equal(isWebsiteEditorPath("/publisher/slots"), false);
    assert.equal(isWebsiteEditorPath("/publisher/websites/1"), true);
    assert.equal(isWebsiteEditorPath("/publisher/websites/42/"), true);
    assert.equal(isWebsiteEditorPath("/admin/websites/7"), true);
  });

  test("2. Sidebar remains visible on Websites list and other console routes", () => {
    for (const route of ["/publisher/websites", "/publisher", "/publisher/slots", "/admin/websites"]) {
      const sess = route.startsWith("/admin") ? adminSession : publisherSession;
      const html = renderToStaticMarkup(
        React.createElement(
          AuthContext.Provider,
          { value: { session: sess, logout: () => {} } },
          React.createElement(
            MemoryRouter,
            { initialEntries: [route] },
            React.createElement(Layout, null)
          )
        )
      );

      assert.doesNotMatch(html, /is-editor-focus/, `${route} should not enable editor focus mode`);
      assert.doesNotMatch(html, /content--editor-focus/, `${route} should keep normal content layout`);
      assert.match(html, /data-editor-focus="false"/);
      assert.doesNotMatch(html, /id="app-sidebar"[^>]*aria-hidden="true"/, `${route} sidebar must remain visible`);
    }
  });

  test("3. Entering Website Editor route enables focus mode, collapses sidebar, and preserves topbar", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: publisherSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/publisher/websites/1"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /class="shell is-editor-focus"/);
    assert.match(html, /class="content content--editor-focus"/);
    assert.match(html, /id="app-sidebar"[^>]*aria-hidden="true"/);
    assert.match(html, /id="app-sidebar"[^>]*data-collapsed="true"/);
    assert.match(html, /Publisher console/);
    assert.match(html, /Charlie Publisher/);
    assert.match(html, /Log out<\/button>/);
  });

  test("4. CSS defines off-canvas collapsed sidebar, overlay open state, and full-bleed content in focus mode", () => {
    const css = fs.readFileSync(path.resolve("./src/styles.css"), "utf8");
    assert.match(css, /\.content\.content--editor-focus\s*\{[^}]*padding:\s*0;[^}]*max-width:\s*none;/s);
    assert.match(css, /\.shell\.is-editor-focus\s+\.sidebar\s*\{[^}]*position:\s*fixed;[^}]*transform:\s*translateX\(-100%\);/s);
    assert.match(css, /\.shell\.is-editor-focus\s+\.sidebar\.is-open\s*\{[^}]*transform:\s*translateX\(0\);/s);
    assert.match(css, /\.shell\.is-editor-focus\s+\.mobile-nav-backdrop\s*\{[^}]*display:\s*block;[^}]*position:\s*fixed;/s);
  });

  test("5. Interactive flow: list -> editor auto-hides sidebar, toggle opens drawer, backdrop & Escape dismiss, <- Sites & unmount restore sidebar", async () => {
    globalThis.fetch = createApiFetchMock();
    globalDoc.body.childNodes = [];
    const container = globalDoc.createElement("div");
    globalDoc.body.appendChild(container);
    const root = createRoot(container);

    let navigateRef = null;
    function NavHelper() {
      navigateRef = useNavigate();
      return null;
    }

    try {
      // A. Start on Websites list (/publisher/websites)
      await act(async () => {
        root.render(
          React.createElement(
            AuthContext.Provider,
            { value: { session: publisherSession, logout: () => {} } },
            React.createElement(
              MemoryRouter,
              { initialEntries: ["/publisher/websites"] },
              React.createElement(
                Routes,
                null,
                React.createElement(
                  Route,
                  { element: React.createElement(Layout, null) },
                  React.createElement(Route, {
                    path: "/publisher/websites",
                    element: React.createElement(
                      React.Fragment,
                      null,
                      React.createElement(NavHelper),
                      React.createElement(WebsiteList)
                    ),
                  }),
                  React.createElement(Route, {
                    path: "/publisher/websites/:id",
                    element: React.createElement(
                      React.Fragment,
                      null,
                      React.createElement(NavHelper),
                      React.createElement(WebsiteEditor)
                    ),
                  }),
                  React.createElement(Route, {
                    path: "/publisher/slots",
                    element: React.createElement("div", { "data-testid": "slots-page" }, "Ad Units"),
                  })
                )
              )
            )
          )
        );
      });

      const shell = globalDoc.querySelector(".shell");
      const sidebar = globalDoc.getElementById("app-sidebar");
      const mainContent = globalDoc.getElementById("main-content");
      assert.equal(shell.getAttribute("data-editor-focus"), "false");
      assert.equal(sidebar.getAttribute("data-collapsed"), "false");
      assert.equal(sidebar.getAttribute("aria-hidden"), null);
      assert.equal(mainContent.className, "content");

      // B. Enter Website Editor (/publisher/websites/1)
      await act(async () => {
        navigateRef("/publisher/websites/1");
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 25));
      });

      assert.equal(shell.getAttribute("data-editor-focus"), "true");
      assert.ok(shell.className.includes("is-editor-focus"));
      assert.ok(mainContent.className.includes("content--editor-focus"));
      assert.equal(sidebar.getAttribute("data-collapsed"), "true");
      assert.equal(sidebar.getAttribute("aria-hidden"), "true");

      const toggleBtn = globalDoc.querySelector('[data-testid="editor-sidebar-toggle"]');
      assert.ok(toggleBtn, "Editor toolbar must include sidebar toggle button");
      assert.equal(toggleBtn.getAttribute("aria-label"), "Toggle navigation sidebar");
      assert.equal(toggleBtn.getAttribute("aria-expanded"), "false");
      assert.equal(toggleBtn.getAttribute("aria-controls"), "app-sidebar");
      assert.equal(toggleBtn.getAttribute("title"), "Toggle navigation sidebar");

      // C. Click toggle -> opens overlay drawer
      await act(async () => {
        toggleBtn.click();
      });
      assert.ok(sidebar.className.includes("is-open"));
      assert.equal(sidebar.getAttribute("data-collapsed"), "false");
      assert.equal(sidebar.getAttribute("aria-hidden"), null);
      assert.equal(toggleBtn.getAttribute("aria-expanded"), "true");
      assert.ok(globalDoc.querySelector('[data-testid="sidebar-backdrop"]'), "Backdrop must render when drawer opens");

      // D1. Click toggle again -> closes drawer
      await act(async () => {
        toggleBtn.click();
      });
      assert.ok(!sidebar.className.includes("is-open"));
      assert.equal(sidebar.getAttribute("data-collapsed"), "true");
      assert.equal(toggleBtn.getAttribute("aria-expanded"), "false");
      assert.equal(globalDoc.querySelector('[data-testid="sidebar-backdrop"]'), null);

      // D2. Open and close via backdrop click
      await act(async () => {
        toggleBtn.click();
      });
      const backdrop = globalDoc.querySelector('[data-testid="sidebar-backdrop"]');
      assert.ok(backdrop);
      await act(async () => {
        backdrop.click();
      });
      assert.ok(!sidebar.className.includes("is-open"));
      assert.equal(toggleBtn.getAttribute("aria-expanded"), "false");

      // D3. Open and close via Escape key
      await act(async () => {
        toggleBtn.click();
      });
      assert.ok(sidebar.className.includes("is-open"));
      await act(async () => {
        globalWin.dispatchEvent(new MiniEvent("keydown", { key: "Escape" }));
      });
      assert.ok(!sidebar.className.includes("is-open"));
      assert.equal(toggleBtn.getAttribute("aria-expanded"), "false");

      // E. Click "<- Sites" link -> returns to Websites list and restores sidebar
      const links = globalDoc.querySelectorAll("a");
      const backToSitesLink = links.find((a) => a.textContent.includes("← Sites"));
      assert.ok(backToSitesLink, "Toolbar must include <- Sites link");
      await act(async () => {
        backToSitesLink.click();
      });
      assert.equal(shell.getAttribute("data-editor-focus"), "false");
      assert.ok(!shell.className.includes("is-editor-focus"));
      assert.equal(mainContent.className, "content");
      assert.equal(sidebar.getAttribute("data-collapsed"), "false");
      assert.equal(sidebar.getAttribute("aria-hidden"), null);

      // F. Unmounting WebsiteEditor resets focus mode even if mounted on non-editor path
      let showEditor = true;
      function MountProbe() {
        const focus = useEditorFocus();
        return React.createElement(
          "div",
          { "data-testid": "mount-probe", "data-focus": String(focus.isEditorFocusMode) },
          showEditor ? React.createElement(WebsiteEditor) : null
        );
      }

      const container2 = globalDoc.createElement("div");
      globalDoc.body.appendChild(container2);
      const root2 = createRoot(container2);
      try {
        await act(async () => {
          root2.render(
            React.createElement(
              AuthContext.Provider,
              { value: { session: publisherSession, logout: () => {} } },
              React.createElement(
                MemoryRouter,
                { initialEntries: ["/custom-route"] },
                React.createElement(
                  Routes,
                  null,
                  React.createElement(
                    Route,
                    { element: React.createElement(Layout, null) },
                    React.createElement(Route, { path: "/custom-route", element: React.createElement(MountProbe) })
                  )
                )
              )
            )
          );
        });
        assert.equal(container2.querySelector('[data-testid="mount-probe"]').getAttribute("data-focus"), "true");

        showEditor = false;
        await act(async () => {
          root2.render(
            React.createElement(
              AuthContext.Provider,
              { value: { session: publisherSession, logout: () => {} } },
              React.createElement(
                MemoryRouter,
                { initialEntries: ["/custom-route"] },
                React.createElement(
                  Routes,
                  null,
                  React.createElement(
                    Route,
                    { element: React.createElement(Layout, null) },
                    React.createElement(Route, { path: "/custom-route", element: React.createElement(MountProbe) })
                  )
                )
              )
            )
          );
        });
        assert.equal(container2.querySelector('[data-testid="mount-probe"]').getAttribute("data-focus"), "false");
      } finally {
        await act(async () => {
          root2.unmount();
        });
      }
    } finally {
      await act(async () => {
        root.unmount();
      });
    }
  });
});
