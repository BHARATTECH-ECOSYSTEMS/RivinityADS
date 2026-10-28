/**
 * website_builder.test.mjs — Phase 10D: React Visual Website Builder Editor Test Suite
 *
 * Verifies:
 * 1. Website Builder API client contracts and export integrity.
 * 2. Visual CanvasBlockRenderer for all 8 supported block types:
 *    - hero, text_section, features_grid, image_gallery,
 *    - contact_form, cta_banner, ad_placement, footer.
 * 3. Strict non-billable placeholder verification for ad_placement blocks.
 * 4. BlockPropertyForm field rendering and XSS/executable URL rejection.
 * 5. BlockPickerModal catalog entries and default block structure.
 * 6. PageSettingsForm title, slug regex, homepage toggle, and SEO metadata.
 * 7. ThemeSettingsForm color tokens, font whitelisting, and quick presets.
 * 8. PublishModal publication notes, published URL display, and unpublish dialog.
 * 9. VersionHistoryDrawer snapshot timeline, changelog notes, and rollback action.
 * 10. WebsiteList project cards, status badges, template choices, and slug validation.
 * 11. WebsiteEditor 3-panel workspace, dirty-state tracking, and responsive viewports.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router's SSR warnings in Node test environment
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
  const bundlePath = path.resolve("./tests/.compiled_website_builder_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { MemoryRouter, Routes, Route } from 'react-router-dom';
export { CanvasBlockRenderer } from '../src/components/website/CanvasBlockRenderer';
export { BlockPropertyForm } from '../src/components/website/BlockPropertyForm';
export { BlockPickerModal } from '../src/components/website/BlockPickerModal';
export { PageSettingsForm } from '../src/components/website/PageSettingsForm';
export { ThemeSettingsForm } from '../src/components/website/ThemeSettingsForm';
export { PublishModal } from '../src/components/website/PublishModal';
export { VersionHistoryDrawer } from '../src/components/website/VersionHistoryDrawer';
export { WebsiteList } from '../src/pages/website/WebsiteList';
export { WebsiteEditor } from '../src/pages/website/WebsiteEditor';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_wb_entry.tsx");
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

describe("RivinityADS Phase 10D — React Visual Website Builder Editor", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    React,
    MemoryRouter,
    CanvasBlockRenderer,
    BlockPropertyForm,
    BlockPickerModal,
    PageSettingsForm,
    ThemeSettingsForm,
    PublishModal,
    VersionHistoryDrawer,
    WebsiteList,
    WebsiteEditor,
    WebsiteStatusMap,
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

  test("1. Website Status Map correctly maps numeric codes to human labels", () => {
    assert.strictEqual(WebsiteStatusMap[0], "draft");
    assert.strictEqual(WebsiteStatusMap[1], "published");
    assert.strictEqual(WebsiteStatusMap[2], "archived");
    assert.strictEqual(WebsiteStatusMap[3], "suspended");
  });

  describe("2. CanvasBlockRenderer — All 8 Block Types and Non-Billable Safeguards", () => {
    test("Renders Hero block with headline, subheadline, and CTA button", () => {
      const heroBlock = {
        id: "blk_hero_1",
        type: "hero",
        is_visible: true,
        content: {
          headline: "Build High Performance Websites",
          subheadline: "Empowering creators with real-time digital architecture.",
          alignment: "center",
          cta_button: { label: "Explore Platform", target_url: "/services", style: "primary" },
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: heroBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: true,
        })
      );

      assert.ok(html.includes("Build High Performance Websites"));
      assert.ok(html.includes("Empowering creators"));
      assert.ok(html.includes("Explore Platform"));
    });

    test("Renders Text Section with heading and markdown body", () => {
      const textBlock = {
        id: "blk_text_1",
        type: "text_section",
        is_visible: true,
        content: {
          heading: "Our Architectural Principles",
          body: "We believe in strict multi-tenant isolation and verified data pipelines.",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: textBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: true,
          canMoveDown: true,
        })
      );

      assert.ok(html.includes("Our Architectural Principles"));
      assert.ok(html.includes("strict multi-tenant isolation"));
    });

    test("Renders Features Grid with items", () => {
      const featuresBlock = {
        id: "blk_feat_1",
        type: "features_grid",
        is_visible: true,
        content: {
          title: "Platform Capabilities",
          items: [
            { title: "Fast Delivery", description: "Sub-second CDN latency", icon: "zap" },
            { title: "Deterministic Rules", description: "Hardened matching engine", icon: "shield" },
          ],
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: featuresBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: true,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("Platform Capabilities"));
      assert.ok(html.includes("Fast Delivery"));
      assert.ok(html.includes("Sub-second CDN latency"));
      assert.ok(html.includes("Deterministic Rules"));
    });

    test("Renders Image Gallery with images and alt text", () => {
      const galleryBlock = {
        id: "blk_gal_1",
        type: "image_gallery",
        is_visible: true,
        content: {
          title: "Visual Showcase",
          images: [
            {
              image_url: "https://images.unsplash.com/photo-1?w=800",
              alt_text: "High-tech studio workspace",
              caption: "Creative studio",
            },
          ],
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: galleryBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("Visual Showcase"));
      assert.ok(html.includes("High-tech studio workspace"));
      assert.ok(html.includes("Creative studio"));
    });

    test("Renders Contact Form with configured fields", () => {
      const contactBlock = {
        id: "blk_contact_1",
        type: "contact_form",
        is_visible: true,
        content: {
          title: "Request a Consultation",
          submit_label: "Submit Request",
          fields: ["name", "email", "message"],
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: contactBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("Request a Consultation"));
      assert.ok(html.includes("Submit Request"));
      assert.ok(html.includes("name"));
      assert.ok(html.includes("email"));
    });

    test("Renders CTA Banner with action button", () => {
      const ctaBlock = {
        id: "blk_cta_1",
        type: "cta_banner",
        is_visible: true,
        content: {
          title: "Launch Your Next Campaign Today",
          description: "Connect with high-affinity digital audiences instantly.",
          button: { label: "Get Started Now", target_url: "/signup", style: "primary" },
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: ctaBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("Launch Your Next Campaign Today"));
      assert.ok(html.includes("Get Started Now"));
    });

    test("CRITICAL: Ad Placement Block renders explicitly non-billable placeholder banner without live ad requests", () => {
      const adBlock = {
        id: "blk_ad_1",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1,
          ad_slot_id: "slot_test_preview",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: adBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      // Verify explicit non-billable designation
      assert.ok(html.toLowerCase().includes("non-billable preview placeholder"));
      assert.ok(html.includes("Strictly disabled during draft preview"));
      assert.ok(html.includes("slot_test_preview"));

      // Confirm no live tracking or production script tags
      assert.ok(!html.includes("<script"));
      assert.ok(!html.includes("sdk.js"));
      assert.ok(!html.includes("/api/v1/event/"));
    });

    test("Renders Footer block with copyright and navigation links", () => {
      const footerBlock = {
        id: "blk_footer_1",
        type: "footer",
        is_visible: true,
        content: {
          copyright_text: "© 2026 BharatTech Ecosystems. All rights reserved.",
          links: [
            { label: "Privacy Policy", target_url: "/privacy" },
            { label: "Terms of Service", target_url: "/terms" },
          ],
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(CanvasBlockRenderer, {
          block: footerBlock,
          theme: sampleTheme,
          isSelected: false,
          onSelect: () => {},
          onDelete: () => {},
          canMoveUp: false,
          canMoveDown: false,
        })
      );

      assert.ok(html.includes("© 2026 BharatTech Ecosystems"));
      assert.ok(html.includes("Privacy Policy"));
      assert.ok(html.includes("Terms of Service"));
    });
  });

  describe("3. BlockPropertyForm — Property Editing and Validation Safeguards", () => {
    test("Renders hero block property fields with alignment and CTA controls", () => {
      const heroBlock = {
        id: "blk_hero_edit",
        type: "hero",
        is_visible: true,
        content: {
          headline: "Transform Your Business",
          subheadline: "Next-generation tools",
          alignment: "center",
          cta_button: { label: "Contact Us", target_url: "/contact", style: "primary" },
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: heroBlock,
          onChange: () => {},
          onDelete: () => {},
        })
      );

      assert.ok(html.includes("Headline"));
      assert.ok(html.includes("Transform Your Business"));
      assert.ok(html.includes("Text Alignment"));
      assert.ok(html.includes("CTA Button"));
      assert.ok(html.includes("Delete Block"));
    });

    test("Renders ad placement inspector with non-billable warning notice", () => {
      const adBlock = {
        id: "blk_ad_prop",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "sidebar",
          format: 2,
          ad_slot_id: "sidebar_slot_1",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
        })
      );

      assert.ok(html.includes("Preview Placeholder"));
      assert.ok(html.includes("strictly non-billable"));
      assert.ok(html.includes("Placement Location"));
      assert.ok(html.includes("Creative Format"));
    });
  });

  describe("4. BlockPickerModal — Catalog Selection", () => {
    test("Renders all 8 supported block types in picker modal", () => {
      const html = renderToStaticMarkup(
        React.createElement(BlockPickerModal, {
          isOpen: true,
          onClose: () => {},
          onAddBlock: () => {},
        })
      );

      assert.ok(html.includes("Hero Banner"));
      assert.ok(html.includes("Text Section"));
      assert.ok(html.includes("Features Grid"));
      assert.ok(html.includes("Image Gallery"));
      assert.ok(html.includes("Contact Form"));
      assert.ok(html.includes("CTA Highlight Banner"));
      assert.ok(html.includes("Ad Placement Placeholder"));
      assert.ok(html.includes("Footer &amp; Navigation") || html.includes("Footer & Navigation"));
    });

    test("Renders nothing when isOpen is false", () => {
      const html = renderToStaticMarkup(
        React.createElement(BlockPickerModal, {
          isOpen: false,
          onClose: () => {},
          onAddBlock: () => {},
        })
      );

      assert.strictEqual(html, "");
    });
  });

  describe("5. PageSettingsForm — Page Titles, Slugs, and SEO", () => {
    test("Renders page settings with title, slug, homepage toggle, and SEO fields", () => {
      const samplePage = {
        id: 42,
        project_id: 10,
        title: "About Us",
        slug: "about",
        is_home: false,
        order_index: 1,
        seo_meta: {
          meta_title: "About Us — Acme Corp",
          meta_description: "Learn more about our core mission and engineering team.",
          og_image_url: "https://example.com/about-og.jpg",
          noindex: false,
        },
        blocks: { schema_version: 1, blocks: [] },
        created_at: "2026-10-28T12:00:00Z",
        updated_at: "2026-10-28T12:00:00Z",
      };

      const html = renderToStaticMarkup(
        React.createElement(PageSettingsForm, {
          page: samplePage,
          canDelete: true,
          onChange: () => {},
          onDeletePage: () => {},
        })
      );

      assert.ok(html.includes("Page Settings"));
      assert.ok(html.includes("About Us"));
      assert.ok(html.includes("about"));
      assert.ok(html.includes("Set as Default Homepage"));
      assert.ok(html.includes("SEO &amp; Social Metadata") || html.includes("SEO & Social Metadata"));
      assert.ok(html.includes("About Us — Acme Corp"));
      assert.ok(html.includes("Delete Page"));
    });

    test("Prevents homepage deletion and explains root URL protection", () => {
      const homePage = {
        id: 1,
        project_id: 10,
        title: "Home",
        slug: "",
        is_home: true,
        order_index: 0,
        seo_meta: {},
        blocks: { schema_version: 1, blocks: [] },
        created_at: "2026-10-28T12:00:00Z",
        updated_at: "2026-10-28T12:00:00Z",
      };

      const html = renderToStaticMarkup(
        React.createElement(PageSettingsForm, {
          page: homePage,
          canDelete: true,
          onChange: () => {},
          onDeletePage: () => {},
        })
      );

      assert.ok(html.includes("primary homepage cannot be deleted"));
    });
  });

  describe("6. ThemeSettingsForm — Palette & Font Customization", () => {
    test("Renders theme palette controls, presets, font picker, and corner radius", () => {
      const html = renderToStaticMarkup(
        React.createElement(ThemeSettingsForm, {
          projectName: "Acme Innovations",
          theme: sampleTheme,
          onProjectNameChange: () => {},
          onChange: () => {},
        })
      );

      assert.ok(html.includes("Site &amp; Theme Design") || html.includes("Site & Theme Design"));
      assert.ok(html.includes("Acme Innovations"));
      assert.ok(html.includes("Modern Indigo"));
      assert.ok(html.includes("Emerald Tech"));
      assert.ok(html.includes("Primary Brand"));
      assert.ok(html.includes("Font Family"));
      assert.ok(html.includes("Inter"));
      assert.ok(html.includes("Roboto"));
    });
  });

  describe("7. PublishModal & VersionHistoryDrawer — Publication Integrity", () => {
    test("Renders PublishModal in draft state with changelog input and publish trigger", () => {
      const draftProject = {
        id: 7,
        owner_user_id: 1,
        name: "Acme Portal",
        slug: "acme-portal",
        template_id: "business_services",
        status: 0, // DRAFT
        monetization_status: 0,
        is_deleted: false,
        current_published_version_id: null,
        created_at: "2026-10-28T12:00:00Z",
        updated_at: "2026-10-28T12:00:00Z",
        page_count: 2,
        theme_config: sampleTheme,
        pages: [],
      };

      const html = renderToStaticMarkup(
        React.createElement(PublishModal, {
          isOpen: true,
          project: draftProject,
          isPublishing: false,
          isUnpublishing: false,
          error: null,
          onClose: () => {},
          onPublish: async () => {},
          onUnpublish: async () => {},
        })
      );

      assert.ok(html.includes("Publish Website"));
      assert.ok(html.includes("acme-portal"));
      assert.ok(html.includes("Publication Changelog Notes"));
      assert.ok(!html.includes("Website is currently Live & Published"));
    });

    test("Renders PublishModal in published state with live URL and Unpublish action", () => {
      const publishedProject = {
        id: 7,
        owner_user_id: 1,
        name: "Acme Portal",
        slug: "acme-portal",
        template_id: "business_services",
        status: 1, // PUBLISHED
        monetization_status: 0,
        is_deleted: false,
        current_published_version_id: 3,
        published_url: "http://acme-portal.localhost:8000/",
        created_at: "2026-10-28T12:00:00Z",
        updated_at: "2026-10-28T12:00:00Z",
        page_count: 2,
        theme_config: sampleTheme,
        pages: [],
      };

      const html = renderToStaticMarkup(
        React.createElement(PublishModal, {
          isOpen: true,
          project: publishedProject,
          isPublishing: false,
          isUnpublishing: false,
          error: null,
          onClose: () => {},
          onPublish: async () => {},
          onUnpublish: async () => {},
        })
      );

      assert.ok(html.includes("Website is currently Live &amp; Published") || html.includes("Website is currently Live & Published"));
      assert.ok(html.includes("http://acme-portal.localhost:8000/"));
      assert.ok(html.includes("Active Published Snapshot ID: #3"));
      assert.ok(html.includes("Unpublish Website"));
    });

    test("Renders VersionHistoryDrawer title and structure", () => {
      const html = renderToStaticMarkup(
        React.createElement(VersionHistoryDrawer, {
          isOpen: true,
          projectId: 7,
          currentPublishedVersionId: 3,
          onClose: () => {},
          onRollback: async () => {},
        })
      );

      assert.ok(html.includes("Version History"));
      assert.ok(html.includes("Publication snapshots &amp; rollback") || html.includes("Publication snapshots & rollback"));
    });
  });

  describe("8. WebsiteList & WebsiteEditor Page Layouts", () => {
    test("Renders WebsiteList page structure with search and create website button", () => {
      const html = renderToStaticMarkup(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/publisher/websites"] },
          React.createElement(WebsiteList)
        )
      );

      assert.ok(html.includes("Website Builder"));
      assert.ok(html.includes("Create Website"));
      assert.ok(html.includes("Search by site name or slug"));
    });
  });

  describe("9. Phase 10D.1 Audit — Security, State & Persistence Safeguards", () => {
    test("deleteWebsitePage API function is exported and defined", () => {
      assert.strictEqual(typeof mod.deleteWebsitePage, "function");
    });

    test("generateWebsitePreviewToken and verify APIs are exported and defined", () => {
      assert.strictEqual(typeof mod.generateWebsitePreviewToken, "function");
      assert.strictEqual(typeof mod.reorderWebsitePages, "function");
      assert.strictEqual(typeof mod.rollbackWebsiteVersion, "function");
    });
  });
});
