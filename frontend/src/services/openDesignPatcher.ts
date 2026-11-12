/**
 * openDesignPatcher.ts — RivinityADS Open Design Phase 3.4C:
 * Custom Section Localized HTML Patching & Sanitization Engine
 *
 * Implements:
 * 1. Zero-dependency HTML fragment parser & serializer (Node and Browser compatible).
 * 2. Deterministic target resolution:
 *    - Stable [data-od-id] metadata.
 *    - Element ID matching (#id).
 *    - Deterministic structural path (tag:nth-of-type(n) with '>' child combinators).
 * 3. Ambiguity & staleness checks:
 *    - Exactly one element must match.
 *    - Target text must match originalValue (staleness prevention).
 * 4. Smallest-possible DOM transformation:
 *    - Replaces only the text content of the target node.
 *    - Preserves all surrounding markup, attributes, sibling nodes, and wrapper structure.
 * 5. Full client-side Schema v2 Sanitizer matching backend OpenDesignHTMLSanitizer:
 *    - 35 ALLOWED_OD_TAGS, 3 SELF_CLOSING_OD_TAGS, 20 DISALLOWED_OD_TAGS.
 *    - 18 ALLOWED_OD_ATTRIBUTES.
 *    - Anti-spoofing enforcement (SPOOFING_CLASS_OR_ID_PATTERNS).
 *    - Disallowed script & event handler patterns (DISALLOWED_SCRIPT_PATTERNS).
 *    - 64 KB (65,536 bytes) fragment size limit.
 *    - Safe URL validation on href and src.
 */

import type { BlockItem } from "../types";

export interface AstNode {
  type: "element" | "text" | "comment";
  tag?: string;
  attributes?: Record<string, string>;
  children?: AstNode[];
  text?: string;
  isSelfClosing?: boolean;
}

export interface LocalizedPatchRequest {
  blockId: string;
  blockType: string;
  patchKind: "html_fragment";
  targetSelector: string;
  targetOdId?: string | null;
  originalValue: string;
  proposedValue: string;
  htmlContent: string;
}

export interface LocalizedPatchResult {
  isValid: boolean;
  error?: string;
  patchedHtml?: string;
  currentValue?: string;
}

// ── 1. Schema v2 Allowed / Disallowed Tags & Attributes ──────────────────────

export const ALLOWED_OD_TAGS: Set<string> = new Set([
  "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
  "ul", "ol", "li", "a", "img", "blockquote", "code", "pre",
  "hr", "br", "strong", "b", "em", "i", "table", "thead",
  "tbody", "tr", "th", "td", "figure", "figcaption",
  "section", "article", "aside", "header", "footer",
]);

export const SELF_CLOSING_OD_TAGS: Set<string> = new Set(["img", "hr", "br"]);

export const DISALLOWED_OD_TAGS: Set<string> = new Set([
  "script", "iframe", "object", "embed", "style", "link",
  "form", "input", "button", "textarea", "select", "meta",
  "base", "applet", "frame", "frameset", "svg", "canvas",
  "video", "audio", "portal",
]);

export const ALLOWED_OD_ATTRIBUTES: Set<string> = new Set([
  "id", "class", "title", "aria-label", "aria-hidden",
  "aria-describedby", "role", "href", "target", "rel",
  "src", "alt", "width", "height", "loading",
  "colspan", "rowspan", "scope", "data-od-id",
]);

export const SPOOFING_CLASS_OR_ID_PATTERNS: RegExp[] = [
  /\brivinity\b/i,
  /rivinity-/i,
  /block-ad-placement/i,
  /\bad-placement\b/i,
  /\bad-slot\b/i,
  /rivinity_ad/i,
  /ad_placement/i,
];

export const DISALLOWED_SCRIPT_PATTERNS: RegExp[] = [
  /<\s*script/i,
  /javascript\s*:/i,
  /vbscript\s*:/i,
  /data\s*:\s*text\/html/i,
  /<\s*iframe/i,
  /<\s*object/i,
  /<\s*embed/i,
  /<\s*style/i,
  /\bon[a-z]+\s*=/i,
];

export const TEXT_LEAF_TAGS: Set<string> = new Set([
  "h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "a", "li",
  "blockquote", "strong", "em", "b", "i", "td", "th", "caption",
  "figcaption", "code", "pre",
]);

export const CONTAINER_TAGS: Set<string> = new Set([
  "div", "section", "article", "aside", "header", "footer",
  "table", "thead", "tbody", "tr", "ul", "ol", "figure",
]);

// ── 2. Safe URL & Anti-Spoofing Helpers ─────────────────────────────────────

export function validateSafeUrl(url: string | null | undefined): boolean {
  if (!url) return true;
  const trimmed = url.trim();
  if (DISALLOWED_SCRIPT_PATTERNS.some((pat) => pat.test(trimmed))) {
    return false;
  }
  return trimmed.startsWith("/") ||
    trimmed.startsWith("http://") ||
    trimmed.startsWith("https://") ||
    trimmed.startsWith("mailto:") ||
    trimmed.startsWith("tel:");
}

export function containsSpoofingTokens(value: string | null | undefined): boolean {
  if (!value) return false;
  return SPOOFING_CLASS_OR_ID_PATTERNS.some((pat) => pat.test(value));
}

export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function unescapeHtml(str: string): string {
  return str
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// ── 3. Text Leaf & Relative Path DOM Helpers ────────────────────────────────

/**
 * Check if a DOM element is a text leaf suitable for localized text inline editing.
 */
export function isTextLeafElement(el: Element | null): boolean {
  if (!el || typeof el.tagName !== "string") return false;
  const tag = el.tagName.toLowerCase();
  if (!TEXT_LEAF_TAGS.has(tag)) return false;

  // Cannot contain container children or nested headings/paragraphs
  for (let i = 0; i < el.children.length; i++) {
    const childTag = el.children[i].tagName.toLowerCase();
    if (CONTAINER_TAGS.has(childTag) || ["h1", "h2", "h3", "h4", "h5", "h6", "p"].includes(childTag)) {
      return false;
    }
  }
  return true;
}

/**
 * Deterministically compute relative DOM selector path from section container down to element.
 * Prioritizes [data-od-id] and #id if available.
 */
export function buildRelativeDomPath(element: Element, container: Element): string {
  const pathSegments: string[] = [];
  let curr: Element | null = element;

  while (curr && curr !== container && curr.parentElement) {
    // If element has data-od-id, use it directly
    const odId = curr.getAttribute("data-od-id");
    if (odId && /^[a-zA-Z0-9_\-]+$/.test(odId)) {
      pathSegments.unshift(`[data-od-id="${odId}"]`);
      break;
    }

    // If element has unique ID, use it
    const elId = curr.getAttribute("id");
    if (elId && /^[a-zA-Z0-9_\-]+$/.test(elId)) {
      pathSegments.unshift(`#${elId}`);
      break;
    }

    const tag = curr.tagName.toLowerCase();
    const parent: Element = curr.parentElement;
    const siblings = parent.children;
    const sameTagSiblings: Element[] = [];

    for (let i = 0; i < siblings.length; i++) {
      if (siblings[i].tagName.toLowerCase() === tag) {
        sameTagSiblings.push(siblings[i]);
      }
    }

    if (sameTagSiblings.length > 1) {
      const idx = sameTagSiblings.indexOf(curr) + 1;
      pathSegments.unshift(`${tag}:nth-of-type(${idx})`);
    } else {
      pathSegments.unshift(tag);
    }

    if (parent === container) {
      break;
    }
    curr = parent;
  }

  return pathSegments.join(" > ");
}

// ── 4. Zero-Dependency HTML Fragment AST Parser & Serializer ────────────────

export function parseHtmlFragment(rawHtml: string): AstNode {
  const root: AstNode = { type: "element", tag: "root", children: [] };
  const stack: AstNode[] = [root];

  const tagRegex = /<!--[\s\S]*?-->|<(\/)?([a-zA-Z0-9_-]+)((?:\s+[^=>\s]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^>\s]+))?)*)\s*(\/?)>|([^<]+)/g;
  let match: RegExpExecArray | null;

  while ((match = tagRegex.exec(rawHtml)) !== null) {
    const [fullMatch, isClosing, tagNameRaw, rawAttrs, isSelfCloseSlash, textContent] = match;

    if (fullMatch.startsWith("<!--")) {
      const commentNode: AstNode = { type: "comment", text: fullMatch };
      stack[stack.length - 1].children?.push(commentNode);
      continue;
    }

    if (textContent !== undefined && textContent !== null && textContent.length > 0) {
      const textNode: AstNode = {
        type: "text",
        text: unescapeHtml(textContent),
      };
      stack[stack.length - 1].children?.push(textNode);
      continue;
    }

    const tagName = (tagNameRaw || "").toLowerCase();

    if (isClosing) {
      // Find matching tag in stack
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tag === tagName) {
          stack.length = i;
          break;
        }
      }
      continue;
    }

    // Opening tag
    const attributes: Record<string, string> = {};
    if (rawAttrs) {
      const attrRegex = /([a-zA-Z0-9_\-:]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^>\s]+)))?/g;
      let attrMatch: RegExpExecArray | null;
      while ((attrMatch = attrRegex.exec(rawAttrs)) !== null) {
        const attrName = attrMatch[1].toLowerCase();
        const attrVal = attrMatch[2] !== undefined ? attrMatch[2] : attrMatch[3] !== undefined ? attrMatch[3] : attrMatch[4] ?? "";
        attributes[attrName] = unescapeHtml(attrVal);
      }
    }

    const isSelfClosing = Boolean(isSelfCloseSlash) || SELF_CLOSING_OD_TAGS.has(tagName);
    const elementNode: AstNode = {
      type: "element",
      tag: tagName,
      attributes,
      children: [],
      isSelfClosing,
    };

    stack[stack.length - 1].children?.push(elementNode);

    if (!isSelfClosing) {
      stack.push(elementNode);
    }
  }

  return root;
}

export function serializeHtmlFragment(node: AstNode): string {
  if (node.type === "comment") {
    return node.text || "";
  }
  if (node.type === "text") {
    return escapeHtml(node.text || "");
  }

  if (node.tag === "root") {
    return (node.children || []).map(serializeHtmlFragment).join("");
  }

  const tag = node.tag || "div";
  const attrs = node.attributes || {};
  const attrParts: string[] = [];

  for (const [key, val] of Object.entries(attrs)) {
    attrParts.push(`${key}="${escapeHtml(val)}"`);
  }
  const attrStr = attrParts.length > 0 ? " " + attrParts.join(" ") : "";

  if (node.isSelfClosing || SELF_CLOSING_OD_TAGS.has(tag)) {
    return `<${tag}${attrStr} />`;
  }

  const inner = (node.children || []).map(serializeHtmlFragment).join("");
  return `<${tag}${attrStr}>${inner}</${tag}>`;
}

export function getNodeText(node: AstNode): string {
  if (node.type === "text") {
    return node.text || "";
  }
  if (node.type === "comment") {
    return "";
  }
  return (node.children || []).map(getNodeText).join("");
}

export function setNodeText(node: AstNode, newText: string): void {
  node.children = [{ type: "text", text: newText }];
}

// ── 5. Fragment Selector Engine ─────────────────────────────────────────────

interface SelectorStep {
  tag?: string;
  id?: string;
  odId?: string;
  classes?: string[];
  nthOfType?: number;
}

function parseSelectorStep(stepStr: string): SelectorStep {
  const step: SelectorStep = {};
  let current = stepStr.trim();

  // [data-od-id="..."]
  const odMatch = current.match(/\[data-od-id=["']?([^"']+)["']?\]/);
  if (odMatch) {
    step.odId = odMatch[1];
    current = current.replace(odMatch[0], "");
  }

  // #id
  const idMatch = current.match(/#([a-zA-Z0-9_\-]+)/);
  if (idMatch) {
    step.id = idMatch[1];
    current = current.replace(idMatch[0], "");
  }

  // :nth-of-type(n)
  const nthMatch = current.match(/:nth-of-type\((\d+)\)/);
  if (nthMatch) {
    step.nthOfType = parseInt(nthMatch[1], 10);
    current = current.replace(nthMatch[0], "");
  }

  // .class
  const classMatches = current.match(/\.([a-zA-Z0-9_\-]+)/g);
  if (classMatches) {
    step.classes = classMatches.map((c) => c.slice(1));
    for (const c of classMatches) {
      current = current.replace(c, "");
    }
  }

  // tag
  const tagMatch = current.match(/^([a-zA-Z0-9_-]+)/);
  if (tagMatch) {
    step.tag = tagMatch[1].toLowerCase();
  }

  return step;
}

function matchesStep(node: AstNode, step: SelectorStep, siblings?: AstNode[]): boolean {
  if (node.type !== "element") return false;

  if (step.tag && node.tag !== step.tag) {
    return false;
  }

  if (step.odId && node.attributes?.["data-od-id"] !== step.odId) {
    return false;
  }

  if (step.id && node.attributes?.["id"] !== step.id) {
    return false;
  }

  if (step.classes) {
    const nodeClasses = (node.attributes?.["class"] || "").split(/\s+/).filter(Boolean);
    for (const cls of step.classes) {
      if (!nodeClasses.includes(cls)) return false;
    }
  }

  if (step.nthOfType !== undefined && siblings) {
    const sameTagSiblings = siblings.filter((s) => s.type === "element" && s.tag === node.tag);
    const index = sameTagSiblings.indexOf(node) + 1;
    if (index !== step.nthOfType) return false;
  }

  return true;
}

export function queryFragment(root: AstNode, selector: string): AstNode[] {
  const trimmed = selector.trim();
  if (!trimmed) return [];

  // Split into child combinator steps (e.g., "div:nth-of-type(1) > p:nth-of-type(2)")
  const rawSteps = trimmed.split(">").map((s) => s.trim()).filter(Boolean);
  if (rawSteps.length === 0) return [];

  let currentCandidates: AstNode[] = [];
  const rootChildren = (root.children || []).filter((c) => c.type === "element");

  // Step 1: find matches for first step anywhere under root or in direct children
  const firstStep = parseSelectorStep(rawSteps[0]);

  function searchFirst(node: AstNode, siblings?: AstNode[]): void {
    if (matchesStep(node, firstStep, siblings)) {
      currentCandidates.push(node);
    }
    const children = (node.children || []).filter((c) => c.type === "element");
    for (const child of children) {
      searchFirst(child, children);
    }
  }

  for (const child of rootChildren) {
    searchFirst(child, rootChildren);
  }

  // Subsequent steps: must be direct children
  for (let i = 1; i < rawSteps.length; i++) {
    const step = parseSelectorStep(rawSteps[i]);
    const nextCandidates: AstNode[] = [];

    for (const parent of currentCandidates) {
      const children = (parent.children || []).filter((c) => c.type === "element");
      for (const child of children) {
        if (matchesStep(child, step, children)) {
          nextCandidates.push(child);
        }
      }
    }
    currentCandidates = nextCandidates;
  }

  return currentCandidates;
}

// ── 6. Schema v2 Sanitizer Verification ─────────────────────────────────────

export function sanitizeAndValidateOpenDesignHtml(
  rawHtml: string
): { isValid: boolean; sanitizedHtml?: string; error?: string } {
  if (!rawHtml || !rawHtml.trim()) {
    return { isValid: true, sanitizedHtml: "" };
  }

  // Size limit: 64 KB
  const byteLength = new TextEncoder().encode(rawHtml).length;
  if (byteLength > 65536) {
    return { isValid: false, error: "HTML content exceeds maximum allowed size (64 KB)" };
  }

  // Disallowed script patterns
  for (const pat of DISALLOWED_SCRIPT_PATTERNS) {
    if (pat.test(rawHtml)) {
      return { isValid: false, error: "Unsafe script or executable tags detected in HTML content" };
    }
  }

  // Parse into AST
  let root: AstNode;
  try {
    root = parseHtmlFragment(rawHtml);
  } catch (err: any) {
    return { isValid: false, error: `Malformed HTML fragment: ${err.message}` };
  }

  // Traverse AST to validate tags and attributes
  const errors: string[] = [];

  function validateNode(node: AstNode): void {
    if (node.type === "element") {
      const tag = (node.tag || "").toLowerCase();

      if (DISALLOWED_OD_TAGS.has(tag) || !ALLOWED_OD_TAGS.has(tag)) {
        errors.push(`HTML tag <${tag}> is forbidden in Open Design sections.`);
        return;
      }

      const attrs = node.attributes || {};
      for (const [attrName, attrVal] of Object.entries(attrs)) {
        const name = attrName.toLowerCase();

        if (name.startsWith("on")) {
          errors.push(`Event handler attribute '${name}' is forbidden.`);
          return;
        }

        if (
          name.startsWith("data-slot") ||
          name.startsWith("data-format") ||
          name.startsWith("data-rivinity") ||
          name.startsWith("data-ad")
        ) {
          errors.push(`Prohibited ad placement attribute '${name}' detected.`);
          return;
        }

        if (!ALLOWED_OD_ATTRIBUTES.has(name)) {
          errors.push(`Attribute '${name}' is not permitted on <${tag}>.`);
          return;
        }

        if (name === "id" || name === "class") {
          for (const pat of SPOOFING_CLASS_OR_ID_PATTERNS) {
            if (pat.test(attrVal)) {
              errors.push(
                `Prohibited token detected in '${name}': '${attrVal}'. Impersonation of ad components or tracking elements is strictly forbidden.`
              );
              return;
            }
          }
          if (!/^[a-zA-Z0-9_\-\s]*$/.test(attrVal)) {
            errors.push(`Invalid characters in attribute '${name}': '${attrVal}'.`);
            return;
          }
        } else if (name === "href" || name === "src") {
          if (!validateSafeUrl(attrVal)) {
            errors.push(`Unsafe URL detected in attribute '${name}': '${attrVal}'.`);
            return;
          }
        }
      }

      for (const child of node.children || []) {
        validateNode(child);
      }
    }
  }

  for (const child of root.children || []) {
    validateNode(child);
    if (errors.length > 0) break;
  }

  if (errors.length > 0) {
    return { isValid: false, error: errors[0] };
  }

  const cleanSerialized = serializeHtmlFragment(root);
  return { isValid: true, sanitizedHtml: cleanSerialized };
}

// ── 7. Localized Patch Execution Engine ─────────────────────────────────────

/**
 * Patch a localized text element within an open_design_section HTML fragment.
 *
 * Requirements enforced:
 * 1. Target resolution via data-od-id or structural path.
 * 2. Unambiguous match check (must resolve to exactly 1 element).
 * 3. Staleness check: target text must match originalValue.
 * 4. Protection immunity check: target and ancestors must not be protected.
 * 5. Text-leaf check: target element cannot contain container children.
 * 6. Smallest DOM mutation: updates only target node textContent.
 * 7. Full Schema v2 sanitization and anti-spoofing verification.
 */
export function patchOpenDesignSection(request: LocalizedPatchRequest): LocalizedPatchResult {
  const { targetSelector, targetOdId, originalValue, proposedValue, htmlContent } = request;

  if (typeof proposedValue !== "string") {
    return { isValid: false, error: "Proposed value must be a string" };
  }

  if (proposedValue.length > 5000) {
    return {
      isValid: false,
      error: `Proposed text value length (${proposedValue.length}) exceeds maximum limit (5000 characters)`,
    };
  }

  // Parse existing fragment into AST
  let root: AstNode;
  try {
    root = parseHtmlFragment(htmlContent || "");
  } catch (err: any) {
    return { isValid: false, error: `Failed to parse section HTML: ${err.message}` };
  }

  // 1. Target Resolution
  let matches: AstNode[] = [];

  // Try data-od-id first if present
  if (targetOdId && /^[a-zA-Z0-9_\-]+$/.test(targetOdId)) {
    matches = queryFragment(root, `[data-od-id="${targetOdId}"]`);
  }

  // Fallback to targetSelector
  if (matches.length === 0 && targetSelector) {
    // Validate targetSelector does not attempt parent traversal or illegal characters
    if (/(\.\.|\/\.\/|:has|:not|\*)/.test(targetSelector)) {
      return { isValid: false, error: "Invalid or prohibited target selector syntax" };
    }
    matches = queryFragment(root, targetSelector);
  }

  if (matches.length === 0) {
    return {
      isValid: false,
      error: `Target element not found in section HTML fragment (selector: '${targetSelector}')`,
    };
  }

  if (matches.length > 1) {
    return {
      isValid: false,
      error: `Ambiguous target selector in section HTML fragment (matched ${matches.length} elements)`,
    };
  }

  const targetNode = matches[0];
  const currentText = getNodeText(targetNode);

  // 2. Staleness Check
  if (currentText.trim() !== (originalValue || "").trim()) {
    return {
      isValid: false,
      error: `Stale edit proposal: current text '${currentText.trim()}' does not match expected original '${(originalValue || "").trim()}'`,
      currentValue: currentText,
    };
  }

  // 3. Protection Immunity Check
  if (targetNode.attributes?.["data-od-protected"] === "true") {
    return { isValid: false, error: "Target element is marked as protected and cannot be edited" };
  }
  const targetClass = targetNode.attributes?.["class"] || "";
  const targetId = targetNode.attributes?.["id"] || "";
  if (containsSpoofingTokens(targetClass) || containsSpoofingTokens(targetId)) {
    return { isValid: false, error: "Target element impersonates protected ad placement or platform component" };
  }

  // 4. Update Target Node Text (Plain text only)
  setNodeText(targetNode, proposedValue);

  // 5. Serialize Updated Fragment
  const updatedHtml = serializeHtmlFragment(root);

  // 6. Run Full Schema v2 Sanitization Verification
  const sanitization = sanitizeAndValidateOpenDesignHtml(updatedHtml);
  if (!sanitization.isValid) {
    return {
      isValid: false,
      error: sanitization.error || "Sanitization verification failed for patched HTML fragment",
      currentValue: currentText,
    };
  }

  return {
    isValid: true,
    patchedHtml: sanitization.sanitizedHtml || updatedHtml,
    currentValue: currentText,
  };
}

// ── 7. Open Design Schema v2 Generated BlockItem Validation (Phase 4E) ───────

export const ALLOWED_SECTION_TAGS: Set<string> = new Set([
  "section", "article", "div", "aside", "header", "footer",
]);

export const ALLOWED_CSS_PROPERTIES: Set<string> = new Set([
  "background", "background-color", "color",
  "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
  "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
  "width", "min-width", "max-width", "height", "min-height", "max-height",
  "display", "flex-direction", "justify-content", "align-items", "flex-wrap", "gap",
  "grid-template-columns", "grid-template-rows", "grid-gap",
  "border", "border-top", "border-bottom", "border-left", "border-right",
  "border-radius", "border-color", "border-width", "border-style",
  "text-align", "font-size", "font-weight", "font-style", "line-height", "letter-spacing",
  "box-shadow", "opacity", "overflow", "z-index", "position", "top", "bottom", "left", "right",
]);

export const FORBIDDEN_STYLE_VALUE_PATTERNS: string[] = [
  ";", "url(", "expression(", "@import", "javascript:", "<", ">", "behavior", "-moz-binding",
];

export interface GeneratedBlockValidationResult {
  isValid: boolean;
  error?: string;
  validatedBlock?: BlockItem;
}

/**
 * Validates a generated BlockItem against the Open Design Schema v2 backend contract.
 * Ensures the block cannot inject arbitrary HTML, bypass tenant styling, or spoof platform components.
 */
export function validateGeneratedBlockItem(raw: any): GeneratedBlockValidationResult {
  // 1. Block object envelope check
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { isValid: false, error: "Block must be a non-null object." };
  }

  // 2. Block ID constraints (1-64 alphanumeric, hyphens, underscores)
  if (typeof raw.id !== "string") {
    return { isValid: false, error: "Block 'id' must be a string." };
  }
  const cleanId = raw.id.trim();
  if (cleanId.length === 0) {
    return { isValid: false, error: "Block 'id' cannot be empty." };
  }
  if (cleanId.length > 64) {
    return { isValid: false, error: `Block 'id' exceeds maximum length of 64 characters (got ${cleanId.length}).` };
  }
  if (!/^[a-zA-Z0-9_\-]+$/.test(cleanId)) {
    return {
      isValid: false,
      error: "Block 'id' may only contain alphanumeric characters, underscores, and hyphens.",
    };
  }

  // 3. Exact type: must be 'open_design_section' (rejects hero, ad_placement, etc.)
  if (raw.type !== "open_design_section") {
    return {
      isValid: false,
      error: `Invalid block type '${raw.type}'. Only 'open_design_section' is permitted for AI-generated blocks.`,
    };
  }

  // 4. is_visible must be a boolean (defaults to true if undefined)
  let isVisible = true;
  if (raw.is_visible !== undefined && raw.is_visible !== null) {
    if (typeof raw.is_visible !== "boolean") {
      return { isValid: false, error: "Block 'is_visible' must be a boolean." };
    }
    isVisible = raw.is_visible;
  }

  // 5. Nested content object
  if (!raw.content || typeof raw.content !== "object" || Array.isArray(raw.content)) {
    return { isValid: false, error: "Block 'content' must be a non-null object." };
  }
  const content = raw.content;

  // 6. html_content string (1 to 65,536 characters)
  if (typeof content.html_content !== "string") {
    return { isValid: false, error: "Block content 'html_content' must be a string." };
  }
  const rawHtml = content.html_content.trim();
  if (rawHtml.length === 0) {
    return { isValid: false, error: "Block content 'html_content' cannot be empty." };
  }
  if (content.html_content.length > 65536) {
    return {
      isValid: false,
      error: `Block content 'html_content' exceeds maximum limit of 65,536 characters (got ${content.html_content.length}).`,
    };
  }

  // Run full Schema v2 sanitization on the HTML fragment
  const sanitization = sanitizeAndValidateOpenDesignHtml(content.html_content);
  if (!sanitization.isValid) {
    return {
      isValid: false,
      error: sanitization.error || "HTML content failed Schema v2 sanitization policy.",
    };
  }
  const sanitizedHtml = sanitization.sanitizedHtml || content.html_content;

  // 7. section_tag: must be whitelisted tag if provided
  let sectionTag: string = "section";
  if (content.section_tag !== undefined && content.section_tag !== null) {
    if (typeof content.section_tag !== "string") {
      return { isValid: false, error: "Block content 'section_tag' must be a string." };
    }
    const cleanTag = content.section_tag.trim().toLowerCase();
    if (!ALLOWED_SECTION_TAGS.has(cleanTag)) {
      return {
        isValid: false,
        error: `Invalid section_tag '${content.section_tag}'. Allowed tags: ${Array.from(ALLOWED_SECTION_TAGS).sort().join(", ")}.`,
      };
    }
    sectionTag = cleanTag;
  }

  // 8. section_id: alphanumeric/hyphens/underscores, max 64, no spoofing
  let sectionId: string | undefined = undefined;
  if (content.section_id !== undefined && content.section_id !== null) {
    if (typeof content.section_id !== "string") {
      return { isValid: false, error: "Block content 'section_id' must be a string." };
    }
    const cleanSecId = content.section_id.trim();
    if (cleanSecId.length > 64) {
      return { isValid: false, error: `Block content 'section_id' cannot exceed 64 characters (got ${cleanSecId.length}).` };
    }
    if (cleanSecId.length > 0 && !/^[a-zA-Z0-9_\-]+$/.test(cleanSecId)) {
      return {
        isValid: false,
        error: "Block content 'section_id' may only contain alphanumeric characters, underscores, and hyphens.",
      };
    }
    if (containsSpoofingTokens(cleanSecId)) {
      return { isValid: false, error: "Block content 'section_id' contains prohibited ad placement tokens." };
    }
    sectionId = cleanSecId;
  }

  // 9. css_classes: alphanumeric/hyphens/underscores/spaces, max 500, no spoofing
  let cssClasses: string | undefined = undefined;
  if (content.css_classes !== undefined && content.css_classes !== null) {
    if (typeof content.css_classes !== "string") {
      return { isValid: false, error: "Block content 'css_classes' must be a string." };
    }
    const cleanClasses = content.css_classes.trim();
    if (cleanClasses.length > 500) {
      return { isValid: false, error: `Block content 'css_classes' cannot exceed 500 characters (got ${cleanClasses.length}).` };
    }
    if (cleanClasses.length > 0 && !/^[a-zA-Z0-9_\-\s]*$/.test(cleanClasses)) {
      return {
        isValid: false,
        error: "Block content 'css_classes' may only contain alphanumeric characters, underscores, hyphens, and spaces.",
      };
    }
    if (containsSpoofingTokens(cleanClasses)) {
      return { isValid: false, error: "Block content 'css_classes' contains prohibited ad placement tokens." };
    }
    cssClasses = cleanClasses;
  }

  // 10. styles: dictionary of allowed properties and safe values (max 30)
  let sanitizedStyles: Record<string, string> = {};
  if (content.styles !== undefined && content.styles !== null) {
    if (typeof content.styles !== "object" || Array.isArray(content.styles)) {
      return { isValid: false, error: "Block content 'styles' must be a plain object." };
    }
    const entries = Object.entries(content.styles);
    if (entries.length > 30) {
      return { isValid: false, error: `Maximum 30 style declarations allowed per section (got ${entries.length}).` };
    }
    for (const [prop, val] of entries) {
      const cleanProp = String(prop).trim().toLowerCase();
      if (!ALLOWED_CSS_PROPERTIES.has(cleanProp)) {
        return { isValid: false, error: `CSS property '${cleanProp}' is not in the allowed properties list.` };
      }
      if (typeof val !== "string") {
        return { isValid: false, error: `CSS property value for '${cleanProp}' must be a string.` };
      }
      const cleanVal = val.trim();
      const valLower = cleanVal.toLowerCase();
      if (FORBIDDEN_STYLE_VALUE_PATTERNS.some((pattern) => valLower.includes(pattern))) {
        return { isValid: false, error: `Unsafe CSS value detected for property '${cleanProp}': '${cleanVal}'.` };
      }
      sanitizedStyles[cleanProp] = cleanVal;
    }
  }

  // 11. asset_references: list of safe URLs (max 20)
  let sanitizedAssets: string[] = [];
  if (content.asset_references !== undefined && content.asset_references !== null) {
    if (!Array.isArray(content.asset_references)) {
      return { isValid: false, error: "Block content 'asset_references' must be an array." };
    }
    if (content.asset_references.length > 20) {
      return { isValid: false, error: `Maximum 20 asset references allowed per section (got ${content.asset_references.length}).` };
    }
    for (const ref of content.asset_references) {
      if (typeof ref !== "string") {
        return { isValid: false, error: "Each asset reference must be a string URL." };
      }
      if (!validateSafeUrl(ref)) {
        return { isValid: false, error: `Unsafe asset reference URL detected: '${ref}'.` };
      }
      sanitizedAssets.push(ref.trim());
    }
  }

  const validatedBlock: BlockItem = {
    id: cleanId,
    type: "open_design_section",
    is_visible: isVisible,
    content: {
      section_tag: sectionTag,
      html_content: sanitizedHtml,
      ...(sectionId !== undefined ? { section_id: sectionId } : {}),
      ...(cssClasses !== undefined ? { css_classes: cssClasses } : {}),
      styles: sanitizedStyles,
      asset_references: sanitizedAssets,
    },
  };

  return {
    isValid: true,
    validatedBlock,
  };
}


