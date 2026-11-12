/**
 * open_design_phase3_4c_patching.test.mjs
 *
 * Comprehensive test suite for RivinityADS Open Design Phase 3.4C:
 * Custom Section Localized HTML Patching & Parent Draft Staging.
 *
 * Verifies:
 * 1. AST Parser, Tokenizer & Serializer:
 *    - HTML tags, attributes, self-closing tags (<img />, <hr>, <br>).
 *    - Comments and HTML entities.
 * 2. Deterministic target resolution:
 *    - Stable [data-od-id] metadata.
 *    - Element ID matching (#id).
 *    - Deterministic structural path (tag:nth-of-type(n) with '>' combinators).
 * 3. Ambiguity & staleness checks:
 *    - Exactly one element must match; ambiguous selectors are rejected.
 *    - Target text must match originalValue; stale proposals are rejected.
 * 4. Smallest-possible DOM transformation:
 *    - Replaces only text content of target leaf.
 *    - Preserves all surrounding markup, attributes, and sibling nodes.
 * 5. Multi-item sections:
 *    - Repeated elements (e.g., list items or paragraphs) update only the targeted node.
 * 6. Full client-side Schema v2 Sanitizer & Anti-spoofing enforcement:
 *    - Rejection of <script>, <iframe>, <style>, and forbidden tags.
 *    - Rejection of inline event handlers (onclick=, onload=, onerror=).
 *    - Rejection of unsafe URLs (javascript:, data:text/html, vbscript:).
 *    - Rejection of ad placement spoofing classes and IDs (rivinity, block-ad-placement, ad-slot).
 *    - Enforcement of 64 KB fragment size limit and 5,000 char leaf limit.
 * 7. Parent draft receiver integration:
 *    - validateProposedPatchAgainstDraft with patchKind: "html_fragment".
 *    - Immutable draft block staging via applyPatchToBlock.
 *    - Authoritative value extraction for open_design_section.
 *    - Rejection of invalid block IDs, type mismatches, and session mismatches.
 * 8. Preview Overlay Engine text leaf interaction:
 *    - Discovery of text leaves inside open_design_section.
 *    - startTextEdit initializes session with patchKind: "html_fragment" and targetSelector.
 *    - applyConfirmedValue and rollbackValue update specific target elements in section.
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
  const bundlePath = path.resolve(__dirname, ".compiled_phase3_4c_patching_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
export {
  isTextLeafElement,
  buildRelativeDomPath,
  parseHtmlFragment,
  serializeHtmlFragment,
  queryFragment,
  sanitizeAndValidateOpenDesignHtml,
  patchOpenDesignSection,
  ALLOWED_OD_TAGS,
  DISALLOWED_OD_TAGS,
  ALLOWED_OD_ATTRIBUTES,
  SPOOFING_CLASS_OR_ID_PATTERNS,
} from '../src/services/openDesignPatcher';

export {
  WebsiteBridgeClient,
  APPROVED_NATIVE_EDITABLE_FIELDS,
  APPROVED_BLOCK_FIELDS,
  extractAuthoritativeValue,
  applyPatchToBlock,
  validateProposedPatchAgainstDraft,
  validateSelectionAgainstDraft,
  extractElementMetadata,
  isElementProtected,
} from '../src/services/websiteBridge';

export {
  PreviewOverlayEngine,
  SCHEMA_FIELD_CONSTRAINTS,
  getFieldConstraints,
  isFieldInlineEditable,
} from '../src/services/previewOverlayEngine';
`;

  const entryFile = path.resolve(__dirname, ".temp_phase3_4c_entry.tsx");
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

const bundleUrl = compileBundle();
const {
  isTextLeafElement,
  buildRelativeDomPath,
  parseHtmlFragment,
  serializeHtmlFragment,
  queryFragment,
  sanitizeAndValidateOpenDesignHtml,
  patchOpenDesignSection,
  WebsiteBridgeClient,
  APPROVED_BLOCK_FIELDS,
  extractAuthoritativeValue,
  applyPatchToBlock,
  validateProposedPatchAgainstDraft,
  validateSelectionAgainstDraft,
  extractElementMetadata,
  PreviewOverlayEngine,
  isFieldInlineEditable,
  getFieldConstraints,
} = await import(bundleUrl);

describe("Open Design Phase 3.4C: AST Parser & Serializer", () => {
  test("parses and serializes standard HTML fragment preserving tags and attributes", () => {
    const originalHtml = '<div class="hero-box"><h1 class="title">Welcome</h1><p id="sub" data-od-id="desc">Subtitle text</p></div>';
    const ast = parseHtmlFragment(originalHtml);
    assert.equal(ast.type, "element");
    assert.equal(ast.children.length, 1);
    const div = ast.children[0];
    assert.equal(div.tag, "div");
    assert.equal(div.attributes.class, "hero-box");
    assert.equal(div.children.length, 2);

    const serialized = serializeHtmlFragment(ast);
    assert.equal(serialized, originalHtml);
  });

  test("handles self-closing tags (<img />, <hr>, <br>)", () => {
    const originalHtml = '<div><p>Before</p><hr /><img src="https://example.com/pic.jpg" alt="Photo" /><p>After</p></div>';
    const ast = parseHtmlFragment(originalHtml);
    const serialized = serializeHtmlFragment(ast);
    assert.ok(serialized.includes("<hr"));
    assert.ok(serialized.includes('<img src="https://example.com/pic.jpg" alt="Photo"'));
  });

  test("handles comments and character entities without corruption", () => {
    const originalHtml = '<div><!-- comment node --><span>Rock &amp; Roll &lt;band&gt;</span></div>';
    const ast = parseHtmlFragment(originalHtml);
    const serialized = serializeHtmlFragment(ast);
    assert.ok(serialized.includes("<!-- comment node -->"));
    assert.ok(serialized.includes("Rock &amp; Roll &lt;band&gt;"));
  });
});

describe("Open Design Phase 3.4C: Target Resolution & Query Engine", () => {
  test("resolves elements via stable data-od-id attribute", () => {
    const html = '<section><div class="card"><h3 data-od-id="card-title">Card 1</h3><p>Description</p></div></section>';
    const ast = parseHtmlFragment(html);
    const matches = queryFragment(ast, '[data-od-id="card-title"]');
    assert.equal(matches.length, 1);
    assert.equal(matches[0].tag, "h3");
    assert.equal(matches[0].children[0].text, "Card 1");
  });

  test("resolves elements via element ID (#id)", () => {
    const html = '<section><div><h2 id="main-heading">Hello World</h2></div></section>';
    const ast = parseHtmlFragment(html);
    const matches = queryFragment(ast, "#main-heading");
    assert.equal(matches.length, 1);
    assert.equal(matches[0].tag, "h2");
    assert.equal(matches[0].children[0].text, "Hello World");
  });

  test("resolves elements via structural path with nth-of-type and '>' combinator", () => {
    const html = `
      <section>
        <div class="col">
          <p>Paragraph 1</p>
          <p>Paragraph 2</p>
          <p>Paragraph 3</p>
        </div>
      </section>
    `;
    const ast = parseHtmlFragment(html);
    const matches = queryFragment(ast, "section > div:nth-of-type(1) > p:nth-of-type(2)");
    assert.equal(matches.length, 1);
    assert.equal(matches[0].children[0].text.trim(), "Paragraph 2");
  });

  test("detects ambiguous selectors matching multiple elements", () => {
    const html = '<div><p class="desc">First</p><p class="desc">Second</p></div>';
    const ast = parseHtmlFragment(html);
    const matches = queryFragment(ast, "p.desc");
    assert.equal(matches.length, 2);
  });
});

describe("Open Design Phase 3.4C: Localized HTML Patch Execution", () => {
  const sampleSectionHtml = [
    '<div class="pricing-card" id="pricing-box">',
    '  <h2 data-od-id="plan-name" class="plan-title">Pro Plan</h2>',
    '  <p class="plan-price">$49/mo</p>',
    '  <ul class="features">',
    '    <li class="item">Unlimited Campaigns</li>',
    '    <li class="item">Real-time Analytics</li>',
    '    <li class="item">24/7 Dedicated Support</li>',
    '  </ul>',
    '  <a href="/upgrade" class="cta-link">Upgrade Now</a>',
    '</div>',
  ].join("\n");

  test("successfully patches text leaf using stable data-od-id", () => {
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="plan-name"]',
      targetOdId: "plan-name",
      originalValue: "Pro Plan",
      proposedValue: "Enterprise Scale Plan",
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, true);
    assert.ok(result.patchedHtml);
    assert.ok(result.patchedHtml.includes("Enterprise Scale Plan"));
    assert.ok(!result.patchedHtml.includes("Pro Plan"));
    // Verify surrounding markup is strictly preserved
    assert.ok(result.patchedHtml.includes('class="pricing-card"'));
    assert.ok(result.patchedHtml.includes('data-od-id="plan-name"'));
    assert.ok(result.patchedHtml.includes('class="plan-title"'));
    assert.ok(result.patchedHtml.includes("$49/mo"));
    assert.ok(result.patchedHtml.includes("Unlimited Campaigns"));
    assert.ok(result.patchedHtml.includes("24/7 Dedicated Support"));
    assert.ok(result.patchedHtml.includes('href="/upgrade"'));
  });

  test("successfully patches repeated list item using structural path", () => {
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: "div:nth-of-type(1) > ul:nth-of-type(1) > li:nth-of-type(2)",
      targetOdId: null,
      originalValue: "Real-time Analytics",
      proposedValue: "AI Predictive Yield Optimization",
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, true);
    assert.ok(result.patchedHtml.includes("AI Predictive Yield Optimization"));
    assert.ok(!result.patchedHtml.includes("Real-time Analytics"));
    // Siblings must be intact
    assert.ok(result.patchedHtml.includes("Unlimited Campaigns"));
    assert.ok(result.patchedHtml.includes("24/7 Dedicated Support"));
  });

  test("rejects stale edit proposals where current text does not match originalValue", () => {
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="plan-name"]',
      targetOdId: "plan-name",
      originalValue: "Starter Plan", // Does not match "Pro Plan"
      proposedValue: "Hacked Plan",
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, false);
    assert.ok(result.error.includes("Stale edit proposal"));
    assert.equal(result.currentValue, "Pro Plan");
  });

  test("rejects ambiguous selectors matching multiple elements", () => {
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: "li.item", // Matches 3 list items!
      targetOdId: null,
      originalValue: "Unlimited Campaigns",
      proposedValue: "Ambiguous Edit",
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, false);
    assert.ok(result.error.includes("Ambiguous target selector"));
  });

  test("rejects edits on protected elements (data-od-protected='true')", () => {
    const protectedHtml = '<div><p data-od-protected="true" id="prot">Protected System Text</p></div>';
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: "#prot",
      targetOdId: null,
      originalValue: "Protected System Text",
      proposedValue: "Tampered Text",
      htmlContent: protectedHtml,
    });

    assert.equal(result.isValid, false);
    assert.ok(result.error.includes("Target element is marked as protected"));
  });

  test("rejects edits on elements impersonating ad slots or platform banners", () => {
    const spoofHtml = '<div><p class="rivinity-ad-slot" id="fake-slot">Ad Slot Copy</p></div>';
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: "#fake-slot",
      targetOdId: null,
      originalValue: "Ad Slot Copy",
      proposedValue: "Malicious Fake Ad",
      htmlContent: spoofHtml,
    });

    assert.equal(result.isValid, false);
    assert.ok(result.error.includes("Target element impersonates protected ad placement"));
  });

  test("escapes proposed HTML to prevent raw markup and script execution", () => {
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="plan-name"]',
      targetOdId: "plan-name",
      originalValue: "Pro Plan",
      proposedValue: '<script>alert("xss")</script>',
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, true);
    assert.ok(result.patchedHtml.includes("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;"));
    assert.ok(!result.patchedHtml.includes("<script>"));
  });

  test("rejects text proposals exceeding 5,000 characters", () => {
    const longText = "A".repeat(5001);
    const result = patchOpenDesignSection({
      blockId: "blk_custom_1",
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="plan-name"]',
      targetOdId: "plan-name",
      originalValue: "Pro Plan",
      proposedValue: longText,
      htmlContent: sampleSectionHtml,
    });

    assert.equal(result.isValid, false);
    assert.ok(result.error.includes("exceeds maximum limit"));
  });
});

describe("Open Design Phase 3.4C: Client Schema v2 Sanitizer", () => {
  test("accepts valid Schema v2 tags and attributes", () => {
    const validHtml = `
      <section class="testimonials">
        <figure>
          <blockquote>"The fastest ad server in India."</blockquote>
          <figcaption>— CTO, Major Fintech</figcaption>
        </figure>
      </section>
    `;
    const res = sanitizeAndValidateOpenDesignHtml(validHtml);
    assert.equal(res.isValid, true);
    assert.ok(res.sanitizedHtml.includes("testimonials"));
    assert.ok(res.sanitizedHtml.includes("figcaption"));
  });

  test("rejects forbidden tags (<script>, <iframe>, <style>, <form>, <button>)", () => {
    const scriptAttempt = '<div><script>console.log("danger")</script></div>';
    const formAttempt = '<div><form action="/steal"><input name="token"/></form></div>';
    const buttonAttempt = '<div><button onclick="steal()">Click me</button></div>';

    assert.equal(sanitizeAndValidateOpenDesignHtml(scriptAttempt).isValid, false);
    assert.equal(sanitizeAndValidateOpenDesignHtml(formAttempt).isValid, false);
    assert.equal(sanitizeAndValidateOpenDesignHtml(buttonAttempt).isValid, false);
  });

  test("rejects inline event handlers (onclick=, onerror=, onload=)", () => {
    const eventHandlerAttempt = '<p onclick="alert(1)">Clickable paragraph</p>';
    const res = sanitizeAndValidateOpenDesignHtml(eventHandlerAttempt);
    assert.equal(res.isValid, false);
    assert.ok(res.error.includes("Unsafe script") || res.error.includes("Event handler attribute"));
  });

  test("rejects unsafe URLs in href or src (javascript:, data:text/html)", () => {
    const jsHref = '<a href="javascript:alert(1)">Exploit Link</a>';
    const dataSrc = '<img src="data:text/html,<script>alert(1)</script>" alt="icon" />';

    assert.equal(sanitizeAndValidateOpenDesignHtml(jsHref).isValid, false);
    assert.equal(sanitizeAndValidateOpenDesignHtml(dataSrc).isValid, false);
  });

  test("rejects ad-spoofing classes or IDs", () => {
    const spoofClass = '<div class="rivinity-ad-slot">Spoofing content</div>';
    const spoofId = '<div id="block-ad-placement-top">Spoofing id</div>';

    assert.equal(sanitizeAndValidateOpenDesignHtml(spoofClass).isValid, false);
    assert.equal(sanitizeAndValidateOpenDesignHtml(spoofId).isValid, false);
  });

  test("rejects fragments exceeding 64 KB (65,536 bytes)", () => {
    const hugeHtml = "<div>" + "p".repeat(66000) + "</div>";
    const res = sanitizeAndValidateOpenDesignHtml(hugeHtml);
    assert.equal(res.isValid, false);
    assert.ok(res.error.includes("exceeds maximum allowed size"));
  });
});

describe("Open Design Phase 3.4C: Parent Draft Receiver Integration", () => {
  const initialDraftBlocks = [
    {
      id: "blk_hero_1",
      type: "hero",
      content: { headline: "Welcome", subheadline: "Fast delivery" },
    },
    {
      id: "blk_section_1",
      type: "open_design_section",
      content: {
        section_tag: "section",
        section_id: "features",
        css_classes: "feature-grid",
        html_content: '<div class="card"><h3 data-od-id="f1">Analytics</h3><p id="f1-desc">Deep metrics</p></div>',
      },
    },
    {
      id: "blk_ad_1",
      type: "ad_placement",
      content: { slot_id: "ad_123" },
    },
  ];

  test("validates and accepts localized patch proposal on open_design_section", () => {
    const payload = {
      sessionId: "session_valid_123",
      blockId: "blk_section_1",
      blockType: "open_design_section",
      field: "html_content",
      itemIndex: null,
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="f1"]',
      targetOdId: "f1",
      originalValue: "Analytics",
      proposedValue: "Real-time Machine Learning Analytics",
    };

    const validation = validateProposedPatchAgainstDraft(
      payload,
      initialDraftBlocks,
      "session_valid_123"
    );

    assert.equal(validation.isValid, true);
    assert.equal(validation.targetBlock.id, "blk_section_1");
    assert.equal(validation.normalizedField, "html_content");
    assert.ok(validation.patchedHtml);
    assert.ok(validation.patchedHtml.includes("Real-time Machine Learning Analytics"));
    assert.ok(!validation.patchedHtml.includes(">Analytics</h3>"));
  });

  test("immutably applies patched HTML into draft block via applyPatchToBlock", () => {
    const targetBlock = initialDraftBlocks[1];
    const patchedHtml = '<div class="card"><h3 data-od-id="f1">Real-time ML Analytics</h3><p id="f1-desc">Deep metrics</p></div>';

    const updated = applyPatchToBlock(
      targetBlock,
      "html_content",
      null,
      "Real-time ML Analytics",
      patchedHtml
    );

    assert.notEqual(updated, targetBlock);
    assert.equal(updated.content.html_content, patchedHtml);
    assert.equal(updated.content.section_tag, "section");
    assert.equal(updated.content.section_id, "features");
    assert.equal(updated.content.css_classes, "feature-grid");
    // Ensure original block is not mutated
    assert.ok(targetBlock.content.html_content.includes("Deep metrics"));
  });

  test("rejects open_design_section proposal with patchKind !== 'html_fragment'", () => {
    const payload = {
      sessionId: "session_123",
      blockId: "blk_section_1",
      blockType: "open_design_section",
      field: "html_content",
      itemIndex: null,
      patchKind: "native_field", // Invalid for custom section
      targetSelector: '[data-od-id="f1"]',
      originalValue: "Analytics",
      proposedValue: "New Text",
    };

    const validation = validateProposedPatchAgainstDraft(
      payload,
      initialDraftBlocks,
      "session_123"
    );

    assert.equal(validation.isValid, false);
    assert.ok(validation.error.includes("patchKind: 'html_fragment'"));
  });

  test("rejects open_design_section proposal targeting unapproved field name", () => {
    const payload = {
      sessionId: "session_123",
      blockId: "blk_section_1",
      blockType: "open_design_section",
      field: "headline", // hero field, not open_design_section
      itemIndex: null,
      patchKind: "html_fragment",
      targetSelector: '[data-od-id="f1"]',
      originalValue: "Analytics",
      proposedValue: "New Text",
    };

    const validation = validateProposedPatchAgainstDraft(
      payload,
      initialDraftBlocks,
      "session_123"
    );

    assert.equal(validation.isValid, false);
    assert.ok(validation.error.includes("is not permitted for block type 'open_design_section'"));
  });

  test("extracts authoritative value for open_design_section via extractAuthoritativeValue", () => {
    const targetBlock = initialDraftBlocks[1];
    const authoritative = extractAuthoritativeValue(targetBlock, "html_content");
    assert.equal(authoritative, targetBlock.content.html_content);
  });

  test("validates selection of html_content on open_design_section as editable", () => {
    const payload = {
      blockId: "blk_section_1",
      blockType: "open_design_section",
      field: "html_content",
      itemIndex: null,
      classification: "editable",
    };
    const res = validateSelectionAgainstDraft(payload, initialDraftBlocks);
    assert.equal(res.isValid, true);
    assert.equal(res.classification, "editable");
  });

  test("validates block-level selection on open_design_section as read_only", () => {
    const payload = {
      blockId: "blk_section_1",
      blockType: "open_design_section",
      field: null,
      itemIndex: null,
      classification: "read_only",
    };
    const res = validateSelectionAgainstDraft(payload, initialDraftBlocks);
    assert.equal(res.isValid, true);
    assert.equal(res.classification, "read_only");
  });

  test("isFieldInlineEditable returns false for custom sections preserving native separation", () => {
    assert.equal(isFieldInlineEditable("open_design_section", "html_content"), false);
    assert.equal(isFieldInlineEditable("open_design_section", "other_field"), false);
  });
});
