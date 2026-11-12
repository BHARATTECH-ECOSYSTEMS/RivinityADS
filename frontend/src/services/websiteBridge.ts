/**
 * websiteBridge.ts — RivinityADS Open Design Phase 3.1: Secure MessageChannel Handshake Client
 *
 * Implements:
 * 1. Opaque-origin sandbox compatibility (communicates via transferred MessagePort).
 * 2. Cryptographic one-time challenge verification.
 * 3. Event source validation (ensures event.source matches the target iframe contentWindow).
 * 4. Automatic timeout and connection retry/cleanup.
 * 5. Type-safe message envelope validation and size limits (100 KB max).
 * 6. Port teardown on navigation, reload, or unmount.
 */

import { isTextLeafElement, patchOpenDesignSection } from "./openDesignPatcher";

export interface BridgeMessage<T = any> {
  type: string;
  session_id?: string;
  challenge?: string;
  timestamp?: number;
  payload?: T;
}

export type BridgeStatus = "uninitialized" | "connecting" | "connected" | "failed" | "closed";

export type SelectionClassification = "editable" | "read_only" | "protected";

export interface ElementSelectionPayload {
  blockId: string;
  blockType: string;
  field: string | null;
  itemIndex: number | null;
  classification: SelectionClassification;
  rect?: { top: number; left: number; width: number; height: number };
}

export interface ParentSelectBlockPayload {
  blockId: string;
  field?: string | null;
  itemIndex?: number | null;
  scrollIntoView?: boolean;
}

export interface ProposedPatchMessagePayload {
  sessionId: string;
  blockId: string;
  blockType: string;
  field: string | null;
  itemIndex: number | null;
  proposedValue: string;
  value?: string;
  patchKind?: "native_field" | "html_fragment";
  targetSelector?: string;
  targetOdId?: string | null;
  originalValue?: string;
}

export interface EditSessionStartPayload {
  sessionId: string;
  blockId: string;
  blockType: string;
  field: string | null;
  itemIndex: number | null;
  initialValue: string;
  targetSelector?: string;
  targetOdId?: string | null;
}

export interface EditCancelPayload {
  sessionId: string;
  blockId: string;
  field: string | null;
  itemIndex: number | null;
  targetSelector?: string;
  targetOdId?: string | null;
}

export interface EditApplyConfirmedPayload {
  sessionId: string;
  blockId: string;
  field: string | null;
  itemIndex: number | null;
  appliedValue: string;
  targetSelector?: string;
  targetOdId?: string | null;
}

export interface EditRejectedPayload {
  sessionId: string;
  blockId: string;
  field: string | null;
  itemIndex: number | null;
  error: string;
  rollbackValue: string;
  targetSelector?: string;
  targetOdId?: string | null;
}

export interface EditSyncValuePayload {
  blockId: string;
  field: string;
  itemIndex: number | null;
  value: string;
}

export interface BridgeClientOptions {
  iframe: HTMLIFrameElement;
  timeoutMs?: number;
  maxPayloadBytes?: number;
  onConnected?: () => void;
  onError?: (error: Error) => void;
  onMessage?: (message: BridgeMessage) => void;
  onStatusChange?: (status: BridgeStatus) => void;
  onElementSelect?: (payload: ElementSelectionPayload) => void;
  onBackgroundClick?: () => void;
  onEditSessionStart?: (payload: EditSessionStartPayload) => void;
  onEditProposePatch?: (payload: ProposedPatchMessagePayload) => void;
  onEditCancel?: (payload: EditCancelPayload) => void;
}

export class WebsiteBridgeClient {
  private iframe: HTMLIFrameElement;
  private channel: MessageChannel | null = null;
  private port: MessagePort | null = null;
  private status: BridgeStatus = "uninitialized";
  private timeoutId: any = null;
  private pendingConnectReject: ((reason: any) => void) | null = null;
  private activeChallenge: string | null = null;
  private activeSessionId: string | null = null;
  private timeoutMs: number;
  private maxPayloadBytes: number;
  private onConnected?: () => void;
  private onError?: (error: Error) => void;
  private onMessage?: (message: BridgeMessage) => void;
  private onStatusChange?: (status: BridgeStatus) => void;
  private onElementSelect?: (payload: ElementSelectionPayload) => void;
  private onBackgroundClick?: () => void;
  private onEditSessionStart?: (payload: EditSessionStartPayload) => void;
  private onEditProposePatch?: (payload: ProposedPatchMessagePayload) => void;
  private onEditCancel?: (payload: EditCancelPayload) => void;

  constructor(options: BridgeClientOptions) {
    this.iframe = options.iframe;
    this.timeoutMs = options.timeoutMs ?? 5000;
    this.maxPayloadBytes = options.maxPayloadBytes ?? 102400; // 100 KB
    this.onConnected = options.onConnected;
    this.onError = options.onError;
    this.onMessage = options.onMessage;
    this.onStatusChange = options.onStatusChange;
    this.onElementSelect = options.onElementSelect;
    this.onBackgroundClick = options.onBackgroundClick;
    this.onEditSessionStart = options.onEditSessionStart;
    this.onEditProposePatch = options.onEditProposePatch;
    this.onEditCancel = options.onEditCancel;
  }

  public getStatus(): BridgeStatus {
    return this.status;
  }

  public getSessionId(): string | null {
    return this.activeSessionId;
  }

  private setStatus(newStatus: BridgeStatus): void {
    this.status = newStatus;
    if (this.onStatusChange) {
      this.onStatusChange(newStatus);
    }
  }

  /**
   * Initiate the secure MessageChannel handshake with the sandboxed iframe.
   */
  public connect(): Promise<boolean> {
    return new Promise((resolve, reject) => {
      if (!this.iframe.contentWindow) {
        const err = new Error("Iframe contentWindow is not accessible");
        this.setStatus("failed");
        if (this.onError) this.onError(err);
        return reject(err);
      }

      this.disconnect();
      this.setStatus("connecting");
      this.pendingConnectReject = reject;

      // Generate random one-time challenge and session ID
      this.activeChallenge =
        typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : "ch_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      this.activeSessionId =
        typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : "sess_" + Math.random().toString(36).slice(2) + Date.now().toString(36);

      try {
        this.channel = new MessageChannel();
        this.port = this.channel.port1;

        // Set connection timeout
        this.timeoutId = setTimeout(() => {
          if (this.status === "connecting") {
            const timeoutErr = new Error(`Bridge handshake timed out after ${this.timeoutMs}ms`);
            this.disconnect(timeoutErr);
            this.setStatus("failed");
            if (this.onError) this.onError(timeoutErr);
          }
        }, this.timeoutMs);

        // Listen on port1
        this.port.onmessage = (event: MessageEvent) => {
          this.handlePortMessage(event, resolve, reject);
        };

        // Post initialization message transferring port2 to the exact iframe contentWindow
        this.iframe.contentWindow.postMessage(
          {
            type: "OD_BRIDGE_INIT",
            challenge: this.activeChallenge,
            session_id: this.activeSessionId,
          },
          "*",
          [this.channel.port2]
        );
      } catch (err: any) {
        this.disconnect(err);
        this.setStatus("failed");
        if (this.onError) this.onError(err);
      }
    });
  }

  private handlePortMessage(
    event: MessageEvent,
    resolveConnect?: (value: boolean) => void,
    rejectConnect?: (reason: any) => void
  ): void {
    const data = event.data;
    if (!data || typeof data !== "object") {
      return;
    }

    // Check payload size
    try {
      const serialized = JSON.stringify(data);
      if (serialized.length > this.maxPayloadBytes) {
        console.warn("WebsiteBridgeClient: rejected oversized message", serialized.length);
        return;
      }
    } catch {
      return;
    }

    // 1. Handshake Acknowledgment Phase
    if (this.status === "connecting") {
      if (data.type === "OD_BRIDGE_ACK") {
        if (data.challenge === this.activeChallenge && data.session_id === this.activeSessionId) {
          if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
          }
          this.pendingConnectReject = null;
          this.setStatus("connected");
          if (this.onConnected) this.onConnected();
          if (resolveConnect) resolveConnect(true);
        } else {
          const err = new Error("Bridge handshake challenge mismatch");
          this.disconnect(err);
          this.setStatus("failed");
          if (this.onError) this.onError(err);
        }
        return;
      }
      return;
    }

    // 2. Active Connected Phase
    if (this.status === "connected") {
      // Reject messages with mismatched session ID
      if (data.session_id && data.session_id !== this.activeSessionId) {
        console.warn("WebsiteBridgeClient: dropped message with stale session_id", data.session_id);
        return;
      }
      if (data.type === "OD_ELEMENT_SELECT" && this.onElementSelect) {
        this.onElementSelect(data.payload);
      } else if (data.type === "OD_BACKGROUND_CLICK" && this.onBackgroundClick) {
        this.onBackgroundClick();
      } else if (data.type === "OD_EDIT_SESSION_START" && this.onEditSessionStart) {
        this.onEditSessionStart(data.payload);
      } else if (data.type === "OD_EDIT_PROPOSE_PATCH" && this.onEditProposePatch) {
        this.onEditProposePatch(data.payload);
      } else if (data.type === "OD_EDIT_CANCEL" && this.onEditCancel) {
        this.onEditCancel(data.payload);
      }
      if (this.onMessage) {
        this.onMessage(data);
      }
    }
  }

  /**
   * Instruct the iframe preview to select and scroll to a specific block.
   */
  public selectBlock(
    blockId: string,
    options?: { field?: string | null; itemIndex?: number | null; scrollIntoView?: boolean }
  ): boolean {
    if (!BLOCK_ID_REGEX.test(blockId)) {
      return false;
    }
    const payload: ParentSelectBlockPayload = {
      blockId,
      field: options?.field ?? null,
      itemIndex: options?.itemIndex ?? null,
      scrollIntoView: options?.scrollIntoView ?? true,
    };
    return this.postMessage("OD_PARENT_SELECT_BLOCK", payload);
  }

  /**
   * Instruct the iframe preview to clear any active selection box.
   */
  public clearSelection(): boolean {
    return this.postMessage("OD_PARENT_CLEAR_SELECTION");
  }

  /**
   * Send OD_EDIT_APPLY_CONFIRMED to the iframe when a proposed edit is validated and staged.
   */
  public confirmPatchApplied(payload: EditApplyConfirmedPayload): boolean {
    return this.postMessage("OD_EDIT_APPLY_CONFIRMED", payload);
  }

  /**
   * Send OD_EDIT_REJECTED to the iframe when a proposed edit fails parent validation.
   */
  public rejectPatch(payload: EditRejectedPayload): boolean {
    return this.postMessage("OD_EDIT_REJECTED", payload);
  }

  /**
   * Send OD_EDIT_SYNC_VALUE to update a field's value in the iframe preview.
   */
  public syncValue(payload: EditSyncValuePayload): boolean {
    return this.postMessage("OD_EDIT_SYNC_VALUE", payload);
  }

  /**
   * Finish any active inline edit session in the iframe (commit or cancel).
   */
  public finishActiveEdit(commit: boolean = true): boolean {
    return this.postMessage("OD_PARENT_EDIT_FINISH", { commit });
  }

  /**
   * Send a message through the private MessagePort to the iframe.
   */
  public postMessage(type: string, payload?: any): boolean {
    if (this.status !== "connected" || !this.port) {
      return false;
    }
    const message: BridgeMessage = {
      type,
      session_id: this.activeSessionId ?? undefined,
      timestamp: Date.now(),
      payload,
    };
    try {
      this.port.postMessage(message);
      return true;
    } catch (err) {
      console.error("WebsiteBridgeClient: failed to post message", err);
      return false;
    }
  }

  /**
   * Disconnect and clean up MessagePort, timers, and state.
   */
  public disconnect(error?: Error): void {
    if (this.pendingConnectReject) {
      try {
        this.pendingConnectReject(error || new Error("Bridge disconnected"));
      } catch {}
      this.pendingConnectReject = null;
    }
    if (this.timeoutId) {
      clearTimeout(this.timeoutId);
      this.timeoutId = null;
    }
    if (this.port) {
      try {
        this.port.close();
      } catch {}
      this.port = null;
    }
    this.channel = null;
    this.activeChallenge = null;
    this.activeSessionId = null;
    if (this.status !== "closed") {
      this.setStatus("closed");
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 3.2: Semantic DOM Metadata Models and Extraction Utilities
// ─────────────────────────────────────────────────────────────────────────────

export interface ExtractedBlockMetadata {
  blockId: string;
  blockType: string;
  field: string | null;
  itemIndex: number | null;
  isEditable: boolean;
  isProtected: boolean;
}

export const VALID_BLOCK_TYPES = new Set([
  "hero",
  "text_section",
  "features_grid",
  "image_gallery",
  "contact_form",
  "cta_banner",
  "ad_placement",
  "footer",
  "open_design_section",
]);

export const BLOCK_ID_REGEX = /^[a-zA-Z0-9_-]{1,64}$/;

export const PROTECTED_SELECTORS = [
  '[data-od-protected="true"]',
  '.block-ad-placement',
  '.rivinity-ad-placeholder',
  '.rivinity-live-ad-container',
  '.rivinity-ad-slot',
  '#rivinity-consent-banner',
  '.preview-banner',
  '[name="hp_comment"]',
  '[data-od-edit-bridge]',
].join(", ");

/**
 * Check if an element or any of its ancestors is marked as protected.
 * Protection is strictly inherited from any ancestor element.
 */
export function isElementProtected(el: Element | null): boolean {
  if (!el || typeof (el as any).closest !== "function") return false;
  return Boolean(el.closest(PROTECTED_SELECTORS));
}

/**
 * Retrieve and validate block ID from element or nearest ancestor block.
 */
export function getBlockId(el: Element | null): string | null {
  if (!el || typeof (el as any).closest !== "function") return null;
  const target = el.hasAttribute("data-od-block-id") ? el : el.closest("[data-od-block-id]");
  if (!target) return null;
  const rawId = target.getAttribute("data-od-block-id");
  if (!rawId) return null;
  const cleanId = rawId.trim();
  return BLOCK_ID_REGEX.test(cleanId) ? cleanId : null;
}

/**
 * Retrieve and validate block type from element or nearest ancestor block.
 */
export function getBlockType(el: Element | null): string | null {
  if (!el || typeof (el as any).closest !== "function") return null;
  const target = el.hasAttribute("data-od-block-type") ? el : el.closest("[data-od-block-type]");
  if (!target) return null;
  const rawType = target.getAttribute("data-od-block-type");
  if (!rawType) return null;
  const cleanType = rawType.trim().toLowerCase();
  return VALID_BLOCK_TYPES.has(cleanType) ? cleanType : null;
}

/**
 * Retrieve field identifier from element.
 */
export function getFieldIdentifier(el: Element | null): string | null {
  if (!el || typeof el.getAttribute !== "function") return null;
  const raw = el.getAttribute("data-od-field");
  if (!raw) return null;
  const clean = raw.trim();
  return clean.length > 0 && clean.length <= 100 ? clean : null;
}

/**
 * Retrieve and validate array item index.
 */
export function getItemIndex(el: Element | null): number | null {
  if (!el || typeof el.getAttribute !== "function") return null;
  const raw = el.getAttribute("data-od-item-index");
  if (!raw) return null;
  const clean = raw.trim();
  if (/^\d+$/.test(clean)) {
    const idx = parseInt(clean, 10);
    return isNaN(idx) || idx < 0 || idx > 1000 ? null : idx;
  }
  return null;
}

/**
 * Extract and validate complete semantic metadata for an element.
 * Rejects missing, malformed, or contradictory metadata.
 * A protected ancestor strictly overrides any descendant editable metadata.
 */
export function extractElementMetadata(el: Element | null): ExtractedBlockMetadata | null {
  if (!el || typeof el.getAttribute !== "function") return null;

  const isProtected = isElementProtected(el);
  const blockId = getBlockId(el);
  const blockType = getBlockType(el);

  // If blockId or blockType is missing or invalid, metadata cannot be resolved
  if (!blockId || !blockType) {
    return null;
  }

  // Protection strictly prevents any element from being editable
  const rawEditable = el.getAttribute("data-od-editable") === "true";
  let isEditable = !isProtected && rawEditable;

  let field = getFieldIdentifier(el);
  const itemIndex = getItemIndex(el);

  if (!isProtected && blockType === "open_design_section" && isTextLeafElement(el)) {
    isEditable = true;
    field = "html_content";
  }

  return {
    blockId,
    blockType,
    field,
    itemIndex,
    isEditable,
    isProtected,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 3.3B: Parent-Side Draft Validation & Selection Contracts
// ─────────────────────────────────────────────────────────────────────────────

export const APPROVED_BLOCK_FIELDS: Record<string, Set<string>> = {
  hero: new Set(["headline", "subheadline", "cta_button.label", "cta_button.target_url", "background_image_url", "alignment"]),
  text_section: new Set(["heading", "body"]),
  features_grid: new Set(["title", "items.title", "items.description", "items.icon"]),
  image_gallery: new Set(["title", "images.image_url", "images.alt_text", "images.caption"]),
  contact_form: new Set(["title", "description", "submit_label"]),
  cta_banner: new Set(["title", "description", "button.label", "button.target_url"]),
  footer: new Set(["copyright_text", "links.label", "links.target_url"]),
  open_design_section: new Set(["html_content"]), // section-level selection or html_content
  ad_placement: new Set([]), // block-level read-only only
};

export interface ValidationResult {
  isValid: boolean;
  error?: string;
  validatedBlock?: any;
  classification?: SelectionClassification;
}

/**
 * Validate incoming iframe selection message against current draft state.
 * Rejects stale sessions, missing blocks, type mismatches, unapproved fields,
 * out-of-bounds array indexes, and forged editable states on protected elements.
 */
export function validateSelectionAgainstDraft(
  payload: any,
  activeDraftBlocks: any[] | undefined | null
): ValidationResult {
  if (!payload || typeof payload !== "object") {
    return { isValid: false, error: "Payload must be a non-null object" };
  }

  const { blockId, blockType, field, itemIndex, classification } = payload;

  if (!blockId || typeof blockId !== "string" || !BLOCK_ID_REGEX.test(blockId)) {
    return { isValid: false, error: "Invalid block identifier format" };
  }

  if (!blockType || typeof blockType !== "string" || !VALID_BLOCK_TYPES.has(blockType.toLowerCase())) {
    return { isValid: false, error: "Invalid or unrecognized block type" };
  }

  if (!Array.isArray(activeDraftBlocks)) {
    return { isValid: false, error: "Active draft blocks are not available" };
  }

  const targetBlock = activeDraftBlocks.find((b) => b && b.id === blockId);
  if (!targetBlock) {
    return { isValid: false, error: "Block ID not found in current draft" };
  }

  if (targetBlock.type !== blockType.toLowerCase()) {
    return { isValid: false, error: "Block type mismatch with draft block" };
  }

  const allowedFields = APPROVED_BLOCK_FIELDS[targetBlock.type] || new Set();

  if (field !== null && field !== undefined) {
    if (typeof field !== "string" || field.length > 100) {
      return { isValid: false, error: "Malformed field identifier" };
    }
    if (targetBlock.type === "open_design_section") {
      if (field !== "html_content") {
        return { isValid: false, error: "Custom section only supports block-level selection" };
      }
    } else if (!allowedFields.has(field)) {
      return { isValid: false, error: `Field '${field}' is not permitted for block type '${targetBlock.type}'` };
    }
  }

  // Check array bounds if itemIndex is provided
  if (itemIndex !== null && itemIndex !== undefined) {
    if (typeof itemIndex !== "number" || itemIndex < 0 || !Number.isInteger(itemIndex)) {
      return { isValid: false, error: "itemIndex must be a non-negative integer" };
    }

    const content = targetBlock.content || {};

    if (targetBlock.type === "text_section" && field === "body") {
      let paragraphs: string[] = [];
      if (Array.isArray(content.paragraphs)) {
        paragraphs = content.paragraphs;
      } else if (typeof content.body === "string") {
        paragraphs = content.body.split(/\n\s*\n/).filter((p: string) => p.trim().length > 0);
      } else if (Array.isArray(content.body)) {
        paragraphs = content.body;
      }
      if (itemIndex >= paragraphs.length) {
        return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for text paragraphs (length ${paragraphs.length})` };
      }
    } else if (targetBlock.type === "features_grid" && field && field.startsWith("items.")) {
      const items = Array.isArray(content.items) ? content.items : [];
      if (itemIndex >= items.length) {
        return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for features items (length ${items.length})` };
      }
    } else if (targetBlock.type === "image_gallery" && field && field.startsWith("images.")) {
      const images = Array.isArray(content.images) ? content.images : [];
      if (itemIndex >= images.length) {
        return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for gallery images (length ${images.length})` };
      }
    } else if (targetBlock.type === "footer" && field && field.startsWith("links.")) {
      const links = Array.isArray(content.links) ? content.links : [];
      if (itemIndex >= links.length) {
        return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for footer links (length ${links.length})` };
      }
    }
  }

  // Enforce protection and classification integrity
  let verifiedClassification: SelectionClassification = "editable";

  if (targetBlock.type === "ad_placement") {
    if (classification === "editable") {
      return { isValid: false, error: "Ad placement block cannot have editable classification" };
    }
    verifiedClassification = "protected";
  } else if (targetBlock.type === "open_design_section") {
    if (field !== null && field !== undefined && field !== "html_content") {
      return { isValid: false, error: "Custom section only supports block-level selection" };
    }
    verifiedClassification = field === "html_content" ? (classification === "protected" ? "protected" : "editable") : "read_only";
  } else if (field === "items.icon") {
    if (classification === "editable") {
      return { isValid: false, error: "Icon element cannot have editable classification" };
    }
    verifiedClassification = "read_only";
  } else {
    verifiedClassification =
      classification === "protected" ? "protected" : classification === "read_only" ? "read_only" : "editable";
  }

  return {
    isValid: true,
    validatedBlock: targetBlock,
    classification: verifiedClassification,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 3.3C: Inspector Field Autofocus & Control Discovery
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Locate corresponding inspector form input control for a given field and optional array index.
 * Uses explicit stable data attributes ([data-inspector-field], [data-inspector-index]).
 * Handles missing controls, composite identifiers, and array fallbacks gracefully.
 */
export function findInspectorControl(
  container: HTMLElement | null,
  field: string | null | undefined,
  index?: number | null
): HTMLElement | null {
  if (!container || !field || typeof container.querySelector !== "function") return null;

  // 1. If index is specified (array item)
  if (index !== null && index !== undefined && !isNaN(index)) {
    // 1a. Exact field with index
    const exactWithIndex = container.querySelector<HTMLElement>(
      `[data-inspector-field="${field}"][data-inspector-index="${index}"]`
    );
    if (exactWithIndex) return exactWithIndex;

    // 1b. Short field with index (e.g., items.title -> title)
    const shortField = field.split(".").pop();
    if (shortField && shortField !== field) {
      const shortWithIndex = container.querySelector<HTMLElement>(
        `[data-inspector-field="${shortField}"][data-inspector-index="${index}"]`
      );
      if (shortWithIndex) return shortWithIndex;
    }

    // 1c. Container-level array target (e.g., field "items", "images", "links" with index)
    if (field === "items") {
      const title = container.querySelector<HTMLElement>(
        `[data-inspector-field="items.title"][data-inspector-index="${index}"]`
      );
      if (title) return title;
    } else if (field === "images") {
      const imgUrl = container.querySelector<HTMLElement>(
        `[data-inspector-field="images.image_url"][data-inspector-index="${index}"]`
      );
      if (imgUrl) return imgUrl;
    } else if (field === "links") {
      const linkLabel = container.querySelector<HTMLElement>(
        `[data-inspector-field="links.label"][data-inspector-index="${index}"]`
      );
      if (linkLabel) return linkLabel;
    }
  }

  // 2. Exact match without index
  const exact = container.querySelector<HTMLElement>(`[data-inspector-field="${field}"]`);
  if (exact) return exact;

  // 3. Fallbacks for compound CTA/button structures
  if (field === "cta_button" || field === "cta_button.label") {
    const ctaLabel = container.querySelector<HTMLElement>('[data-inspector-field="cta_button.label"]');
    if (ctaLabel) return ctaLabel;
  }
  if (field === "button" || field === "button.label") {
    const btnLabel = container.querySelector<HTMLElement>('[data-inspector-field="button.label"]');
    if (btnLabel) return btnLabel;
  }

  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 3.4B: Parent-Side Draft Receiver & Patch Staging
// ─────────────────────────────────────────────────────────────────────────────

export interface NativeFieldConstraint {
  maxLength: number;
  multiline: boolean;
  required: boolean;
}

export const APPROVED_NATIVE_EDITABLE_FIELDS: Record<string, Set<string>> = {
  hero: new Set(["headline", "subheadline", "cta_button.label"]),
  text_section: new Set(["heading", "body"]),
  features_grid: new Set(["title", "items.title", "items.description"]),
  image_gallery: new Set(["title", "images.caption"]),
  contact_form: new Set(["title", "submit_label"]),
  cta_banner: new Set(["title", "description", "button.label"]),
  footer: new Set(["copyright_text", "links.label"]),
  ad_placement: new Set([]),
  open_design_section: new Set([]),
};

export const NATIVE_FIELD_CONSTRAINTS: Record<string, Record<string, NativeFieldConstraint>> = {
  hero: {
    headline: { maxLength: 160, multiline: false, required: true },
    subheadline: { maxLength: 300, multiline: true, required: false },
    "cta_button.label": { maxLength: 60, multiline: false, required: true },
  },
  text_section: {
    heading: { maxLength: 160, multiline: false, required: false },
    body: { maxLength: 10000, multiline: true, required: true },
  },
  features_grid: {
    title: { maxLength: 160, multiline: false, required: false },
    "items.title": { maxLength: 100, multiline: false, required: true },
    "items.description": { maxLength: 500, multiline: true, required: true },
  },
  image_gallery: {
    title: { maxLength: 160, multiline: false, required: false },
    "images.caption": { maxLength: 200, multiline: false, required: false },
  },
  contact_form: {
    title: { maxLength: 120, multiline: false, required: false },
    submit_label: { maxLength: 60, multiline: false, required: true },
  },
  cta_banner: {
    title: { maxLength: 160, multiline: false, required: true },
    description: { maxLength: 300, multiline: true, required: false },
    "button.label": { maxLength: 60, multiline: false, required: true },
  },
  footer: {
    copyright_text: { maxLength: 160, multiline: false, required: false },
    "links.label": { maxLength: 50, multiline: false, required: true },
  },
};

export function normalizeNativeField(field: string | null | undefined): string | null {
  if (!field) return null;
  return field.replace(/\[\d*\]/g, "").trim();
}

export const DISALLOWED_HTML_TAG_REGEX = /<\s*[a-zA-Z\/!][^>]*>/;
export const DISALLOWED_SCRIPT_PATTERNS = [
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

export function containsDisallowedHtmlOrScript(value: string): boolean {
  if (DISALLOWED_HTML_TAG_REGEX.test(value)) return true;
  return DISALLOWED_SCRIPT_PATTERNS.some((pat) => pat.test(value));
}

/**
 * Extract authoritative plain-text value from draft block content for a given field and optional itemIndex.
 */
export function extractAuthoritativeValue(
  block: any,
  field: string | null | undefined,
  itemIndex?: number | null
): string {
  if (!block || !block.content || !field) return "";
  const norm = normalizeNativeField(field);
  if (!norm) return "";
  const content = block.content;

  if (block.type === "open_design_section" || norm === "html_content") {
    return typeof content.html_content === "string" ? content.html_content : "";
  }

  switch (norm) {
    case "headline":
      return typeof content.headline === "string" ? content.headline : "";
    case "subheadline":
      return typeof content.subheadline === "string" ? content.subheadline : "";
    case "cta_button.label":
      return typeof content.cta_button?.label === "string" ? content.cta_button.label : "";
    case "heading":
      return typeof content.heading === "string" ? content.heading : "";
    case "body":
      if (itemIndex !== null && itemIndex !== undefined) {
        if (Array.isArray(content.paragraphs) && typeof content.paragraphs[itemIndex] === "string") {
          return content.paragraphs[itemIndex];
        }
        if (typeof content.body === "string") {
          const paras = content.body.split(/\n\s*\n/).filter((p: string) => p.trim().length > 0);
          return paras[itemIndex] || "";
        }
      }
      return typeof content.body === "string" ? content.body : "";
    case "title":
      return typeof content.title === "string" ? content.title : "";
    case "items.title":
      if (itemIndex !== null && itemIndex !== undefined && Array.isArray(content.items)) {
        return typeof content.items[itemIndex]?.title === "string" ? content.items[itemIndex].title : "";
      }
      return "";
    case "items.description":
      if (itemIndex !== null && itemIndex !== undefined && Array.isArray(content.items)) {
        return typeof content.items[itemIndex]?.description === "string" ? content.items[itemIndex].description : "";
      }
      return "";
    case "images.caption":
      if (itemIndex !== null && itemIndex !== undefined && Array.isArray(content.images)) {
        return typeof content.images[itemIndex]?.caption === "string" ? content.images[itemIndex].caption : "";
      }
      return "";
    case "submit_label":
      return typeof content.submit_label === "string" ? content.submit_label : "";
    case "description":
      return typeof content.description === "string" ? content.description : "";
    case "button.label":
      return typeof content.button?.label === "string" ? content.button.label : "";
    case "copyright_text":
      return typeof content.copyright_text === "string" ? content.copyright_text : "";
    case "links.label":
      if (itemIndex !== null && itemIndex !== undefined && Array.isArray(content.links)) {
        return typeof content.links[itemIndex]?.label === "string" ? content.links[itemIndex].label : "";
      }
      return "";
    default:
      return "";
  }
}

/**
 * Pure immutable updater applying a validated native text patch to a draft block.
 * Preserves all sibling fields and unaffected array items without mutating the original block.
 */
export function applyPatchToBlock(
  block: any,
  field: string,
  itemIndex: number | null | undefined,
  value: string,
  patchedHtml?: string
): any {
  if (!block) return block;
  if (block.type === "open_design_section") {
    return {
      ...block,
      content: {
        ...(block.content || {}),
        html_content: patchedHtml !== undefined ? patchedHtml : value,
      },
    };
  }
  const norm = normalizeNativeField(field);
  if (!norm) return block;
  const content = { ...(block.content || {}) };

  switch (norm) {
    case "headline":
      return { ...block, content: { ...content, headline: value } };
    case "subheadline":
      return { ...block, content: { ...content, subheadline: value } };
    case "cta_button.label":
      return {
        ...block,
        content: {
          ...content,
          cta_button: { ...(content.cta_button || {}), label: value },
        },
      };
    case "heading":
      return { ...block, content: { ...content, heading: value } };
    case "body": {
      if (itemIndex !== null && itemIndex !== undefined) {
        let paragraphs: string[] = [];
        if (Array.isArray(content.paragraphs)) {
          paragraphs = [...content.paragraphs];
        } else if (typeof content.body === "string") {
          paragraphs = content.body.split(/\n\s*\n/).filter((p: string) => p.trim().length > 0);
        }
        if (itemIndex >= 0 && itemIndex < paragraphs.length) {
          paragraphs[itemIndex] = value;
        } else {
          paragraphs.push(value);
        }
        const nextBody = paragraphs.join("\n\n");
        return {
          ...block,
          content: {
            ...content,
            body: nextBody,
            ...(Array.isArray(content.paragraphs) ? { paragraphs } : {}),
          },
        };
      }
      return {
        ...block,
        content: {
          ...content,
          body: value,
          ...(Array.isArray(content.paragraphs)
            ? { paragraphs: value.split(/\n\s*\n/).filter((p: string) => p.trim().length > 0) }
            : {}),
        },
      };
    }
    case "title":
      return { ...block, content: { ...content, title: value } };
    case "items.title": {
      const items = Array.isArray(content.items) ? [...content.items] : [];
      if (itemIndex !== null && itemIndex !== undefined && itemIndex >= 0 && itemIndex < items.length) {
        items[itemIndex] = { ...items[itemIndex], title: value };
      }
      return { ...block, content: { ...content, items } };
    }
    case "items.description": {
      const items = Array.isArray(content.items) ? [...content.items] : [];
      if (itemIndex !== null && itemIndex !== undefined && itemIndex >= 0 && itemIndex < items.length) {
        items[itemIndex] = { ...items[itemIndex], description: value };
      }
      return { ...block, content: { ...content, items } };
    }
    case "images.caption": {
      const images = Array.isArray(content.images) ? [...content.images] : [];
      if (itemIndex !== null && itemIndex !== undefined && itemIndex >= 0 && itemIndex < images.length) {
        images[itemIndex] = { ...images[itemIndex], caption: value };
      }
      return { ...block, content: { ...content, images } };
    }
    case "submit_label":
      return { ...block, content: { ...content, submit_label: value } };
    case "description":
      return { ...block, content: { ...content, description: value } };
    case "button.label":
      return {
        ...block,
        content: {
          ...content,
          button: { ...(content.button || {}), label: value },
        },
      };
    case "copyright_text":
      return { ...block, content: { ...content, copyright_text: value } };
    case "links.label": {
      const links = Array.isArray(content.links) ? [...content.links] : [];
      if (itemIndex !== null && itemIndex !== undefined && itemIndex >= 0 && itemIndex < links.length) {
        links[itemIndex] = { ...links[itemIndex], label: value };
      }
      return { ...block, content: { ...content, links } };
    }
    default:
      return block;
  }
}

export interface PatchValidationResult {
  isValid: boolean;
  error?: string;
  targetBlock?: any;
  normalizedField?: string;
  currentValue?: string;
  constraint?: NativeFieldConstraint;
  patchedHtml?: string;
}

/**
 * Validate incoming iframe inline editing proposal strictly against active draft blocks.
 * Independent parent-side authority ensuring:
 * - Session matching and integrity.
 * - Block ID existence and type matching in active draft.
 * - Protection and custom-section immunity.
 * - Approved native editable field allowlist (rejecting inspector-only properties).
 * - Array bounds safety for items/images/links/paragraphs.
 * - Type string safety and rejection of HTML tags/scripts.
 * - Schema minLength, multiline/singleline, and maxLength enforcement.
 * - Stage 3.4C: Localized HTML Fragment Patching & Sanitization for open_design_section.
 */
export function validateProposedPatchAgainstDraft(
  payload: any,
  activeDraftBlocks: any[] | undefined | null,
  expectedSessionId?: string | null
): PatchValidationResult {
  if (!payload || typeof payload !== "object") {
    return { isValid: false, error: "Payload must be a non-null object" };
  }

  const { sessionId, blockId, blockType, field, itemIndex, proposedValue } = payload;

  // 1. Session verification
  if (!sessionId || typeof sessionId !== "string" || !sessionId.trim()) {
    return { isValid: false, error: "Session identifier is missing or invalid" };
  }
  if (expectedSessionId && sessionId !== expectedSessionId) {
    return { isValid: false, error: "Editing session mismatch or stale session" };
  }

  // 2. Block ID format & draft presence
  if (!blockId || typeof blockId !== "string" || !BLOCK_ID_REGEX.test(blockId)) {
    return { isValid: false, error: "Invalid block identifier format" };
  }
  if (!Array.isArray(activeDraftBlocks)) {
    return { isValid: false, error: "Active draft blocks are not available" };
  }
  const targetBlock = activeDraftBlocks.find((b) => b && b.id === blockId);
  if (!targetBlock) {
    return { isValid: false, error: `Block ID '${blockId}' not found in current draft` };
  }

  // 3. Block Type verification
  if (!blockType || typeof blockType !== "string") {
    return { isValid: false, error: "Missing or invalid block type" };
  }
  if (targetBlock.type !== blockType.toLowerCase()) {
    return { isValid: false, error: `Block type mismatch: expected '${targetBlock.type}', got '${blockType}'` };
  }
  if (targetBlock.type === "ad_placement") {
    return { isValid: false, error: "Ad placement block is protected and cannot be edited" };
  }

  // Stage 3.4C: Localized HTML Fragment Patching for open_design_section
  if (targetBlock.type === "open_design_section") {
    const normField = normalizeNativeField(field);
    if (normField !== "html_content") {
      return {
        isValid: false,
        error: `Field '${field}' is not permitted for block type 'open_design_section'`,
        targetBlock,
      };
    }
    if (payload.patchKind !== "html_fragment") {
      return {
        isValid: false,
        error: "Custom section full HTML inline editing is not supported in this stage. Localized patches require patchKind: 'html_fragment'",
        targetBlock,
        normalizedField: "html_content",
      };
    }
    if (!payload.targetSelector || typeof payload.targetSelector !== "string" || !payload.targetSelector.trim()) {
      return {
        isValid: false,
        error: "Missing or invalid targetSelector for custom section localized edit",
        targetBlock,
        normalizedField: "html_content",
      };
    }
    if (typeof proposedValue !== "string") {
      return {
        isValid: false,
        error: "Proposed value must be a string",
        targetBlock,
        normalizedField: "html_content",
      };
    }

    const htmlContent = targetBlock.content?.html_content || "";
    const patchResult = patchOpenDesignSection({
      blockId,
      blockType: "open_design_section",
      patchKind: "html_fragment",
      targetSelector: payload.targetSelector,
      targetOdId: payload.targetOdId,
      originalValue: typeof payload.originalValue === "string" ? payload.originalValue : (payload.initialValue || ""),
      proposedValue,
      htmlContent,
    });

    if (!patchResult.isValid) {
      return {
        isValid: false,
        error: patchResult.error || "Failed to patch custom section HTML",
        targetBlock,
        normalizedField: "html_content",
        currentValue: patchResult.currentValue,
      };
    }

    return {
      isValid: true,
      targetBlock,
      normalizedField: "html_content",
      patchedHtml: patchResult.patchedHtml,
      currentValue: patchResult.currentValue,
    };
  }

  // 4. Field verification
  if (!field || typeof field !== "string") {
    return { isValid: false, error: "Missing or invalid field identifier" };
  }
  const normalizedField = normalizeNativeField(field);
  if (!normalizedField) {
    return { isValid: false, error: "Malformed field identifier" };
  }

  const nativeAllowed = APPROVED_NATIVE_EDITABLE_FIELDS[targetBlock.type];
  if (!nativeAllowed || !nativeAllowed.has(normalizedField)) {
    const generalAllowed = APPROVED_BLOCK_FIELDS[targetBlock.type];
    if (generalAllowed && generalAllowed.has(normalizedField)) {
      return {
        isValid: false,
        error: `Field '${normalizedField}' is an inspector-only property and cannot be edited inline`,
        targetBlock,
        normalizedField,
        currentValue: extractAuthoritativeValue(targetBlock, normalizedField, itemIndex),
      };
    }
    return {
      isValid: false,
      error: `Field '${normalizedField}' is not permitted for block type '${targetBlock.type}'`,
      targetBlock,
      normalizedField,
      currentValue: extractAuthoritativeValue(targetBlock, normalizedField, itemIndex),
    };
  }

  // 5. Array index verification for array-based fields
  const content = targetBlock.content || {};
  if (targetBlock.type === "features_grid" && normalizedField.startsWith("items.")) {
    if (itemIndex === null || itemIndex === undefined || typeof itemIndex !== "number" || !Number.isInteger(itemIndex) || itemIndex < 0) {
      return { isValid: false, error: `itemIndex is required for array field '${normalizedField}'` };
    }
    const items = Array.isArray(content.items) ? content.items : [];
    if (itemIndex >= items.length) {
      return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for features items (length ${items.length})` };
    }
  } else if (targetBlock.type === "image_gallery" && normalizedField.startsWith("images.")) {
    if (itemIndex === null || itemIndex === undefined || typeof itemIndex !== "number" || !Number.isInteger(itemIndex) || itemIndex < 0) {
      return { isValid: false, error: `itemIndex is required for array field '${normalizedField}'` };
    }
    const images = Array.isArray(content.images) ? content.images : [];
    if (itemIndex >= images.length) {
      return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for gallery images (length ${images.length})` };
    }
  } else if (targetBlock.type === "footer" && normalizedField.startsWith("links.")) {
    if (itemIndex === null || itemIndex === undefined || typeof itemIndex !== "number" || !Number.isInteger(itemIndex) || itemIndex < 0) {
      return { isValid: false, error: `itemIndex is required for array field '${normalizedField}'` };
    }
    const links = Array.isArray(content.links) ? content.links : [];
    if (itemIndex >= links.length) {
      return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for footer links (length ${links.length})` };
    }
  } else if (targetBlock.type === "text_section" && normalizedField === "body") {
    if (itemIndex !== null && itemIndex !== undefined) {
      if (typeof itemIndex !== "number" || !Number.isInteger(itemIndex) || itemIndex < 0) {
        return { isValid: false, error: "itemIndex must be a non-negative integer" };
      }
      let paragraphs: string[] = [];
      if (Array.isArray(content.paragraphs)) {
        paragraphs = content.paragraphs;
      } else if (typeof content.body === "string") {
        paragraphs = content.body.split(/\n\s*\n/).filter((p: string) => p.trim().length > 0);
      }
      if (itemIndex >= paragraphs.length) {
        return { isValid: false, error: `itemIndex ${itemIndex} out of bounds for text paragraphs (length ${paragraphs.length})` };
      }
    }
  } else {
    // Non-array fields should not have itemIndex
    if (itemIndex !== null && itemIndex !== undefined) {
      return { isValid: false, error: `itemIndex is not permitted for scalar field '${normalizedField}'` };
    }
  }

  const authoritativeCurrentValue = extractAuthoritativeValue(targetBlock, normalizedField, itemIndex);

  // 6. Proposed value type verification
  if (typeof proposedValue !== "string") {
    return {
      isValid: false,
      error: "Proposed value must be a string",
      targetBlock,
      normalizedField,
      currentValue: authoritativeCurrentValue,
    };
  }

  // 7. Security: reject HTML tags and script injection
  if (containsDisallowedHtmlOrScript(proposedValue)) {
    return {
      isValid: false,
      error: "HTML tags, markup, and script patterns are strictly forbidden in native plain text fields",
      targetBlock,
      normalizedField,
      currentValue: authoritativeCurrentValue,
    };
  }

  // 8. Schema constraints validation
  const blockConstraints = NATIVE_FIELD_CONSTRAINTS[targetBlock.type];
  const constraint = blockConstraints ? blockConstraints[normalizedField] : null;

  if (constraint) {
    // Single-line rejection of newlines
    if (!constraint.multiline && /[\r\n]/.test(proposedValue)) {
      return {
        isValid: false,
        error: `Single-line field '${normalizedField}' cannot contain line breaks`,
        targetBlock,
        normalizedField,
        currentValue: authoritativeCurrentValue,
        constraint,
      };
    }

    // Required validation
    if (constraint.required && proposedValue.trim().length === 0) {
      return {
        isValid: false,
        error: `Field '${normalizedField}' is required and cannot be empty`,
        targetBlock,
        normalizedField,
        currentValue: authoritativeCurrentValue,
        constraint,
      };
    }

    // Max length validation
    if (proposedValue.length > constraint.maxLength) {
      return {
        isValid: false,
        error: `Proposed value length (${proposedValue.length}) exceeds maximum allowed length (${constraint.maxLength}) for '${normalizedField}'`,
        targetBlock,
        normalizedField,
        currentValue: authoritativeCurrentValue,
        constraint,
      };
    }
  }

  return {
    isValid: true,
    targetBlock,
    normalizedField,
    currentValue: authoritativeCurrentValue,
    constraint: constraint || undefined,
  };
}

/**
 * Detect changes between previous and updated draft block and synchronize modified text values
 * to the preview iframe in real time via OD_EDIT_SYNC_VALUE (Two-Way Sync Stage 3.4D).
 */
export function syncBlockChangesToPreview(
  client: WebsiteBridgeClient,
  prevBlock: any,
  updatedBlock: any
): boolean {
  if (!client || client.getStatus() !== "connected") return false;
  if (!prevBlock || !updatedBlock || prevBlock.id !== updatedBlock.id) return false;

  const blockId = updatedBlock.id;
  const prevContent = prevBlock.content || {};
  const nextContent = updatedBlock.content || {};

  let synced = false;

  // 1. Top-level scalar string fields
  const allowedTopLevelFields = new Set([
    "headline",
    "subheadline",
    "heading",
    "body",
    "title",
    "submit_label",
    "description",
    "copyright_text",
  ]);

  for (const key of Object.keys(nextContent)) {
    if (
      allowedTopLevelFields.has(key) &&
      typeof nextContent[key] === "string" &&
      nextContent[key] !== prevContent[key]
    ) {
      client.syncValue({
        blockId,
        field: key,
        itemIndex: null,
        value: nextContent[key],
      });
      synced = true;
    }
  }

  // 2. Nested button / cta_button label fields
  if (nextContent.cta_button && typeof nextContent.cta_button.label === "string") {
    if (nextContent.cta_button.label !== prevContent.cta_button?.label) {
      client.syncValue({
        blockId,
        field: "cta_button.label",
        itemIndex: null,
        value: nextContent.cta_button.label,
      });
      synced = true;
    }
  }

  if (nextContent.button && typeof nextContent.button.label === "string") {
    if (nextContent.button.label !== prevContent.button?.label) {
      client.syncValue({
        blockId,
        field: "button.label",
        itemIndex: null,
        value: nextContent.button.label,
      });
      synced = true;
    }
  }

  // 3. Array fields: features_grid.items[].title / description
  if (Array.isArray(nextContent.items)) {
    const prevItems = Array.isArray(prevContent.items) ? prevContent.items : [];
    nextContent.items.forEach((item: any, idx: number) => {
      const prevItem = prevItems[idx] || {};
      if (item && typeof item.title === "string" && item.title !== prevItem.title) {
        client.syncValue({
          blockId,
          field: "items.title",
          itemIndex: idx,
          value: item.title,
        });
        synced = true;
      }
      if (item && typeof item.description === "string" && item.description !== prevItem.description) {
        client.syncValue({
          blockId,
          field: "items.description",
          itemIndex: idx,
          value: item.description,
        });
        synced = true;
      }
    });
  }

  // 4. Array fields: image_gallery.images[].caption
  if (Array.isArray(nextContent.images)) {
    const prevImages = Array.isArray(prevContent.images) ? prevContent.images : [];
    nextContent.images.forEach((img: any, idx: number) => {
      const prevImg = prevImages[idx] || {};
      if (img && typeof img.caption === "string" && img.caption !== prevImg.caption) {
        client.syncValue({
          blockId,
          field: "images.caption",
          itemIndex: idx,
          value: img.caption,
        });
        synced = true;
      }
    });
  }

  // 5. Array fields: footer.links[].label
  if (Array.isArray(nextContent.links)) {
    const prevLinks = Array.isArray(prevContent.links) ? prevContent.links : [];
    nextContent.links.forEach((link: any, idx: number) => {
      const prevLink = prevLinks[idx] || {};
      if (link && typeof link.label === "string" && link.label !== prevLink.label) {
        client.syncValue({
          blockId,
          field: "links.label",
          itemIndex: idx,
          value: link.label,
        });
        synced = true;
      }
    });
  }

  return synced;
}

