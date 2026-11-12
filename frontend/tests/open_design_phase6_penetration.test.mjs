/**
 * open_design_phase6_penetration.test.mjs — RivinityADS Open Design Phase 6 (Stage 6.1)
 *
 * Frontend Adversarial Penetration Test Suite.
 *
 * Verifies:
 * 1. HTML Sanitization & Anti-Spoofing Penetration:
 *    - Entity-encoded spoofing tokens.
 *    - Prohibited ad placement classes, IDs, and data attributes.
 *    - Case and whitespace obfuscation attempts.
 *    - Dangerous URI schemes and executable protocols.
 *    - Event handler attributes.
 *    - Style attribute rejection (preventing clickjacking/ad-stacking overlays).
 * 2. Block-Type Mutation Attacks via Edit Bridge:
 *    - Attempting to mutate ordinary content blocks into ad_placement.
 *    - Attempting to edit protected ad_placement blocks.
 *    - Attempting to inject or alter ad_slot_id via bridge messages.
 *    - Attempting to patch with malformed or closed sessions.
 * 3. Preview Bridge Channel Security:
 *    - Spoofed handshake challenge rejection.
 *    - Stale or mismatched session rejection.
 *    - Oversized message payload rejection (> 100 KB).
 */

import test, { after, describe } from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve(__dirname, ".compiled_phase6_penetration_bundle.mjs");
  const entryFile = path.resolve(__dirname, ".temp_phase6_penetration_entry.ts");
  cleanupFiles.push(bundlePath, entryFile);

  fs.writeFileSync(
    entryFile,
    `
export {
  extractElementMetadata,
  validateSelectionAgainstDraft,
  validateProposedPatchAgainstDraft,
  isElementProtected,
  applyPatchToBlock,
  WebsiteBridgeClient,
  containsDisallowedHtmlOrScript,
} from '../src/services/websiteBridge';
export {
  parseHtmlFragment,
  patchOpenDesignSection,
  sanitizeAndValidateOpenDesignHtml,
  validateSafeUrl,
  containsSpoofingTokens,
  SPOOFING_CLASS_OR_ID_PATTERNS,
} from '../src/services/openDesignPatcher';
`,
    "utf8"
  );

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
    external: ["react", "react-dom", "react-router-dom"],
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

describe("RivinityADS Open Design Phase 6 (Stage 6.1) — Penetration Test Suite", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    sanitizeAndValidateOpenDesignHtml,
    validateSafeUrl,
    containsSpoofingTokens,
    validateProposedPatchAgainstDraft,
    applyPatchToBlock,
    containsDisallowedHtmlOrScript,
    WebsiteBridgeClient,
  } = mod;

  // ════════════════════════════════════════════════════════════════════════════
  // 1. HTML Sanitizer & Anti-Spoofing Penetration
  // ════════════════════════════════════════════════════════════════════════════

  describe("1. Frontend HTML Sanitizer & Anti-Spoofing", () => {
    test("Rejects entity-encoded ad-spoofing class names", () => {
      const payloads = [
        '<div class="&#x72;&#x69;&#x76;&#x69;&#x6e;&#x69;&#x74;&#x79;">Ad Mock</div>',
        '<div class="&#98;lock-ad-placement">Ad Mock</div>',
        '<span id="&#x61;&#x64;&#x2d;&#x73;&#x6c;&#x6f;&#x74;">Fake Slot</span>',
      ];
      for (const p of payloads) {
        const res = sanitizeAndValidateOpenDesignHtml(p);
        assert.strictEqual(res.isValid, false, `Expected '${p}' to be rejected`);
      }
    });

    test("Rejects prohibited ad-placement classes, IDs, and tokens", () => {
      const spoofingPayloads = [
        '<div class="block-ad-placement">Fake Ad Unit</div>',
        '<div class="rivinity">Fake Rivinity Container</div>',
        '<div class="rivinity-banner">Fake Banner</div>',
        '<div class="ad-placement">Fake Ad Placement</div>',
        '<div class="ad-slot">Fake Ad Slot</div>',
        '<div id="rivinity_ad_header">Fake ID</div>',
      ];
      for (const p of spoofingPayloads) {
        const res = sanitizeAndValidateOpenDesignHtml(p);
        assert.strictEqual(res.isValid, false, `Expected '${p}' to be rejected`);
        assert.match(res.error || "", /Prohibited token detected|Impersonation/i);
      }
    });

    test("Rejects case and whitespace obfuscated ad tokens", () => {
      const obfuscated = [
        '<div class="  rivinity  ">Padded</div>',
        '<div class="BLOCK-AD-PLACEMENT">Upper Case</div>',
        '<div class="rIvInItY-slot">Mixed Case</div>',
        '<div id="  ad_placement  ">Padded ID</div>',
      ];
      for (const p of obfuscated) {
        const res = sanitizeAndValidateOpenDesignHtml(p);
        assert.strictEqual(res.isValid, false, `Expected '${p}' to be rejected`);
      }
    });

    test("Rejects dangerous URI schemes in links and images", () => {
      const dangerousUrls = [
        '<a href="javascript:alert(1)">Click</a>',
        '<a href="vbscript:msgbox(1)">Click</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD4=">Click</a>',
        '<img src="javascript:alert(1)" />',
      ];
      for (const p of dangerousUrls) {
        const res = sanitizeAndValidateOpenDesignHtml(p);
        assert.strictEqual(res.isValid, false, `Expected '${p}' to be rejected`);
      }
    });

    test("Rejects event handler attributes", () => {
      const handlers = [
        '<div onclick="malicious()">Click</div>',
        '<img src="https://example.com/pic.png" onerror="steal()" />',
        '<p onmouseover="alert(1)">Hover</p>',
      ];
      for (const h of handlers) {
        const res = sanitizeAndValidateOpenDesignHtml(h);
        assert.strictEqual(res.isValid, false, `Expected '${h}' to be rejected`);
      }
    });

    test("Rejects style attribute to prevent clickjacking and ad-stacking overlays", () => {
      const styled = [
        '<div style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999">Overlay</div>',
        '<p style="color:red">Styled</p>',
      ];
      for (const s of styled) {
        const res = sanitizeAndValidateOpenDesignHtml(s);
        assert.strictEqual(res.isValid, false, `Expected '${s}' to be rejected`);
        assert.match(res.error || "", /Attribute 'style' is not permitted/i);
      }
    });

    test("Rejects prohibited data-slot and ad placement attributes", () => {
      const dataAttrs = [
        '<div data-slot-id="slot_injected">Ad</div>',
        '<div data-rivinity="banner">Ad</div>',
        '<div data-ad-placement="true">Ad</div>',
      ];
      for (const d of dataAttrs) {
        const res = sanitizeAndValidateOpenDesignHtml(d);
        assert.strictEqual(res.isValid, false, `Expected '${d}' to be rejected`);
        assert.match(res.error || "", /Prohibited ad placement attribute/i);
      }
    });

    test("Rejects executable or dangerous tags", () => {
      const tags = [
        '<script>alert(1)</script>',
        '<iframe src="https://evil.com"></iframe>',
        '<style>body { display: none; }</style>',
        '<form action="https://evil.com"><input type="text" /></form>',
        '<meta http-equiv="refresh" content="0;url=https://evil.com" />',
      ];
      for (const t of tags) {
        const res = sanitizeAndValidateOpenDesignHtml(t);
        assert.strictEqual(res.isValid, false, `Expected '${t}' to be rejected`);
      }
    });
  });

  // ════════════════════════════════════════════════════════════════════════════
  // 2. Block-Type Mutation Attacks via Edit Bridge
  // ════════════════════════════════════════════════════════════════════════════

  describe("2. Block-Type Mutation Attacks via Edit Bridge", () => {
    const sampleBlocks = [
      {
        id: "hero_block_1",
        type: "hero",
        order_index: 0,
        content: {
          headline: "Welcome",
          subheadline: "Build fast websites",
        },
      },
      {
        id: "ad_slot_block_2",
        type: "ad_placement",
        order_index: 1,
        content: {
          ad_slot_id: "slot_authorized_1",
          format: "banner",
          width: 728,
          height: 90,
        },
      },
      {
        id: "text_block_3",
        type: "text_section",
        order_index: 2,
        content: {
          heading: "About Us",
          body: "We provide great services.",
        },
      },
    ];

    test("Rejects patch proposal attempting to mutate hero into ad_placement", () => {
      const maliciousPayload = {
        sessionId: "sess_mut_1",
        blockId: "hero_block_1",
        blockType: "ad_placement", // Attacker claims blockType is ad_placement!
        field: "ad_slot_id",
        itemIndex: null,
        proposedValue: "slot_unauthorized_999",
      };

      const result = validateProposedPatchAgainstDraft(maliciousPayload, sampleBlocks, "sess_mut_1");
      assert.strictEqual(result.isValid, false);
      assert.match(result.error || "", /Block type mismatch: expected 'hero', got 'ad_placement'/i);
    });

    test("Rejects patch proposal targeting protected ad_placement block", () => {
      const attackOnAdBlock = {
        sessionId: "sess_mut_2",
        blockId: "ad_slot_block_2",
        blockType: "ad_placement",
        field: "ad_slot_id",
        itemIndex: null,
        proposedValue: "slot_hacked_attacker",
      };

      const result = validateProposedPatchAgainstDraft(attackOnAdBlock, sampleBlocks, "sess_mut_2");
      assert.strictEqual(result.isValid, false);
      assert.match(result.error || "", /Ad placement block is protected and cannot be edited/i);
    });

    test("Rejects patch proposal attempting to change protected ad_placement into text_section", () => {
      const attackMorphAdBlock = {
        sessionId: "sess_mut_3",
        blockId: "ad_slot_block_2",
        blockType: "text_section",
        field: "heading",
        itemIndex: null,
        proposedValue: "Replaced Ad",
      };

      const result = validateProposedPatchAgainstDraft(attackMorphAdBlock, sampleBlocks, "sess_mut_3");
      assert.strictEqual(result.isValid, false);
      assert.match(result.error || "", /Block type mismatch: expected 'ad_placement', got 'text_section'/i);
    });

    test("Rejects patch proposal with missing, stale, or mismatched sessionId", () => {
      const payload = {
        sessionId: "sess_attacker_forged",
        blockId: "hero_block_1",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        proposedValue: "Legit Looking Headline",
      };

      // Expected session is 'sess_legit_active'
      const result = validateProposedPatchAgainstDraft(payload, sampleBlocks, "sess_legit_active");
      assert.strictEqual(result.isValid, false);
      assert.match(result.error || "", /Editing session mismatch or stale session/i);
    });

    test("Rejects HTML or script tag injection into native plain text fields", () => {
      const xssPayload = {
        sessionId: "sess_xss_1",
        blockId: "hero_block_1",
        blockType: "hero",
        field: "headline",
        itemIndex: null,
        proposedValue: "Headline <script>alert('xss')</script>",
      };

      const result = validateProposedPatchAgainstDraft(xssPayload, sampleBlocks, "sess_xss_1");
      assert.strictEqual(result.isValid, false);
      assert.match(result.error || "", /HTML tags, markup, and script patterns are strictly forbidden/i);
    });

    test("applyPatchToBlock strictly preserves block type and identity", () => {
      const block = sampleBlocks[0]; // hero
      const patched = applyPatchToBlock(block, "headline", null, "New Safe Headline");
      assert.strictEqual(patched.id, "hero_block_1");
      assert.strictEqual(patched.type, "hero", "Block type must remain strictly immutable");
      assert.strictEqual(patched.content.headline, "New Safe Headline");
      assert.strictEqual(patched.content.subheadline, "Build fast websites");
    });
  });

  // ════════════════════════════════════════════════════════════════════════════
  // 3. Preview Bridge Channel Security
  // ════════════════════════════════════════════════════════════════════════════

  describe("3. Preview Bridge Channel Security", () => {
    function createMockIframe() {
      const postedMessages = [];
      const mockContentWindow = {
        postMessage: (data, targetOrigin, transfer) => {
          postedMessages.push({ data, targetOrigin, transfer });
        },
      };
      return {
        mockIframe: { contentWindow: mockContentWindow },
        postedMessages,
      };
    }

    test("Handshake fails when iframe acknowledges with forged challenge", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      let errorReceived = null;

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 1000,
        onError: (err) => {
          errorReceived = err;
        },
      });

      const connectPromise = client.connect();
      const port2 = postedMessages[0].transfer[0];

      // Attacker returns forged challenge
      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: "attacker-forged-challenge",
        session_id: postedMessages[0].data.session_id,
      });

      await assert.rejects(connectPromise, /challenge mismatch/i);
      assert.strictEqual(client.getStatus(), "failed");
      assert.ok(errorReceived);
      assert.match(errorReceived.message, /challenge mismatch/i);
    });

    test("Rejects messages with mismatched session ID during active connection", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      const receivedMessages = [];

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 1000,
        onMessage: (msg) => receivedMessages.push(msg),
      });

      const connectPromise = client.connect();
      const port2 = postedMessages[0].transfer[0];

      // Acknowledge handshake legitimately
      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: postedMessages[0].data.challenge,
        session_id: postedMessages[0].data.session_id,
      });
      await connectPromise;
      assert.strictEqual(client.getStatus(), "connected");

      // Attacker sends message with forged / foreign session_id
      port2.postMessage({
        type: "OD_ELEMENT_SELECT",
        session_id: "forged_session_attacker",
        payload: { blockId: "hero_block_1" },
      });

      await new Promise((r) => setTimeout(r, 50));
      assert.strictEqual(receivedMessages.length, 0, "Messages with mismatched session_id must be dropped");

      client.disconnect();
    });

    test("Enforces maximum payload size limit (rejects oversized messages > 100 KB)", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      const receivedMessages = [];

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 1000,
        maxPayloadBytes: 1000, // Short ceiling for test
        onMessage: (msg) => receivedMessages.push(msg),
      });

      const connectPromise = client.connect();
      const port2 = postedMessages[0].transfer[0];

      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: postedMessages[0].data.challenge,
        session_id: postedMessages[0].data.session_id,
      });
      await connectPromise;

      // Send message exceeding 1000 bytes
      port2.postMessage({
        type: "OD_OVERSIZED",
        session_id: client.getSessionId(),
        payload: "A".repeat(2000),
      });

      await new Promise((r) => setTimeout(r, 50));
      assert.strictEqual(receivedMessages.length, 0, "Oversized message must be discarded");

      client.disconnect();
    });
  });
});

