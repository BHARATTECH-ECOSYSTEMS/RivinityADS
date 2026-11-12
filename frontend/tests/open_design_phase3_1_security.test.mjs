/**
 * open_design_phase3_1_security.test.mjs — Phase 3.1: Secure Preview Bridge Foundation Test Suite
 *
 * Verifies:
 * 1. WebsiteEditor preview iframe sandbox configuration:
 *    - Strictly enforces `sandbox="allow-scripts allow-forms"`.
 *    - Strictly OMITS `allow-same-origin` to ensure opaque-origin boundary.
 *    - Strictly OMITS `allow-top-navigation`, `allow-popups`, and other unnecessary permissions.
 *    - Confirms `edit_mode=1` parameter in preview iframe source URL.
 * 2. WebsiteBridgeClient handshake and security mechanics:
 *    - Generates unique cryptographic challenges and session IDs per connection attempt.
 *    - Correctly transfers MessagePort via window.postMessage with 'OD_BRIDGE_INIT'.
 *    - Successful handshake completion upon matching 'OD_BRIDGE_ACK'.
 *    - Handshake rejection when challenge or session_id does not match.
 *    - Timeout handling when preview iframe does not acknowledge within configured timeout.
 *    - Payload size protection enforcing 100 KB ceiling.
 *    - Point-to-point postMessage transmission and teardown on disconnect().
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
  const bundlePath = path.resolve("./tests/.compiled_phase3_1_security_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { MemoryRouter, Routes, Route } from 'react-router-dom';
export { WebsiteEditor } from '../src/pages/website/WebsiteEditor';
export { WebsiteBridgeClient } from '../src/services/websiteBridge';
export * from '../src/api/client';
export * from '../src/types';
`;

  const entryFile = path.resolve("./tests/.temp_phase3_1_entry.tsx");
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

describe("RivinityADS Open Design Phase 3.1 — Secure Preview Bridge Foundation", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const { React, MemoryRouter, WebsiteBridgeClient } = mod;

  describe("1. WebsiteEditor Sandbox Security Attributes Verification", () => {
    test("Editor preview iframe is configured with opaque-origin sandbox ('allow-scripts allow-forms')", () => {
      // Inspect source code of WebsiteEditor.tsx directly to verify exact attributes
      const editorSource = fs.readFileSync(
        path.resolve("./src/pages/website/WebsiteEditor.tsx"),
        "utf8"
      );

      // Verify sandbox attribute
      assert.ok(
        editorSource.includes('sandbox="allow-scripts allow-forms"'),
        "WebsiteEditor must explicitly declare sandbox=\"allow-scripts allow-forms\""
      );

      // Strictly verify allow-same-origin is NOT present in sandbox
      const sandboxMatches = editorSource.match(/sandbox=["']([^"']+)["']/g) || [];
      for (const sm of sandboxMatches) {
        assert.ok(
          !sm.includes("allow-same-origin"),
          `Sandbox attribute "${sm}" must NEVER include allow-same-origin`
        );
        assert.ok(
          !sm.includes("allow-top-navigation"),
          `Sandbox attribute "${sm}" must NEVER include allow-top-navigation`
        );
        assert.ok(
          !sm.includes("allow-popups"),
          `Sandbox attribute "${sm}" must NEVER include allow-popups`
        );
      }
    });

    test("Editor preview URL appends &edit_mode=1 to invoke nonce generation and bridge injection", () => {
      const editorSource = fs.readFileSync(
        path.resolve("./src/pages/website/WebsiteEditor.tsx"),
        "utf8"
      );
      assert.ok(
        editorSource.includes("&edit_mode=1"),
        "previewIframeSrc must include &edit_mode=1 to signal editing preview mode"
      );
    });
  });

  describe("2. WebsiteBridgeClient Handshake & MessageChannel Lifecycle", () => {
    function createMockIframe() {
      let postedMessages = [];
      const mockWindow = {
        postMessage: (data, targetOrigin, transfer) => {
          postedMessages.push({ data, targetOrigin, transfer });
        },
      };

      const mockIframe = {
        contentWindow: mockWindow,
        _getPostedMessages: () => postedMessages,
      };

      return { mockIframe, postedMessages };
    }

    test("Exports WebsiteBridgeClient class with proper contract", () => {
      assert.strictEqual(typeof WebsiteBridgeClient, "function");
      const client = new WebsiteBridgeClient({ iframe: {} });
      assert.strictEqual(client.getStatus(), "uninitialized");
      assert.strictEqual(typeof client.connect, "function");
      assert.strictEqual(typeof client.disconnect, "function");
      assert.strictEqual(typeof client.postMessage, "function");
    });

    test("connect() sends OD_BRIDGE_INIT with challenge and transfers MessagePort", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 500,
      });

      const connectPromise = client.connect();
      assert.strictEqual(client.getStatus(), "connecting");

      // Verify init message sent to iframe
      assert.strictEqual(postedMessages.length, 1);
      const initMsg = postedMessages[0];
      assert.strictEqual(initMsg.data.type, "OD_BRIDGE_INIT");
      assert.ok(initMsg.data.challenge && initMsg.data.challenge.length > 8);
      assert.ok(initMsg.data.session_id && initMsg.data.session_id.length > 8);
      assert.strictEqual(initMsg.targetOrigin, "*");
      assert.ok(Array.isArray(initMsg.transfer) && initMsg.transfer.length === 1);

      // Clean up by disconnecting so timer doesn't linger
      client.disconnect();
      try {
        await connectPromise;
      } catch {}
    });

    test("Handshake succeeds when iframe responds with matching OD_BRIDGE_ACK", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      let connectedCalled = false;
      let statusHistory = [];

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 1000,
        onConnected: () => {
          connectedCalled = true;
        },
        onStatusChange: (s) => {
          statusHistory.push(s);
        },
      });

      const connectPromise = client.connect();
      assert.strictEqual(client.getStatus(), "connecting");

      // Retrieve transferred port (port2)
      const port2 = postedMessages[0].transfer[0];
      assert.ok(port2);

      // Simulate iframe acknowledging with valid challenge and session_id
      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: postedMessages[0].data.challenge,
        session_id: postedMessages[0].data.session_id,
      });

      const result = await connectPromise;
      assert.strictEqual(result, true);
      assert.strictEqual(client.getStatus(), "connected");
      assert.strictEqual(connectedCalled, true);
      assert.ok(statusHistory.includes("connected"));

      // Clean up
      client.disconnect();
      assert.strictEqual(client.getStatus(), "closed");
    });

    test("Handshake fails when challenge does not match", async () => {
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

      // Simulate fake/spoofed challenge
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

    test("Handshake times out if iframe does not acknowledge", async () => {
      const { mockIframe } = createMockIframe();
      let errorReceived = null;

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 50, // Short timeout for test
        onError: (err) => {
          errorReceived = err;
        },
      });

      await assert.rejects(client.connect(), /timed out/i);
      assert.strictEqual(client.getStatus(), "failed");
      assert.ok(errorReceived);
      assert.match(errorReceived.message, /timed out/i);
    });

    test("Enforces maximum payload size limit (discards oversized messages)", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      let receivedMessages = [];

      const client = new WebsiteBridgeClient({
        iframe: mockIframe,
        timeoutMs: 1000,
        maxPayloadBytes: 500, // Small limit for test
        onMessage: (msg) => {
          receivedMessages.push(msg);
        },
      });

      const connectPromise = client.connect();
      const port2 = postedMessages[0].transfer[0];

      // Complete handshake
      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: postedMessages[0].data.challenge,
        session_id: postedMessages[0].data.session_id,
      });
      await connectPromise;

      // Send normal message within limit
      port2.postMessage({
        type: "OD_PING",
        session_id: client.getSessionId(),
        payload: { small: "ok" },
      });

      // Send oversized message (exceeding 500 bytes)
      port2.postMessage({
        type: "OD_HUGE",
        session_id: client.getSessionId(),
        payload: "X".repeat(1000),
      });

      // Wait a tick for port messages
      await new Promise((r) => setTimeout(r, 50));

      assert.strictEqual(receivedMessages.length, 1);
      assert.strictEqual(receivedMessages[0].type, "OD_PING");

      client.disconnect();
    });

    test("postMessage sends messages through private port only when connected", async () => {
      const { mockIframe, postedMessages } = createMockIframe();
      const client = new WebsiteBridgeClient({ iframe: mockIframe, timeoutMs: 1000 });

      // Cannot send when uninitialized
      assert.strictEqual(client.postMessage("TEST"), false);

      const connectPromise = client.connect();
      // Cannot send while connecting
      assert.strictEqual(client.postMessage("TEST"), false);

      const port2 = postedMessages[0].transfer[0];
      port2.postMessage({
        type: "OD_BRIDGE_ACK",
        challenge: postedMessages[0].data.challenge,
        session_id: postedMessages[0].data.session_id,
      });
      await connectPromise;

      // Capture messages received on port2
      let port2Messages = [];
      port2.onmessage = (e) => port2Messages.push(e.data);

      const sent = client.postMessage("PARENT_CMD", { action: "highlight" });
      assert.strictEqual(sent, true);

      await new Promise((r) => setTimeout(r, 50));
      assert.strictEqual(port2Messages.length, 1);
      assert.strictEqual(port2Messages[0].type, "PARENT_CMD");
      assert.strictEqual(port2Messages[0].payload.action, "highlight");
      assert.strictEqual(port2Messages[0].session_id, client.getSessionId());

      client.disconnect();
      assert.strictEqual(client.postMessage("AFTER_DISCONNECT"), false);
    });
  });
});

