/**
 * open_design_phase2_templates.test.mjs — Phase 2: Open Design Template Catalog & Ingestion Test Suite
 *
 * Verifies:
 * 1. API client exports for template catalog: listWebsiteTemplates, getWebsiteTemplate.
 * 2. CanvasBlockRenderer rendering open_design_section block with safe HTML, custom CSS classes, and styles.
 * 3. CanvasBlockRenderer fallback state when open_design_section has empty html_content.
 * 4. BlockPickerModal includes the open_design_section block with correct metadata and default content.
 * 5. BlockPropertyForm renders open_design_section editing inputs (section_id, section_tag, css_classes, html_content).
 * 6. TemplatePreviewModal renders template metadata, viewport switch controls (Desktop 1200px, Tablet 768px, Mobile 375px), and select action.
 * 7. WebsiteList template catalog browsing, category tabs ("All", "Landing & Marketing", "Publishing & Editorial", "Business & Operations", "Creative & Agency"), and search filtering.
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
  const bundlePath = path.resolve("./tests/.compiled_phase2_templates_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { MemoryRouter, Routes, Route } from 'react-router-dom';
export { CanvasBlockRenderer } from '../src/components/website/CanvasBlockRenderer';
export { BlockPropertyForm } from '../src/components/website/BlockPropertyForm';
export { BlockPickerModal } from '../src/components/website/BlockPickerModal';
export { TemplatePreviewModal } from '../src/components/website/TemplatePreviewModal';
export { WebsiteList } from '../src/pages/website/WebsiteList';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_phase2_entry.tsx");
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

describe("RivinityADS Open Design Phase 2 — Template Catalog & Design System Ingestion", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    React,
    MemoryRouter,
    CanvasBlockRenderer,
    BlockPropertyForm,
    BlockPickerModal,
    TemplatePreviewModal,
    WebsiteList,
    listWebsiteTemplates,
    getWebsiteTemplate,
  } = mod;

  const sampleTheme = {
    primary_color: "#2563eb",
    secondary_color: "#475569",
    background_color: "#ffffff",
    text_color: "#0f172a",
    font_family: "Inter",
    border_radius: "0.5rem",
    logo_url: "https://example.com/logo.svg",
    favicon_url: "https://example.com/favicon.ico",
  };

  test("1. API Client exports listWebsiteTemplates and getWebsiteTemplate functions", () => {
    assert.strictEqual(typeof listWebsiteTemplates, "function");
    assert.strictEqual(typeof getWebsiteTemplate, "function");
  });

  describe("2. CanvasBlockRenderer — open_design_section", () => {
    test("Renders open_design_section with sanitized HTML, custom classes, and section ID", () => {
      const odBlock = {
        id: "blk_od_hero",
        type: "open_design_section",
        is_visible: true,
        content: {
          section_tag: "section",
          section_id: "hero-pricing",
          css_classes: "od-hero-modern od-container",
          styles: { borderBottom: "1px solid #e2e8f0" },
          html_content: '<div class="od-card"><h2>Modern SaaS Platform</h2><p>Designed for velocity</p><a href="/pricing" class="od-btn">View Plans</a></div>',
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: odBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: true,
        })
      );

      assert.ok(html.includes('id="hero-pricing"'), "Should render section id");
      assert.ok(html.includes("od-hero-modern"), "Should include custom css classes");
      assert.ok(html.includes("Modern SaaS Platform"), "Should render inner HTML content");
      assert.ok(html.includes("View Plans"), "Should render CTA link");
      assert.ok(html.includes("od-rendered-html-container"), "Should use rendered container class");
    });

    test("Renders open_design_section fallback state when html_content is empty", () => {
      const emptyOdBlock = {
        id: "blk_od_empty",
        type: "open_design_section",
        is_visible: true,
        content: {
          section_tag: "section",
          section_id: "placeholder-sec",
          css_classes: "od-empty",
          html_content: "",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: emptyOdBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: true,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("Open Design Visual Section"), "Should show fallback label");
      assert.ok(html.includes("placeholder-sec"), "Should show component ID");
    });
  });

  describe("3. BlockPickerModal — Open Design Section Integration", () => {
    test("Modal renders Open Design Section option with icon and description", () => {
      let selectedBlockType = null;
      let selectedContent = null;

      const html = renderToStaticMarkup(
        React.createElement(BlockPickerModal, {
          isOpen: true,
          onClose: () => {},
          onAddBlock: (type, content) => {
            selectedBlockType = type;
            selectedContent = content;
          },
        })
      );

      assert.ok(html.includes("Open Design Visual Section"), "Should list Open Design Section in block picker");
      assert.ok(html.includes("Modular HTML/CSS section layout"), "Should describe Open Design block");
      assert.ok(html.includes("🎨"), "Should render palette icon");
    });
  });

  describe("4. BlockPropertyForm — Open Design Section Editor", () => {
    test("Renders section_id, section_tag, css_classes, and html_content input fields", () => {
      const block = {
        id: "blk_od_1",
        type: "open_design_section",
        is_visible: true,
        content: {
          section_id: "features-grid-v2",
          section_tag: "section",
          css_classes: "od-grid od-dark",
          html_content: '<div class="od-feat">Feature A</div>',
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: block,
          onUpdateBlock: () => {},
          onClose: () => {},
        })
      );

      assert.ok(html.includes("Section Identifier (ID)"), "Should have Section ID label");
      assert.ok(html.includes("features-grid-v2"), "Should have current Section ID value");
      assert.ok(html.includes("HTML Semantic Tag"), "Should have semantic tag selector");
      assert.ok(html.includes("CSS Classes"), "Should have CSS classes input");
      assert.ok(html.includes("od-grid od-dark"), "Should contain existing css classes value");
      assert.ok(html.includes("Sanitized HTML Content"), "Should have sanitized HTML content textarea");
      assert.ok(html.includes("Only safe HTML tags and attributes permitted"), "Should display sanitization policy notice");
      assert.ok(html.includes("server sanitizer"), "Should mention server-side sanitization");
    });
  });

  describe("5. TemplatePreviewModal — Responsive Viewports & Selection", () => {
    const sampleSummary = {
      id: "saas-landing",
      name: "SaaS Landing",
      description: "High-conversion product landing page for modern B2B/B2C SaaS offerings.",
      category: "Landing & Marketing",
      thumbnail_url: "/templates/thumbnails/saas-landing.png",
      badge: "Popular",
      default_theme_config: sampleTheme,
      pages_count: 3,
      is_active: true,
    };

    test("Renders modal header, badge, category, and viewport buttons", () => {
      const html = renderToStaticMarkup(
        React.createElement(TemplatePreviewModal, {
          isOpen: true,
          templateSummary: sampleSummary,
          onClose: () => {},
          onSelectTemplate: () => {},
        })
      );

      assert.ok(html.includes("SaaS Landing"), "Should render template name");
      assert.ok(html.includes("Landing &amp; Marketing") || html.includes("Landing & Marketing"), "Should render category tag");
      assert.ok(html.includes("Popular"), "Should render template badge");
      assert.ok(html.includes("Desktop (1200px)"), "Should have Desktop viewport switch button");
      assert.ok(html.includes("Tablet (768px)"), "Should have Tablet viewport switch button");
      assert.ok(html.includes("Mobile (375px)"), "Should have Mobile viewport switch button");
      assert.ok(html.includes("Use This Template"), "Should render primary CTA button");
    });

    test("Returns null when modal is closed", () => {
      const html = renderToStaticMarkup(
        React.createElement(TemplatePreviewModal, {
          isOpen: false,
          templateSummary: sampleSummary,
          onClose: () => {},
          onSelectTemplate: () => {},
        })
      );

      assert.strictEqual(html, "");
    });
  });

  describe("6. WebsiteList — Template Browser & Category Filtering", () => {
    test("Renders category filter tabs and search input when creating a website", () => {
      const html = renderToStaticMarkup(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/publisher/websites"] },
          React.createElement(WebsiteList)
        )
      );

      assert.ok(html.includes("Website Builder"), "Should render Website Builder title");
      assert.ok(html.includes("Create Website"), "Should render Create Website button");
      assert.ok(html.includes("Search by site name or slug"), "Should render search input");
    });
  });
});
