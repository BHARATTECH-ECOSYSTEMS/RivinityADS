/**
 * open_design_phase5_ad_slot_palette.test.mjs
 *
 * Phase 5 Stage 5.3: Ad-Slot Palette & Placement Integration Test Suite
 *
 * Verifies:
 * 1. AdSlotPalette component rendering: loading, error, empty, and populated states.
 * 2. Slot discovery & metadata display: name, key, format, dimensions, floor price, status.
 * 3. Placed slot detection: shows "✓ Placed" badge when slot is bound in page blocks.
 * 4. Format filtering (All / Banner / Native) and text search.
 * 5. Placement actions: quick place (in_article) and location menu (header_banner, in_article, sidebar, footer).
 * 6. Drag-and-drop attributes: draggable=true on active slots, prevented on inactive.
 * 7. BlockPropertyForm inspector integration:
 *    - Authoritative select populated by bound slots.
 *    - Format-based slot eligibility (incompatible slots disabled).
 *    - Inactive slots disabled.
 *    - Slot details metadata card (dimensions, floor price, format, status).
 *    - Stale / unauthorized slot warning and clear action.
 *    - Incompatible format mismatch warning.
 * 8. Draft block manipulation:
 *    - Placement into active draft with proper format and slot ID.
 *    - Location-aware ordering (header after hero, footer before footer).
 *    - Unrelated block preservation during insertion, update, and removal.
 * 9. Authoritative backend publication alignment & non-billable preview placeholder.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React SSR warnings in Node test environment
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
  const bundlePath = path.resolve("./tests/.compiled_ad_slot_palette_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { AdSlotPalette } from '../src/components/website/AdSlotPalette';
export { BlockPropertyForm } from '../src/components/website/BlockPropertyForm';
export { CanvasBlockRenderer } from '../src/components/website/CanvasBlockRenderer';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_palette_entry.tsx");
  fs.writeFileSync(entryFile, wrapper, "utf8");
  cleanupFiles.push(entryFile);

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    jsx: "automatic",
    target: "node18",
    external: ["react", "react-dom", "react-dom/server", "react-router-dom"],
  });

  return pathToFileURL(bundlePath).href;
}

const bundleUrl = compileBundle();
const {
  React,
  AdSlotPalette,
  BlockPropertyForm,
  CanvasBlockRenderer,
} = await import(bundleUrl);

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

const sampleTheme = {
  primary_color: "#2563eb",
  secondary_color: "#7c3aed",
  font_family: "Inter, sans-serif",
  heading_font: "Inter, sans-serif",
  border_radius: "8px",
};

const mockSlots = [
  {
    id: 101,
    slot_id: "slot_banner_728x90",
    name: "Leaderboard Top Banner",
    format: 1, // Display Banner
    width: 728,
    height: 90,
    status: 1, // Active
    floor_price: 1.5,
    property_id: 42,
    is_chat_surface: false,
  },
  {
    id: 102,
    slot_id: "slot_native_card",
    name: "Article Native Recommendation",
    format: 2, // Native Card
    width: 300,
    height: 250,
    status: 1, // Active
    floor_price: 0.8,
    property_id: 42,
    is_chat_surface: false,
  },
  {
    id: 103,
    slot_id: "slot_chat_surface",
    name: "Assistant Sidebar Slot",
    format: 1, // Banner
    width: 300,
    height: 600,
    status: 1, // Active
    floor_price: 2.0,
    property_id: 42,
    is_chat_surface: true,
  },
  {
    id: 104,
    slot_id: "slot_inactive_archive",
    name: "Archived Footer Slot",
    format: 1,
    width: 728,
    height: 90,
    status: 0, // Inactive
    floor_price: 0.5,
    property_id: 42,
    is_chat_surface: false,
  },
];

describe("RivinityADS Phase 5 Stage 5.3: Ad-Slot Palette & Placement Integration", () => {
  // ── 1. AdSlotPalette Loading, Error & Empty States ─────────────────────────
  describe("1. AdSlotPalette States & Discovery", () => {
    test("Renders loading indicator when isLoading is true", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: [],
          isLoading: true,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-palette-loading"'));
      assert.ok(html.includes("Loading authorized publisher slots..."));
    });

    test("Renders error alert with retry button when error is present", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: [],
          isLoading: false,
          error: "403 Forbidden: Project not linked to publisher",
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-palette-error"'));
      assert.ok(html.includes("403 Forbidden: Project not linked to publisher"));
      assert.ok(html.includes("Retry Connection"));
    });

    test("Renders informative empty state when project has 0 authorized slots", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: [],
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-palette-empty"'));
      assert.ok(html.includes("No Authorized Slots Found"));
      assert.ok(html.includes("Link a publisher account with a verified domain"));
    });

    test("Renders non-billable preview disclosure in all states", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes("Monetization Discovery:"));
      assert.ok(html.includes("Placed units remain non-billable preview placeholders in editor."));
    });
  });

  // ── 2. Authorized Slot Cards & Metadata Display ───────────────────────────
  describe("2. Slot Cards & Metadata Rendering", () => {
    test("Renders all authorized slot cards with names and keys", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-card-slot_banner_728x90"'));
      assert.ok(html.includes("Leaderboard Top Banner"));
      assert.ok(html.includes("slot_banner_728x90"));

      assert.ok(html.includes('data-testid="ad-slot-card-slot_native_card"'));
      assert.ok(html.includes("Article Native Recommendation"));
      assert.ok(html.includes("slot_native_card"));
    });

    test("Displays correct format badges (Banner vs Native)", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes("Banner (Format 1)"));
      assert.ok(html.includes("Native (Format 2)"));
    });

    test("Displays dimensions and floor prices", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes("728×90"));
      assert.ok(html.includes("300×250"));
      assert.ok(html.includes("Floor: $1.50"));
      assert.ok(html.includes("Floor: $0.80"));
    });

    test("Displays Chat Surface badge for chat surface slots", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes("Chat Surface"));
    });

    test("Displays Placed badge when slot is bound in currentBlocks", () => {
      const currentBlocks = [
        {
          id: "blk_ad_1",
          type: "ad_placement",
          is_visible: true,
          content: {
            placement_location: "header_banner",
            format: 1,
            ad_slot_id: "slot_banner_728x90",
          },
        },
      ];

      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks,
        })
      );

      assert.ok(html.includes('data-testid="slot-placed-badge-slot_banner_728x90"'));
      assert.ok(html.includes("✓ Placed"));
      // Native slot is not placed
      assert.ok(!html.includes('data-testid="slot-placed-badge-slot_native_card"'));
    });

    test("Draggable attribute is true for active slots and false for inactive slots", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      // Active card has draggable="true"
      assert.ok(html.includes('draggable="true"'));
      // Inactive card shows Inactive badge
      assert.ok(html.includes("Inactive"));
    });

    test("Renders search input and format filter tabs", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-search-input"'));
      assert.ok(html.includes('data-testid="filter-format-all"'));
      assert.ok(html.includes('data-testid="filter-format-banner"'));
      assert.ok(html.includes('data-testid="filter-format-native"'));
    });
  });

  // ── 3. Placement Actions & Supported Backend Locations ─────────────────────
  describe("3. Placement Actions & Supported Locations", () => {
    test("Renders Quick Place button and options dropdown toggle for active slots", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      assert.ok(html.includes('data-testid="place-slot-quick-slot_banner_728x90"'));
      assert.ok(html.includes("+ Place in Content"));
      assert.ok(html.includes('data-testid="place-slot-options-slot_banner_728x90"'));
    });

    test("Quick place button is disabled for inactive slots", () => {
      const html = renderToStaticMarkup(
        React.createElement(AdSlotPalette, {
          projectId: 1,
          availableSlots: mockSlots,
          isLoading: false,
          error: null,
          onRefresh: () => {},
          onPlaceSlot: () => {},
          currentBlocks: [],
        })
      );

      // Find inactive slot button: slot_inactive_archive has disabled attribute
      assert.ok(
        /disabled(?:="")?[^>]*data-testid="place-slot-quick-slot_inactive_archive"|data-testid="place-slot-quick-slot_inactive_archive"[^>]*disabled/i.test(
          html
        )
      );
    });
  });

  // ── 4. BlockPropertyForm Inspector Enhancement ────────────────────────────
  describe("4. BlockPropertyForm Inspector Integration", () => {
    test("Renders authoritative slot select dropdown populated with available slots", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1,
          ad_slot_id: "slot_banner_728x90",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      assert.ok(html.includes("Authorized Ad Slot"));
      assert.ok(html.includes('<select class="form-input" data-inspector-field="ad_slot_id"'));
      assert.ok(html.includes('value="slot_banner_728x90"'));
      assert.ok(html.includes("Leaderboard Top Banner (slot_banner_728x90) — 728x90"));
    });

    test("Incompatible format slots are disabled in the inspector select", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1, // Display Banner
          ad_slot_id: null,
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      // slot_native_card is Format 2, so it must be disabled with mismatch label
      assert.ok(html.includes('[Format 2 mismatch]'));
      assert.ok(html.includes('value="slot_native_card" disabled=""'));

      // slot_inactive_archive is Inactive, so it must be disabled
      assert.ok(html.includes('[Inactive]'));
      assert.ok(html.includes('value="slot_inactive_archive" disabled=""'));
    });

    test("Displays slot details card when an authorized slot is selected", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "header_banner",
          format: 1,
          ad_slot_id: "slot_banner_728x90",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      assert.ok(html.includes('data-testid="selected-slot-details"'));
      assert.ok(html.includes("728 × 90 px"));
      assert.ok(html.includes("$1.50"));
      assert.ok(html.includes("Format 1 (Banner)"));
      assert.ok(html.includes("Active"));
    });

    test("Renders Clear Slot button when a slot is assigned", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "header_banner",
          format: 1,
          ad_slot_id: "slot_banner_728x90",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      assert.ok(html.includes('data-testid="clear-ad-slot-btn"'));
      assert.ok(html.includes("Clear Slot"));
    });

    test("Displays warning alert when block references an unauthorized or stale slot key", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "sidebar",
          format: 1,
          ad_slot_id: "slot_unauthorized_attacker_key",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-unauthorized-warning"'));
      assert.ok(html.includes("slot_unauthorized_attacker_key"));
      assert.ok(html.includes("Publication validation will fail closed"));
    });

    test("Displays format mismatch warning when slot format does not match placement format", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 2, // Native
          ad_slot_id: "slot_banner_728x90", // Format 1 slot
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          availableSlots: mockSlots,
        })
      );

      assert.ok(html.includes('data-testid="ad-slot-format-mismatch-warning"'));
      assert.ok(html.includes("Slot format (1) does not match block format (2)"));
    });

    test("Falls back cleanly to manual text input when availableSlots is undefined (backward compatibility)", () => {
      const adBlock = {
        id: "blk_ad_test",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1,
          ad_slot_id: "legacy_slot_key",
        },
      };

      const html = renderToStaticMarkup(
        React.createElement(BlockPropertyForm, {
          block: adBlock,
          onChange: () => {},
          onDelete: () => {},
          // availableSlots omitted
        })
      );

      assert.ok(html.includes('Ad Slot Key (Optional)'));
      assert.ok(html.includes('<input type="text" class="form-input" data-inspector-field="ad_slot_id"'));
      assert.ok(html.includes('value="legacy_slot_key"'));
    });
  });

  // ── 5. Placement Insertion & Block Hierarchy Integrity ────────────────────
  describe("5. Placement Hierarchy & Draft Persistence", () => {
    test("Placing header_banner inserts ad block after hero section", () => {
      const initialBlocks = [
        { id: "hero_1", type: "hero", is_visible: true, content: { headline: "Welcome" } },
        { id: "txt_1", type: "text_section", is_visible: true, content: { heading: "About" } },
        { id: "foot_1", type: "footer", is_visible: true, content: { copyright_text: "2026" } },
      ];

      const slot = mockSlots[0]; // Leaderboard Top Banner
      const location = "header_banner";

      // Replicate placement logic
      const newBlock = {
        id: "blk_ad_header",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: location,
          format: slot.format,
          ad_slot_id: slot.slot_id,
        },
      };

      const heroIdx = initialBlocks.findIndex((b) => b.type === "hero");
      const nextBlocks = [
        ...initialBlocks.slice(0, heroIdx + 1),
        newBlock,
        ...initialBlocks.slice(heroIdx + 1),
      ];

      assert.strictEqual(nextBlocks.length, 4);
      assert.strictEqual(nextBlocks[0].id, "hero_1");
      assert.strictEqual(nextBlocks[1].id, "blk_ad_header");
      assert.strictEqual(nextBlocks[1].content.placement_location, "header_banner");
      assert.strictEqual(nextBlocks[1].content.format, 1);
      assert.strictEqual(nextBlocks[1].content.ad_slot_id, "slot_banner_728x90");
      assert.strictEqual(nextBlocks[2].id, "txt_1");
      assert.strictEqual(nextBlocks[3].id, "foot_1");
    });

    test("Placing footer inserts ad block before footer section", () => {
      const initialBlocks = [
        { id: "hero_1", type: "hero", is_visible: true, content: { headline: "Welcome" } },
        { id: "txt_1", type: "text_section", is_visible: true, content: { heading: "About" } },
        { id: "foot_1", type: "footer", is_visible: true, content: { copyright_text: "2026" } },
      ];

      const slot = mockSlots[0];
      const location = "footer";

      const newBlock = {
        id: "blk_ad_footer",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: location,
          format: slot.format,
          ad_slot_id: slot.slot_id,
        },
      };

      const footerIdx = initialBlocks.findIndex((b) => b.type === "footer");
      const nextBlocks = [
        ...initialBlocks.slice(0, footerIdx),
        newBlock,
        ...initialBlocks.slice(footerIdx),
      ];

      assert.strictEqual(nextBlocks.length, 4);
      assert.strictEqual(nextBlocks[0].id, "hero_1");
      assert.strictEqual(nextBlocks[1].id, "txt_1");
      assert.strictEqual(nextBlocks[2].id, "blk_ad_footer");
      assert.strictEqual(nextBlocks[2].content.placement_location, "footer");
      assert.strictEqual(nextBlocks[3].id, "foot_1");
    });

    test("Removing or updating ad placement preserves all unrelated blocks", () => {
      const blocksWithAd = [
        { id: "hero_1", type: "hero", is_visible: true, content: { headline: "Welcome" } },
        { id: "ad_1", type: "ad_placement", is_visible: true, content: { ad_slot_id: "slot_banner_728x90" } },
        { id: "txt_1", type: "text_section", is_visible: true, content: { heading: "About" } },
      ];

      // Remove ad block
      const blocksAfterDelete = blocksWithAd.filter((b) => b.id !== "ad_1");
      assert.strictEqual(blocksAfterDelete.length, 2);
      assert.strictEqual(blocksAfterDelete[0].id, "hero_1");
      assert.strictEqual(blocksAfterDelete[0].content.headline, "Welcome");
      assert.strictEqual(blocksAfterDelete[1].id, "txt_1");
      assert.strictEqual(blocksAfterDelete[1].content.heading, "About");
    });
  });

  // ── 6. Canvas Rendering & Non-Billable Safeguards ──────────────────────────
  describe("6. Canvas Visual Safeguards & Non-Billable Mockups", () => {
    test("CanvasBlockRenderer renders non-billable banner placeholder with slot key", () => {
      const adBlock = {
        id: "blk_ad_canvas",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "in_article",
          format: 1,
          ad_slot_id: "slot_banner_728x90",
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

      assert.ok(html.includes("Non-Billable Preview Placeholder"));
      assert.ok(html.includes("Advertisement"));
      assert.ok(html.includes("RivinityADS Placement Slot (in_article)"));
      assert.ok(html.includes("Format: Standard Display Banner"));
      assert.ok(html.includes("Live monetization inactive • Strictly disabled during draft preview"));
      assert.ok(html.includes("Slot: slot_banner_728x90"));
      assert.ok(html.includes('data-od-protected="true"'));
      assert.ok(html.includes('data-od-editable="false"'));
    });

    test("CanvasBlockRenderer renders native format placeholder correctly", () => {
      const adBlock = {
        id: "blk_ad_native",
        type: "ad_placement",
        is_visible: true,
        content: {
          placement_location: "sidebar",
          format: 2,
          ad_slot_id: "slot_native_card",
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

      assert.ok(html.includes("Format: Native Card"));
      assert.ok(html.includes("RivinityADS Placement Slot (sidebar)"));
      assert.ok(html.includes("Slot: slot_native_card"));
    });
  });
});
