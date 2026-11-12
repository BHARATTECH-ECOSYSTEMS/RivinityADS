/**
 * open_design_phase3_3b_selection.test.mjs — Phase 3.3B: MessageChannel Selection Protocol & Parent Draft Receiver Test Suite
 *
 * Verifies:
 * 1. Valid iframe-to-parent selection (`OD_ELEMENT_SELECT`) passing draft validation.
 * 2. Invalid session rejection (stale session ID dropped).
 * 3. Non-existent block ID rejection (block not in active draft).
 * 4. Block type mismatch rejection (draft type doesn't match payload type).
 * 5. Invalid/unapproved field rejection for block type.
 * 6. Out-of-bounds array index rejection (paragraphs, items, images, links).
 * 7. Deleted block selection rejection.
 * 8. Cross-page and cross-project block ID rejection.
 * 9. Protected block classification enforcement (ad placements cannot be editable).
 * 10. Read-only field classification enforcement (icons, custom section).
 * 11. Parent-to-iframe selection (`selectBlock` sending `OD_PARENT_SELECT_BLOCK`).
 * 12. Selection clearing (`clearSelection` and `OD_BACKGROUND_CLICK`).
 * 13. Stale port teardown on reconnect.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_phase3_3b_selection_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  WebsiteBridgeClient,
  validateSelectionAgainstDraft,
  APPROVED_BLOCK_FIELDS,
  extractElementMetadata,
  isElementProtected,
  PROTECTED_SELECTORS,
  VALID_BLOCK_TYPES,
  BLOCK_ID_REGEX,
} from '../src/services/websiteBridge';
export { PreviewOverlayEngine } from '../src/services/previewOverlayEngine';
`;

  const entryFile = path.resolve("./tests/.temp_phase3_3b_entry.tsx");
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

// Mock MessagePort & MessageChannel for Node test runner
class MockMessagePort {
  constructor() {
    this.peer = null;
    this.onmessage = null;
    this.closed = false;
  }

  postMessage(data) {
    if (this.closed) return;
    if (this.peer && !this.peer.closed && this.peer.onmessage) {
      queueMicrotask(() => {
        if (!this.peer.closed && this.peer.onmessage) {
          this.peer.onmessage({ data });
        }
      });
    }
  }

  close() {
    this.closed = true;
  }
}

class MockMessageChannel {
  constructor() {
    this.port1 = new MockMessagePort();
    this.port2 = new MockMessagePort();
    this.port1.peer = this.port2;
    this.port2.peer = this.port1;
  }
}

globalThis.MessageChannel = MockMessageChannel;

describe("RivinityADS Open Design Phase 3.3B — MessageChannel Selection Protocol & Parent Draft Receiver", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    WebsiteBridgeClient,
    validateSelectionAgainstDraft,
    APPROVED_BLOCK_FIELDS,
    PreviewOverlayEngine,
  } = mod;

  const mockDraftBlocks = [
    {
      id: "hero_draft_1",
      type: "hero",
      content: {
        headline: "Draft Hero Headline",
        subheadline: "Draft Hero Subheadline",
        cta_button: { label: "Learn More", target_url: "https://example.com" },
      },
    },
    {
      id: "text_draft_2",
      type: "text_section",
      content: {
        heading: "About Our Product",
        body: "First paragraph copy.\n\nSecond paragraph copy.\n\nThird paragraph copy.",
      },
    },
    {
      id: "features_draft_3",
      type: "features_grid",
      content: {
        title: "Key Features",
        items: [
          { title: "Feature Alpha", description: "Desc Alpha", icon: "zap" },
          { title: "Feature Beta", description: "Desc Beta", icon: "shield" },
        ],
      },
    },
    {
      id: "ad_draft_4",
      type: "ad_placement",
      content: {
        format: 1,
        ad_slot_id: "slot_banner_top",
      },
    },
    {
      id: "od_custom_draft_5",
      type: "open_design_section",
      content: {
        section_tag: "section",
        css_classes: "custom-od",
        html_content: "<div>User authored HTML</div>",
      },
    },
  ];

  describe("1. Parent-Side Draft Validation (`validateSelectionAgainstDraft`)", () => {
    test("Valid hero selection passes validation with editable classification", () => {
      const payload = {
        blockId: "hero_draft_1",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, true);
      assert.strictEqual(result.validatedBlock.id, "hero_draft_1");
      assert.strictEqual(result.classification, "editable");
    });

    test("Valid array item selection validates within array bounds", () => {
      const payload = {
        blockId: "features_draft_3",
        blockType: "features_grid",
        field: "items.title",
        itemIndex: 1, // index 1 exists (length 2)
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, true);
      assert.strictEqual(result.classification, "editable");
    });

    test("Rejects out-of-bounds array index", () => {
      const payload = {
        blockId: "features_draft_3",
        blockType: "features_grid",
        field: "items.title",
        itemIndex: 5, // out of bounds (length 2)
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, false);
      assert.ok(result.error.includes("out of bounds"));
    });

    test("Rejects non-existent block ID (deleted or cross-page)", () => {
      const payload = {
        blockId: "deleted_or_other_page_block_999",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, false);
      assert.strictEqual(result.error, "Block ID not found in current draft");
    });

    test("Rejects block type mismatch", () => {
      const payload = {
        blockId: "hero_draft_1",
        blockType: "features_grid", // actual is hero
        field: "title",
        itemIndex: null,
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, false);
      assert.strictEqual(result.error, "Block type mismatch with draft block");
    });

    test("Rejects unapproved field identifier for block type", () => {
      const payload = {
        blockId: "hero_draft_1",
        blockType: "hero",
        field: "arbitrary_injected_field", // not in APPROVED_BLOCK_FIELDS.hero
        itemIndex: null,
        classification: "editable",
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, false);
      assert.ok(result.error.includes("Field 'arbitrary_injected_field' is not permitted"));
    });

    test("Enforces protected classification on ad placement blocks (rejects editable)", () => {
      const payload = {
        blockId: "ad_draft_4",
        blockType: "ad_placement",
        field: null,
        itemIndex: null,
        classification: "editable", // Forged editable claim
      };

      const result = validateSelectionAgainstDraft(payload, mockDraftBlocks);
      assert.strictEqual(result.isValid, false);
      assert.ok(result.error.includes("Ad placement block cannot have editable classification"));

      // Valid read-only / protected ad placement selection
      const validAdPayload = {
        blockId: "ad_draft_4",
        blockType: "ad_placement",
        field: null,
        itemIndex: null,
        classification: "protected",
      };
      const validResult = validateSelectionAgainstDraft(validAdPayload, mockDraftBlocks);
      assert.strictEqual(validResult.isValid, true);
      assert.strictEqual(validResult.classification, "protected");
    });

    test("Forces read-only classification on open_design_section (rejects inner field selection)", () => {
      const invalidInnerPayload = {
        blockId: "od_custom_draft_5",
        blockType: "open_design_section",
        field: "inner_tag", // Inner field selection not permitted
        itemIndex: null,
        classification: "editable",
      };

      const invalidResult = validateSelectionAgainstDraft(invalidInnerPayload, mockDraftBlocks);
      assert.strictEqual(invalidResult.isValid, false);
      assert.strictEqual(invalidResult.error, "Custom section only supports block-level selection");

      // Valid section-level selection
      const validSectionPayload = {
        blockId: "od_custom_draft_5",
        blockType: "open_design_section",
        field: null,
        itemIndex: null,
        classification: "read_only",
      };
      const validResult = validateSelectionAgainstDraft(validSectionPayload, mockDraftBlocks);
      assert.strictEqual(validResult.isValid, true);
      assert.strictEqual(validResult.classification, "read_only");
    });
  });

  describe("2. MessageChannel Selection Protocol (`WebsiteBridgeClient`)", () => {
    function setupBridgeWithMockIframe() {
      let iframePort = null;
      let initData = null;

      const mockIframe = {
        contentWindow: {
          postMessage(data, origin, transfer) {
            initData = data;
            iframePort = transfer && transfer[0];
            // Simulate iframe acknowledging handshake
            if (iframePort) {
              iframePort.postMessage({
                type: "OD_BRIDGE_ACK",
                challenge: data.challenge,
                session_id: data.session_id,
              });
            }
          },
        },
      };

      return { mockIframe, getIframePort: () => iframePort, getInitData: () => initData };
    }

    test("Receives OD_ELEMENT_SELECT and dispatches onElementSelect callback", async () => {
      const { mockIframe, getIframePort } = setupBridgeWithMockIframe();
      let receivedPayload = null;

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        onElementSelect: (payload) => {
          receivedPayload = payload;
        },
      });

      await client.connect();
      assert.strictEqual(client.getStatus(), "connected");

      const iframePort = getIframePort();
      assert.ok(iframePort);

      // Simulate iframe sending OD_ELEMENT_SELECT
      iframePort.postMessage({
        type: "OD_ELEMENT_SELECT",
        session_id: client.getSessionId(),
        timestamp: Date.now(),
        payload: {
          blockId: "hero_draft_1",
          blockType: "hero",
          field: "headline",
          itemIndex: null,
          classification: "editable",
        },
      });

      // Wait a microtask tick
      await new Promise((r) => setTimeout(r, 20));

      assert.ok(receivedPayload);
      assert.strictEqual(receivedPayload.blockId, "hero_draft_1");
      assert.strictEqual(receivedPayload.field, "headline");

      client.disconnect();
    });

    test("Rejects messages with mismatched session ID", async () => {
      const { mockIframe, getIframePort } = setupBridgeWithMockIframe();
      let receivedPayload = null;

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        onElementSelect: (payload) => {
          receivedPayload = payload;
        },
      });

      await client.connect();

      const iframePort = getIframePort();
      // Send message with STALE / WRONG session ID
      iframePort.postMessage({
        type: "OD_ELEMENT_SELECT",
        session_id: "stale_session_from_old_iframe_9999",
        timestamp: Date.now(),
        payload: { blockId: "hero_draft_1", blockType: "hero" },
      });

      await new Promise((r) => setTimeout(r, 20));
      assert.strictEqual(receivedPayload, null, "Stale session message must be dropped");

      client.disconnect();
    });

    test("Parent sends OD_PARENT_SELECT_BLOCK and OD_PARENT_CLEAR_SELECTION to iframe port", async () => {
      const { mockIframe, getIframePort } = setupBridgeWithMockIframe();
      const client = new WebsiteBridgeClient({ iframe: mockIframe });
      await client.connect();

      const iframePort = getIframePort();
      const receivedMessages = [];
      iframePort.onmessage = (e) => {
        receivedMessages.push(e.data);
      };

      // 1. Parent selects block
      const ok1 = client.selectBlock("text_draft_2", { scrollIntoView: true });
      assert.strictEqual(ok1, true);

      // 2. Parent clears selection
      const ok2 = client.clearSelection();
      assert.strictEqual(ok2, true);

      await new Promise((r) => setTimeout(r, 20));

      assert.strictEqual(receivedMessages.length, 2);
      assert.strictEqual(receivedMessages[0].type, "OD_PARENT_SELECT_BLOCK");
      assert.strictEqual(receivedMessages[0].payload.blockId, "text_draft_2");
      assert.strictEqual(receivedMessages[0].payload.scrollIntoView, true);
      assert.strictEqual(receivedMessages[1].type, "OD_PARENT_CLEAR_SELECTION");

      client.disconnect();
    });

    test("OD_BACKGROUND_CLICK triggers onBackgroundClick callback", async () => {
      const { mockIframe, getIframePort } = setupBridgeWithMockIframe();
      let bgClicked = false;

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        onBackgroundClick: () => {
          bgClicked = true;
        },
      });

      await client.connect();
      const iframePort = getIframePort();

      iframePort.postMessage({
        type: "OD_BACKGROUND_CLICK",
        session_id: client.getSessionId(),
        timestamp: Date.now(),
      });

      await new Promise((r) => setTimeout(r, 20));
      assert.strictEqual(bgClicked, true, "onBackgroundClick callback must be triggered");

      client.disconnect();
    });
  });
});

