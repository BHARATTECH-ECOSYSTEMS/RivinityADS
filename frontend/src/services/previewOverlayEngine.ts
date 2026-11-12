/**
 * previewOverlayEngine.ts — RivinityADS Open Design Phase 3.3A & 3.4A:
 * Bridge Overlay, DOM Interaction & Native Text Inline Editing Engine
 *
 * Implements:
 * 1. Dedicated overlay guide layer (`data-od-edit-guides-layer="true"`).
 * 2. Visual hover outline (`od-guide-hover`) and selected-element outline (`od-guide-selected`).
 * 3. High-precision coordinate tracking via getBoundingClientRect() without mutating element styles or layout.
 * 4. DOM discovery reusing Phase 3.2 semantic metadata and ancestor protection inheritance.
 * 5. Platform protection immunity: ad placements, banners, honeypots are never editable.
 * 6. Responsive reflow tracking on window resize and scroll for desktop, tablet, and mobile previews.
 * 7. Stage 3.4A Native Text Inline Editing Engine:
 *    - Explicit inline editing session with caret placement from click.
 *    - contenteditable="plaintext-only" with fallback, paste and drop interceptors.
 *    - Floating toolbar with character counter, commit (✓), and cancel (✕).
 *    - Keydown controls (single-line Enter, multiline Ctrl/Cmd+Enter, Escape).
 *    - Private MessageChannel integration payload definitions (OD_EDIT_SESSION_START, OD_EDIT_PROPOSE_PATCH, OD_EDIT_CANCEL).
 *    - Blur isolation: iframe blur or parent inspector clicks never cancel or commit active edits.
 * 8. Clean lifecycle management and event listener teardown.
 */

import {
  ExtractedBlockMetadata,
  extractElementMetadata,
  isElementProtected,
  PROTECTED_SELECTORS,
} from "./websiteBridge";
import { buildRelativeDomPath, isTextLeafElement } from "./openDesignPatcher";

export interface OverlayTarget {
  element: HTMLElement;
  metadata: ExtractedBlockMetadata;
  rect: DOMRect;
}

export interface FieldConstraint {
  maxLength?: number;
  multiline: boolean;
  required: boolean;
}

export interface ActiveTextEditSession {
  sessionId: string;
  element: HTMLElement;
  originalText: string;
  metadata: ExtractedBlockMetadata;
  constraint: FieldConstraint;
  patchKind?: "native_field" | "html_fragment";
  targetSelector?: string;
  targetOdId?: string | null;
  _handlers?: {
    onKey: (ev: KeyboardEvent) => void;
    onBeforeInput: (ev: any) => void;
    onInput: () => void;
    onPaste: (ev: ClipboardEvent) => void;
    onDrop: (ev: DragEvent) => void;
  };
}

export interface ProposedPatchPayload {
  sessionId: string;
  blockId: string;
  blockType: string;
  field: string | null;
  itemIndex: number | null;
  proposedValue: string;
  value: string;
  patchKind?: "native_field" | "html_fragment";
  targetSelector?: string;
  targetOdId?: string | null;
  originalValue?: string;
}

export interface CancelPayload {
  sessionId: string;
  blockId: string;
  field: string | null;
  itemIndex: number | null;
  targetSelector?: string;
  targetOdId?: string | null;
}

export interface OverlayEngineOptions {
  rootDocument?: Document;
  onSelect?: (target: OverlayTarget) => void;
  onHover?: (target: OverlayTarget | null) => void;
  onEditSessionStart?: (session: ActiveTextEditSession) => void;
  onEditProposePatch?: (payload: ProposedPatchPayload) => void;
  onEditCancel?: (payload: CancelPayload) => void;
  enablePointerEvents?: boolean;
}

export const SCHEMA_FIELD_CONSTRAINTS: Record<string, Record<string, FieldConstraint>> = {
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

export function normalizeFieldIdentifier(field: string | null): string | null {
  if (!field) return null;
  return field.replace(/\[\d*\]/g, "");
}

export function getFieldConstraints(blockType: string, field: string | null): FieldConstraint | null {
  if (!blockType || !field) return null;
  const blockConstraints = SCHEMA_FIELD_CONSTRAINTS[blockType];
  if (!blockConstraints) return null;

  const normalized = normalizeFieldIdentifier(field);
  if (normalized && blockConstraints[normalized]) {
    return blockConstraints[normalized];
  }

  const prefix = `${blockType}.`;
  if (normalized && normalized.startsWith(prefix)) {
    const stripped = normalized.slice(prefix.length);
    if (blockConstraints[stripped]) {
      return blockConstraints[stripped];
    }
  }

  return null;
}

export function isFieldInlineEditable(blockType: string, field: string | null): boolean {
  return getFieldConstraints(blockType, field) !== null;
}

export class PreviewOverlayEngine {
  private doc: Document;
  private layer: HTMLElement | null = null;
  private hoverBox: HTMLElement | null = null;
  private selectedBox: HTMLElement | null = null;
  private activeHoverEl: HTMLElement | null = null;
  private activeSelectedEl: HTMLElement | null = null;

  // Stage 3.4A toolbar & active edit state
  private inlineToolbar: HTMLElement | null = null;
  private toolbarCounter: HTMLElement | null = null;
  private toolbarBtnSave: HTMLElement | null = null;
  private toolbarBtnCancel: HTMLElement | null = null;
  private activeTextEdit:
    | (ActiveTextEditSession & {
        _handlers?: {
          onKey: (ev: KeyboardEvent) => void;
          onBeforeInput: (ev: any) => void;
          onInput: () => void;
          onPaste: (ev: ClipboardEvent) => void;
          onDrop: (ev: DragEvent) => void;
        };
      })
    | null = null;

  private onSelect?: (target: OverlayTarget) => void;
  private onHover?: (target: OverlayTarget | null) => void;
  private onEditSessionStart?: (session: ActiveTextEditSession) => void;
  private onEditProposePatch?: (payload: ProposedPatchPayload) => void;
  private onEditCancel?: (payload: CancelPayload) => void;
  private enablePointerEvents: boolean;
  private isDestroyed = false;

  // Bound event listeners for cleanup
  private boundPointerOver: (e: Event) => void;
  private boundPointerOut: (e: Event) => void;
  private boundClick: (e: Event) => void;
  private boundDblClick: (e: Event) => void;
  private boundScrollOrResize: (e: Event) => void;

  constructor(options: OverlayEngineOptions = {}) {
    this.doc = options.rootDocument || (typeof document !== "undefined" ? document : (null as any));
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.onEditSessionStart = options.onEditSessionStart;
    this.onEditProposePatch = options.onEditProposePatch;
    this.onEditCancel = options.onEditCancel;
    this.enablePointerEvents = options.enablePointerEvents ?? true;

    this.boundPointerOver = this.handlePointerOver.bind(this);
    this.boundPointerOut = this.handlePointerOut.bind(this);
    this.boundClick = this.handleClick.bind(this);
    this.boundDblClick = this.handleDblClick.bind(this);
    this.boundScrollOrResize = this.handleScrollOrResize.bind(this);

    if (this.doc) {
      this.init();
    }
  }

  /**
   * Initialize overlay guide layer and attach document listeners.
   */
  public init(): void {
    if (!this.doc || this.isDestroyed) return;

    this.layer = this.ensureGuidesLayer();

    if (this.enablePointerEvents && this.doc.addEventListener) {
      this.doc.addEventListener("pointerover", this.boundPointerOver, true);
      this.doc.addEventListener("pointerout", this.boundPointerOut, true);
      this.doc.addEventListener("click", this.boundClick, true);
      this.doc.addEventListener("dblclick", this.boundDblClick, true);

      const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
      if (win && win.addEventListener) {
        win.addEventListener("scroll", this.boundScrollOrResize, { passive: true } as any);
        win.addEventListener("resize", this.boundScrollOrResize, { passive: true } as any);
      }
    }
  }

  /**
   * Retrieve or create the single dedicated overlay guide container.
   * Prevents duplicate overlay layers after reinitialization.
   */
  public ensureGuidesLayer(): HTMLElement {
    if (!this.doc || !this.doc.body) {
      throw new Error("PreviewOverlayEngine: document or document.body is not available");
    }

    const existing = this.doc.querySelector('[data-od-edit-guides-layer="true"]') as HTMLElement | null;
    if (existing) {
      return existing;
    }

    const layer = this.doc.createElement("div");
    layer.setAttribute("data-od-edit-guides-layer", "true");
    layer.setAttribute("aria-hidden", "true");
    layer.style.position = "fixed";
    layer.style.top = "0";
    layer.style.left = "0";
    layer.style.width = "100%";
    layer.style.height = "100%";
    layer.style.pointerEvents = "none";
    layer.style.zIndex = "999999";
    layer.style.overflow = "visible";

    this.doc.body.appendChild(layer);
    return layer;
  }

  public getGuidesLayer(): HTMLElement | null {
    return this.layer;
  }

  /**
   * Retrieve or create the single dedicated floating editing toolbar.
   */
  public ensureInlineToolbar(): HTMLElement | null {
    if (this.inlineToolbar && this.inlineToolbar.isConnected) {
      return this.inlineToolbar;
    }
    const layer = this.ensureGuidesLayer();
    if (!layer) return null;

    const existing = layer.querySelector('[data-od-inline-toolbar="true"]') as HTMLElement | null;
    if (existing) {
      this.inlineToolbar = existing;
      this.toolbarCounter = existing.querySelector(".od-toolbar-counter") as HTMLElement | null;
      this.toolbarBtnSave = existing.querySelector(".od-toolbar-save") as HTMLElement | null;
      this.toolbarBtnCancel = existing.querySelector(".od-toolbar-cancel") as HTMLElement | null;
      return existing;
    }

    const tb = this.doc.createElement("div");
    tb.className = "od-inline-toolbar";
    tb.setAttribute("data-od-inline-toolbar", "true");
    tb.setAttribute("aria-hidden", "true");
    tb.style.position = "absolute";
    tb.style.boxSizing = "border-box";
    tb.style.zIndex = "1000000";
    tb.style.display = "none";
    tb.style.alignItems = "center";
    tb.style.gap = "6px";
    tb.style.backgroundColor = "#0f172a";
    tb.style.border = "1px solid #334155";
    tb.style.borderRadius = "4px";
    tb.style.padding = "3px 6px";
    tb.style.boxShadow = "0 4px 14px rgba(0, 0, 0, 0.35)";
    tb.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    tb.style.fontSize = "11px";
    tb.style.lineHeight = "1";
    tb.style.pointerEvents = "auto";
    tb.style.userSelect = "none";

    const counter = this.doc.createElement("span");
    counter.className = "od-toolbar-counter";
    counter.style.color = "#94a3b8";
    counter.style.fontSize = "10px";
    counter.style.fontFamily = "monospace";
    counter.style.padding = "0 4px";
    tb.appendChild(counter);

    const btnSave = this.doc.createElement("button");
    btnSave.type = "button";
    btnSave.className = "od-toolbar-btn od-toolbar-save";
    btnSave.setAttribute("title", "Apply edit (Enter)");
    btnSave.textContent = "✓";
    btnSave.style.background = "rgba(56, 189, 248, 0.15)";
    btnSave.style.border = "1px solid rgba(56, 189, 248, 0.4)";
    btnSave.style.borderRadius = "3px";
    btnSave.style.color = "#38bdf8";
    btnSave.style.cursor = "pointer";
    btnSave.style.padding = "2px 6px";
    btnSave.style.fontSize = "11px";
    btnSave.style.fontWeight = "700";
    btnSave.style.lineHeight = "1";

    const btnCancel = this.doc.createElement("button");
    btnCancel.type = "button";
    btnCancel.className = "od-toolbar-btn od-toolbar-cancel";
    btnCancel.setAttribute("title", "Cancel edit (Esc)");
    btnCancel.textContent = "✕";
    btnCancel.style.background = "transparent";
    btnCancel.style.border = "1px solid rgba(148, 163, 184, 0.3)";
    btnCancel.style.borderRadius = "3px";
    btnCancel.style.color = "#cbd5e1";
    btnCancel.style.cursor = "pointer";
    btnCancel.style.padding = "2px 6px";
    btnCancel.style.fontSize = "11px";
    btnCancel.style.fontWeight = "700";
    btnCancel.style.lineHeight = "1";

    btnSave.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.finishActiveTextEdit(true);
    });

    btnCancel.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.finishActiveTextEdit(false);
    });

    tb.appendChild(btnSave);
    tb.appendChild(btnCancel);
    layer.appendChild(tb);

    this.inlineToolbar = tb;
    this.toolbarCounter = counter;
    this.toolbarBtnSave = btnSave;
    this.toolbarBtnCancel = btnCancel;

    return tb;
  }

  public getToolbarElement(): HTMLElement | null {
    return this.inlineToolbar;
  }

  public renderInlineToolbar(el: HTMLElement): void {
    const toolbar = this.ensureInlineToolbar();
    if (!toolbar || !el || !el.isConnected) {
      this.hideInlineToolbar();
      return;
    }

    const rect = el.getBoundingClientRect();
    toolbar.style.display = "flex";

    const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
    const winWidth = win?.innerWidth || this.doc.documentElement?.clientWidth || 800;
    const winHeight = win?.innerHeight || this.doc.documentElement?.clientHeight || 600;

    const tbHeight = 28;
    const tbWidth = 140;

    const topPos = rect.top >= tbHeight + 6 ? rect.top - tbHeight - 4 : rect.bottom + 4;
    const clampedTop = Math.max(4, Math.min(topPos, winHeight - tbHeight - 4));
    toolbar.style.top = `${clampedTop}px`;

    const leftPos = Math.max(8, Math.min(rect.left, winWidth - tbWidth - 8));
    toolbar.style.left = `${leftPos}px`;

    this.updateCounterAndToolbar();
  }

  public updateCounterAndToolbar(): void {
    if (!this.activeTextEdit || !this.toolbarCounter) return;
    const currentText = this.activeTextEdit.element.textContent || "";
    const len = currentText.length;
    if (this.activeTextEdit.constraint && this.activeTextEdit.constraint.maxLength) {
      const max = this.activeTextEdit.constraint.maxLength;
      this.toolbarCounter.textContent = `${len} / ${max}`;
      this.toolbarCounter.style.display = "inline";
      if (len > max) {
        this.toolbarCounter.style.color = "#ef4444";
        this.toolbarCounter.style.fontWeight = "700";
      } else {
        this.toolbarCounter.style.color = "#94a3b8";
        this.toolbarCounter.style.fontWeight = "500";
      }
    } else {
      this.toolbarCounter.style.display = "none";
    }
  }

  public hideInlineToolbar(): void {
    if (this.inlineToolbar) {
      this.inlineToolbar.style.display = "none";
    }
  }

  /**
   * Find nearest valid semantic element for interaction.
   * Returns null if target is inside overlay, toolbar, platform notice, or has no valid metadata.
   */
  public findSemanticTarget(el: Element | null): HTMLElement | null {
    if (!el || typeof el.closest !== "function") return null;

    // 1. Overlay & Toolbar Self-Exclusion: Never target the guide layer or anything inside it
    if (
      el.hasAttribute("data-od-edit-guides-layer") ||
      el.closest('[data-od-edit-guides-layer="true"]') ||
      el.hasAttribute("data-od-inline-toolbar") ||
      el.closest('[data-od-inline-toolbar="true"]')
    ) {
      return null;
    }

    // 2. Protected Platform Elements check
    const isProtected = isElementProtected(el);

    // If element is a platform banner, consent banner, or honeypot, strictly ignore
    if (el.closest('.preview-banner, #rivinity-consent-banner, [name="hp_comment"], [data-od-edit-bridge]')) {
      return null;
    }

    // Ad placement blocks are protected: allow block-level discovery only (read-only)
    if (el.closest('.block-ad-placement, .rivinity-ad-placeholder, .rivinity-live-ad-container, .rivinity-ad-slot')) {
      const adBlock = el.closest('[data-od-block-id]') as HTMLElement | null;
      if (adBlock) {
        return adBlock;
      }
      return null;
    }

    // 3. Normal elements: Find specific field target or nearest block container
    let candidate: HTMLElement | null = null;
    if (el.hasAttribute("data-od-field") && el.hasAttribute("data-od-block-id")) {
      candidate = el as HTMLElement;
    } else {
      const nearestField = el.closest('[data-od-field]') as HTMLElement | null;
      if (nearestField && nearestField.hasAttribute("data-od-block-id")) {
        candidate = nearestField;
      } else {
        candidate = el.closest('[data-od-block-id]') as HTMLElement | null;
      }
    }

    if (!candidate) return null;

    // Ensure candidate has valid metadata
    const meta = extractElementMetadata(candidate);
    if (!meta) return null;

    return candidate;
  }

  /**
   * Build human-readable badge label from extracted metadata.
   */
  private formatBadgeLabel(meta: ExtractedBlockMetadata): string {
    if (meta.isProtected) {
      return "🔒 Protected";
    }

    const typeLabel = meta.blockType
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());

    if (!meta.field) {
      return typeLabel;
    }

    const fieldLabel = meta.field.replace(/_/g, " ");
    if (meta.itemIndex !== null) {
      return `${typeLabel} • ${fieldLabel}[${meta.itemIndex}]`;
    }

    return `${typeLabel} • ${fieldLabel}`;
  }

  /**
   * Display or update hover outline over target element.
   */
  public setHoverTarget(el: HTMLElement | null): boolean {
    if (this.isDestroyed || !this.layer) return false;

    if (!el) {
      this.clearHover();
      return true;
    }

    const semanticEl = this.findSemanticTarget(el);
    if (!semanticEl) {
      this.clearHover();
      return false;
    }

    const metadata = extractElementMetadata(semanticEl);
    if (!metadata) {
      this.clearHover();
      return false;
    }

    this.activeHoverEl = semanticEl;
    const rect = semanticEl.getBoundingClientRect();

    if (rect.width <= 0 && rect.height <= 0) {
      this.clearHover();
      return false;
    }

    if (!this.hoverBox) {
      this.hoverBox = this.doc.createElement("div");
      this.hoverBox.className = "od-guide-box od-guide-hover";
      this.hoverBox.setAttribute("aria-hidden", "true");
      this.hoverBox.style.position = "absolute";
      this.hoverBox.style.boxSizing = "border-box";
      this.hoverBox.style.pointerEvents = "none";
      this.hoverBox.style.borderRadius = "4px";
      this.hoverBox.style.zIndex = "1000";
      this.layer.appendChild(this.hoverBox);
    }

    const isProtected = metadata.isProtected;
    this.hoverBox.style.display = "block";
    this.hoverBox.style.top = `${rect.top}px`;
    this.hoverBox.style.left = `${rect.left}px`;
    this.hoverBox.style.width = `${rect.width}px`;
    this.hoverBox.style.height = `${rect.height}px`;
    this.hoverBox.style.border = isProtected
      ? "1.5px dashed rgba(245, 158, 11, 0.9)"
      : "1.5px dashed rgba(59, 130, 246, 0.9)";
    this.hoverBox.style.backgroundColor = isProtected
      ? "rgba(245, 158, 11, 0.06)"
      : "rgba(59, 130, 246, 0.06)";

    const badgeText = this.formatBadgeLabel(metadata);
    let badge = this.hoverBox.querySelector(".od-guide-badge") as HTMLElement | null;
    if (!badge) {
      badge = this.doc.createElement("div");
      badge.className = "od-guide-badge";
      badge.style.position = "absolute";
      badge.style.fontSize = "10px";
      badge.style.fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
      badge.style.fontWeight = "600";
      badge.style.padding = "2px 6px";
      badge.style.borderRadius = "3px";
      badge.style.whiteSpace = "nowrap";
      badge.style.pointerEvents = "none";
      badge.style.lineHeight = "1.2";
      badge.style.color = "#ffffff";
      this.hoverBox.appendChild(badge);
    }

    badge.textContent = badgeText;
    badge.style.backgroundColor = isProtected ? "#d97706" : "#2563eb";

    if (rect.top < 24) {
      badge.style.top = "2px";
      badge.style.left = "2px";
    } else {
      badge.style.top = "-20px";
      badge.style.left = "0";
    }

    if (this.onHover) {
      this.onHover({
        element: semanticEl,
        metadata,
        rect,
      });
    }

    return true;
  }

  /**
   * Display or update selection outline around target element.
   */
  public setSelectedTarget(el: HTMLElement | null): boolean {
    if (this.isDestroyed || !this.layer) return false;

    if (!el) {
      this.clearSelected();
      return true;
    }

    const semanticEl = this.findSemanticTarget(el);
    if (!semanticEl) {
      this.clearSelected();
      return false;
    }

    const metadata = extractElementMetadata(semanticEl);
    if (!metadata) {
      this.clearSelected();
      return false;
    }

    this.activeSelectedEl = semanticEl;
    const rect = semanticEl.getBoundingClientRect();

    if (rect.width <= 0 && rect.height <= 0) {
      this.clearSelected();
      return false;
    }

    if (!this.selectedBox) {
      this.selectedBox = this.doc.createElement("div");
      this.selectedBox.className = "od-guide-box od-guide-selected";
      this.selectedBox.setAttribute("aria-hidden", "true");
      this.selectedBox.style.position = "absolute";
      this.selectedBox.style.boxSizing = "border-box";
      this.selectedBox.style.pointerEvents = "none";
      this.selectedBox.style.borderRadius = "4px";
      this.selectedBox.style.zIndex = "1001";
      this.selectedBox.style.boxShadow =
        "0 0 0 1px rgba(255, 255, 255, 0.8), 0 4px 12px rgba(37, 99, 235, 0.25)";

      ["top-left", "top-right", "bottom-left", "bottom-right"].forEach((pos) => {
        const handle = this.doc.createElement("div");
        handle.className = `od-guide-handle ${pos}`;
        handle.style.position = "absolute";
        handle.style.width = "6px";
        handle.style.height = "6px";
        handle.style.backgroundColor = "#ffffff";
        handle.style.border = "1.5px solid #2563eb";
        handle.style.borderRadius = "1px";

        if (pos.includes("top")) handle.style.top = "-3px";
        if (pos.includes("bottom")) handle.style.bottom = "-3px";
        if (pos.includes("left")) handle.style.left = "-3px";
        if (pos.includes("right")) handle.style.right = "-3px";

        this.selectedBox!.appendChild(handle);
      });

      this.layer.appendChild(this.selectedBox);
    }

    const isProtected = metadata.isProtected;
    this.selectedBox.style.display = "block";
    this.selectedBox.style.top = `${rect.top}px`;
    this.selectedBox.style.left = `${rect.left}px`;
    this.selectedBox.style.width = `${rect.width}px`;
    this.selectedBox.style.height = `${rect.height}px`;
    this.selectedBox.style.border = isProtected
      ? "2px solid #d97706"
      : "2px solid #2563eb";
    this.selectedBox.style.backgroundColor = isProtected
      ? "rgba(245, 158, 11, 0.08)"
      : "rgba(37, 99, 235, 0.08)";

    const badgeText = this.formatBadgeLabel(metadata);
    let badge = this.selectedBox.querySelector(".od-guide-badge") as HTMLElement | null;
    if (!badge) {
      badge = this.doc.createElement("div");
      badge.className = "od-guide-badge";
      badge.style.position = "absolute";
      badge.style.fontSize = "10px";
      badge.style.fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
      badge.style.fontWeight = "700";
      badge.style.padding = "2px 6px";
      badge.style.borderRadius = "3px";
      badge.style.whiteSpace = "nowrap";
      badge.style.pointerEvents = "none";
      badge.style.lineHeight = "1.2";
      badge.style.color = "#ffffff";
      this.selectedBox.appendChild(badge);
    }

    badge.textContent = badgeText;
    badge.style.backgroundColor = isProtected ? "#d97706" : "#2563eb";

    if (rect.top < 24) {
      badge.style.top = "2px";
      badge.style.left = "2px";
    } else {
      badge.style.top = "-22px";
      badge.style.left = "-1px";
    }

    if (this.onSelect) {
      this.onSelect({
        element: semanticEl,
        metadata,
        rect,
      });
    }

    return true;
  }

  public clearHover(): void {
    this.activeHoverEl = null;
    if (this.hoverBox) {
      this.hoverBox.style.display = "none";
    }
    if (this.onHover) {
      this.onHover(null);
    }
  }

  public clearSelected(): void {
    this.activeSelectedEl = null;
    if (this.selectedBox) {
      this.selectedBox.style.display = "none";
    }
  }

  public clearAll(): void {
    this.clearHover();
    this.clearSelected();
  }

  /**
   * Programmatically select a block by its block ID and optional field target.
   */
  public selectByBlockId(
    blockId: string,
    options?: { field?: string | null; itemIndex?: number | null; scrollIntoView?: boolean }
  ): boolean {
    if (this.activeTextEdit) {
      const isSame =
        this.activeTextEdit.metadata.blockId === blockId &&
        (options?.field || null) === (this.activeTextEdit.metadata.field || null) &&
        (options?.itemIndex !== undefined ? options.itemIndex : null) ===
          (this.activeTextEdit.metadata.itemIndex !== undefined ? this.activeTextEdit.metadata.itemIndex : null);
      if (!isSame) {
        this.finishActiveTextEdit(true);
      }
    }

    if (!this.doc) return false;
    const blockEl = this.doc.querySelector(`[data-od-block-id="${blockId}"]`) as HTMLElement | null;
    if (!blockEl) return false;

    let targetEl: HTMLElement = blockEl;
    if (options?.field) {
      let fieldSel = `[data-od-field="${options.field}"]`;
      if (options.itemIndex !== undefined && options.itemIndex !== null) {
        fieldSel += `[data-od-item-index="${options.itemIndex}"]`;
      }
      const fieldEl = blockEl.querySelector(fieldSel) as HTMLElement | null;
      if (fieldEl) targetEl = fieldEl;
    }

    const ok = this.setSelectedTarget(targetEl);
    if (ok && options?.scrollIntoView && typeof targetEl.scrollIntoView === "function") {
      try {
        targetEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch {
        targetEl.scrollIntoView();
      }
    }
    return ok;
  }

  /**
   * Position caret using caretRangeFromPoint / caretPositionFromPoint or safe fallback.
   */
  public placeCaretFromClick(
    clickEvent?: MouseEvent | { clientX: number; clientY: number },
    el?: HTMLElement
  ): void {
    const target = el || this.activeTextEdit?.element;
    if (!target) return;
    const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
    if (!win || typeof win.getSelection !== "function") return;

    let range: Range | null = null;
    const clientX = clickEvent && typeof clickEvent.clientX === "number" ? clickEvent.clientX : null;
    const clientY = clickEvent && typeof clickEvent.clientY === "number" ? clickEvent.clientY : null;

    if (clientX !== null && clientY !== null) {
      if ((this.doc as any).caretRangeFromPoint) {
        try {
          range = (this.doc as any).caretRangeFromPoint(clientX, clientY);
        } catch (_) {}
      } else if ((this.doc as any).caretPositionFromPoint) {
        try {
          const pos = (this.doc as any).caretPositionFromPoint(clientX, clientY);
          if (pos && pos.offsetNode && this.doc.createRange) {
            range = this.doc.createRange();
            range.setStart(pos.offsetNode, pos.offset);
            range.collapse(true);
          }
        } catch (_) {}
      }
    }

    if (!range && this.doc.createRange) {
      try {
        range = this.doc.createRange();
        range.selectNodeContents(target);
        range.collapse(false);
      } catch (_) {}
    }

    if (range) {
      try {
        const sel = win.getSelection();
        if (sel) {
          sel.removeAllRanges();
          sel.addRange(range);
        }
      } catch (_) {}
    }
  }

  /**
   * Start an inline text editing session on a supported editable element.
   */
  public startTextEdit(
    el: HTMLElement,
    clickEvent?: MouseEvent | { clientX: number; clientY: number },
    options?: { allowCustomSection?: boolean }
  ): boolean {
    if (this.isDestroyed || !el || !el.isConnected) return false;
    if (isElementProtected(el)) return false;

    const meta = extractElementMetadata(el);
    if (!meta || meta.isProtected) return false;

    const isCustomSection = meta.blockType === "open_design_section";
    if (isCustomSection) {
      if (!options?.allowCustomSection || !isTextLeafElement(el)) {
        return false;
      }
    } else {
      if (!meta.isEditable) return false;
    }

    if (
      meta.blockType === "ad_placement" ||
      (typeof el.closest === "function" &&
        el.closest(
          '.block-ad-placement, .rivinity-ad-placeholder, .rivinity-live-ad-container, .rivinity-ad-slot, [data-od-block-type="ad_placement"]'
        ))
    ) {
      return false;
    }

    const disallowedFields = [
      "cta_button.target_url",
      "background_image_url",
      "alignment",
      "items.icon",
      "images.image_url",
      "images.alt_text",
      "fields",
      "button.target_url",
      "links.target_url",
    ];
    if (meta.field && disallowedFields.includes(meta.field)) {
      return false;
    }

    let constraint: FieldConstraint | null = null;
    if (isCustomSection) {
      const tag = el.tagName.toLowerCase();
      const isHeading = tag.startsWith("h") && tag.length === 2;
      constraint = { maxLength: 5000, multiline: !isHeading, required: false };
    } else {
      constraint = getFieldConstraints(meta.blockType, meta.field);
    }
    if (!constraint) return false;

    if (this.activeTextEdit && this.activeTextEdit.element === el) {
      this.placeCaretFromClick(clickEvent, el);
      return true;
    }

    if (this.activeTextEdit) {
      this.finishActiveTextEdit(true);
    }

    const originalText = el.textContent || "";
    const sessionId = `edit_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;

    let targetSelector: string | undefined = undefined;
    let targetOdId: string | null = null;
    if (isCustomSection) {
      const secContainer = (el.closest('[data-od-block-id][data-od-block-type="open_design_section"]') ||
        el.closest('[data-od-block-id]')) as HTMLElement;
      if (secContainer) {
        targetSelector = buildRelativeDomPath(el, secContainer);
      }
      targetOdId = el.getAttribute("data-od-id") || null;
    }

    try {
      el.setAttribute("contenteditable", "plaintext-only");
      if ((el as any).contentEditable !== "plaintext-only") {
        el.setAttribute("contenteditable", "true");
      }
    } catch (_) {
      el.setAttribute("contenteditable", "true");
    }

    el.setAttribute("data-od-editing", "true");
    el.style.outline = "2px solid #2563eb";
    el.style.outlineOffset = "2px";
    el.style.cursor = "text";

    try {
      if (typeof el.focus === "function") el.focus();
    } catch (_) {}
    this.placeCaretFromClick(clickEvent, el);

    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Enter") {
        if (!constraint.multiline) {
          ev.preventDefault();
          if (ev.shiftKey) {
            return;
          }
          this.finishActiveTextEdit(true);
        } else if (ev.ctrlKey || ev.metaKey) {
          ev.preventDefault();
          this.finishActiveTextEdit(true);
        }
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        this.finishActiveTextEdit(false);
      } else if (
        (ev.ctrlKey || ev.metaKey) &&
        (ev.key === "b" || ev.key === "i" || ev.key === "u" || ev.key === "B" || ev.key === "I" || ev.key === "U")
      ) {
        ev.preventDefault();
      } else if (ev.key && ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        if (constraint.maxLength) {
          const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
          const sel = win?.getSelection ? win.getSelection() : null;
          const hasSelection = sel && sel.toString().length > 0;
          if (!hasSelection && (el.textContent || "").length >= constraint.maxLength) {
            ev.preventDefault();
          }
        }
      }
    };

    const onBeforeInput = (ev: any) => {
      if (!ev) return;
      if (
        ev.inputType &&
        (ev.inputType.indexOf("format") === 0 ||
          ev.inputType === "insertLink" ||
          ev.inputType === "insertOrderedList" ||
          ev.inputType === "insertUnorderedList" ||
          ev.inputType === "insertHorizontalRule")
      ) {
        ev.preventDefault();
        return;
      }
      if (!constraint.multiline && (ev.inputType === "insertParagraph" || ev.inputType === "insertLineBreak")) {
        ev.preventDefault();
        return;
      }
    };

    const onInput = () => {
      if (!this.activeTextEdit || this.activeTextEdit.element !== el) return;
      if (el.children && el.children.length > 0 && !constraint.multiline) {
        el.textContent = el.textContent || "";
      }
      this.updateCounterAndToolbar();
      if (this.activeSelectedEl === el) {
        this.setSelectedTarget(el);
      }
      this.renderInlineToolbar(el);
    };

    const onPaste = (ev: ClipboardEvent) => {
      ev.preventDefault();
      let text = "";
      if (ev.clipboardData && ev.clipboardData.getData) {
        text = ev.clipboardData.getData("text/plain");
      } else {
        const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
        if ((win as any)?.clipboardData && (win as any).clipboardData.getData) {
          text = (win as any).clipboardData.getData("Text");
        }
      }
      if (!text) return;
      if (!constraint.multiline) {
        text = text.replace(/[\r\n]+/g, " ");
      }
      if (constraint.maxLength) {
        const currentLen = (el.textContent || "").length;
        const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
        const sel = win?.getSelection ? win.getSelection() : null;
        const selLen = sel && sel.toString() ? sel.toString().length : 0;
        const remaining = Math.max(0, constraint.maxLength - (currentLen - selLen));
        if (text.length > remaining) {
          text = text.slice(0, remaining);
        }
      }
      if (!text) return;

      if (this.doc.queryCommandSupported && this.doc.queryCommandSupported("insertText")) {
        this.doc.execCommand("insertText", false, text);
      } else {
        const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
        const sel = win?.getSelection ? win.getSelection() : null;
        const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
        if (range && sel) {
          range.deleteContents();
          const node = this.doc.createTextNode(text);
          range.insertNode(node);
          range.setStartAfter(node);
          range.collapse(true);
          sel.removeAllRanges();
          sel.addRange(range);
        } else {
          el.textContent = (el.textContent || "") + text;
        }
      }
      this.updateCounterAndToolbar();
    };

    const onDrop = (ev: DragEvent) => {
      ev.preventDefault();
      let text = ev.dataTransfer ? ev.dataTransfer.getData("text/plain") : "";
      if (!text) return;
      if (!constraint.multiline) {
        text = text.replace(/[\r\n]+/g, " ");
      }
      if (constraint.maxLength) {
        const currentLen = (el.textContent || "").length;
        const remaining = Math.max(0, constraint.maxLength - currentLen);
        if (text.length > remaining) {
          text = text.slice(0, remaining);
        }
      }
      if (!text) return;
      if (this.doc.queryCommandSupported && this.doc.queryCommandSupported("insertText")) {
        this.doc.execCommand("insertText", false, text);
      } else {
        el.textContent = (el.textContent || "") + text;
      }
      this.updateCounterAndToolbar();
    };

    this.activeTextEdit = {
      sessionId,
      element: el,
      originalText,
      metadata: meta,
      constraint,
      patchKind: isCustomSection ? "html_fragment" : "native_field",
      targetSelector,
      targetOdId,
      _handlers: {
        onKey,
        onBeforeInput,
        onInput,
        onPaste,
        onDrop,
      },
    };

    el.addEventListener("keydown", onKey as any);
    el.addEventListener("beforeinput", onBeforeInput as any);
    el.addEventListener("input", onInput as any);
    el.addEventListener("paste", onPaste as any);
    el.addEventListener("drop", onDrop as any);

    this.renderInlineToolbar(el);

    if (this.onEditSessionStart) {
      this.onEditSessionStart(this.activeTextEdit);
    }

    return true;
  }

  /**
   * Commit or cancel the active text editing session.
   */
  public finishActiveTextEdit(commit: boolean): boolean {
    if (!this.activeTextEdit) return false;

    const session = this.activeTextEdit;
    this.activeTextEdit = null;

    const el = session.element;
    const handlers = session._handlers;

    el.removeAttribute("contenteditable");
    el.removeAttribute("data-od-editing");
    el.style.outline = "";
    el.style.outlineOffset = "";
    el.style.cursor = "";

    if (handlers) {
      el.removeEventListener("keydown", handlers.onKey as any);
      el.removeEventListener("beforeinput", handlers.onBeforeInput as any);
      el.removeEventListener("input", handlers.onInput as any);
      el.removeEventListener("paste", handlers.onPaste as any);
      el.removeEventListener("drop", handlers.onDrop as any);
    }

    this.hideInlineToolbar();

    if (!commit) {
      el.textContent = session.originalText;
      if (this.onEditCancel) {
        this.onEditCancel({
          sessionId: session.sessionId,
          blockId: session.metadata.blockId,
          field: session.metadata.field,
          itemIndex: session.metadata.itemIndex,
          targetSelector: session.targetSelector,
          targetOdId: session.targetOdId,
        });
      }
    } else {
      const rawText = el.textContent || "";
      const finalVal = rawText;
      if (finalVal !== session.originalText) {
        if (this.onEditProposePatch) {
          this.onEditProposePatch({
            sessionId: session.sessionId,
            blockId: session.metadata.blockId,
            blockType: session.metadata.blockType,
            field: session.metadata.field,
            itemIndex: session.metadata.itemIndex,
            proposedValue: finalVal,
            value: finalVal,
            patchKind: session.patchKind || "native_field",
            targetSelector: session.targetSelector,
            targetOdId: session.targetOdId,
            originalValue: session.originalText,
          });
        }
      }
    }

    if (this.activeSelectedEl && this.activeSelectedEl.isConnected) {
      this.setSelectedTarget(this.activeSelectedEl);
    }

    return true;
  }

  /**
   * Start a localized inline text editing session on a text leaf inside an open_design_section.
   */
  public startLocalizedSectionEdit(
    el: HTMLElement,
    clickEvent?: MouseEvent | { clientX: number; clientY: number }
  ): boolean {
    return this.startTextEdit(el, clickEvent, { allowCustomSection: true });
  }

  public getActiveTextEdit(): ActiveTextEditSession | null {
    return this.activeTextEdit;
  }

  /**
   * Recalculate bounding boxes for active hover and selection targets on scroll or resize.
   */
  public updatePositions(): void {
    if (this.isDestroyed) return;

    if (this.activeHoverEl) {
      if (this.activeHoverEl.isConnected) {
        this.setHoverTarget(this.activeHoverEl);
      } else {
        this.clearHover();
      }
    }

    if (this.activeSelectedEl) {
      if (this.activeSelectedEl.isConnected) {
        this.setSelectedTarget(this.activeSelectedEl);
      } else {
        this.clearSelected();
      }
    }

    if (this.activeTextEdit) {
      if (this.activeTextEdit.element && this.activeTextEdit.element.isConnected) {
        this.renderInlineToolbar(this.activeTextEdit.element);
      } else {
        this.finishActiveTextEdit(false);
      }
    }
  }

  public getActiveHoverTarget(): OverlayTarget | null {
    if (!this.activeHoverEl || !this.activeHoverEl.isConnected) return null;
    const metadata = extractElementMetadata(this.activeHoverEl);
    if (!metadata) return null;
    return {
      element: this.activeHoverEl,
      metadata,
      rect: this.activeHoverEl.getBoundingClientRect(),
    };
  }

  public getActiveSelectedTarget(): OverlayTarget | null {
    if (!this.activeSelectedEl || !this.activeSelectedEl.isConnected) return null;
    const metadata = extractElementMetadata(this.activeSelectedEl);
    if (!metadata) return null;
    return {
      element: this.activeSelectedEl,
      metadata,
      rect: this.activeSelectedEl.getBoundingClientRect(),
    };
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Event Handlers
  // ─────────────────────────────────────────────────────────────────────────────

  private handlePointerOver(e: Event): void {
    const target = (e.target || null) as Element | null;
    if (!target) return;

    const semantic = this.findSemanticTarget(target);
    if (semantic) {
      this.setHoverTarget(semantic);
    }
  }

  private handlePointerOut(e: Event): void {
    const target = (e.target || null) as Element | null;
    if (!target) return;

    if (this.activeHoverEl && (target === this.activeHoverEl || this.activeHoverEl.contains(target))) {
      const related = (e as any).relatedTarget as Element | null;
      if (!related || !this.activeHoverEl.contains(related)) {
        this.clearHover();
      }
    }
  }

  private handleClick(e: Event): void {
    const target = (e.target || null) as Element | null;
    if (!target) return;

    if (
      typeof (target as any).closest === "function" &&
      (target as any).closest('[data-od-inline-toolbar="true"]')
    ) {
      return;
    }

    const semantic = this.findSemanticTarget(target);
    if (semantic) {
      this.setSelectedTarget(semantic);
      const meta = extractElementMetadata(semantic);

      if (meta && meta.isEditable && !meta.isProtected && isFieldInlineEditable(meta.blockType, meta.field)) {
        if (this.activeTextEdit && this.activeTextEdit.element === semantic) {
          this.placeCaretFromClick(e as MouseEvent, semantic);
        } else {
          this.startTextEdit(semantic, e as MouseEvent);
        }
      } else if (meta && meta.blockType === "open_design_section" && target && isTextLeafElement(target) && !isElementProtected(target)) {
        if (this.activeTextEdit && this.activeTextEdit.element === target) {
          this.placeCaretFromClick(e as MouseEvent, target as HTMLElement);
        } else {
          this.startLocalizedSectionEdit(target as HTMLElement, e as MouseEvent);
        }
      } else {
        if (this.activeTextEdit) {
          this.finishActiveTextEdit(true);
        }
      }
    } else {
      if (
        typeof (target as any).closest === "function" &&
        !(target as any).closest('[data-od-edit-guides-layer="true"]')
      ) {
        if (this.activeTextEdit) {
          this.finishActiveTextEdit(true);
        }
        this.clearSelected();
      }
    }
  }

  private handleDblClick(e: Event): void {
    const target = (e.target || null) as Element | null;
    if (!target) return;

    if (
      typeof (target as any).closest === "function" &&
      (target as any).closest('[data-od-inline-toolbar="true"]')
    ) {
      return;
    }

    const semantic = this.findSemanticTarget(target);
    if (semantic) {
      const meta = extractElementMetadata(semantic);
      if (meta && meta.isEditable && !meta.isProtected && isFieldInlineEditable(meta.blockType, meta.field)) {
        if (!this.activeTextEdit || this.activeTextEdit.element !== semantic) {
          this.startTextEdit(semantic, e as MouseEvent);
        }
      } else if (meta && meta.blockType === "open_design_section" && target && isTextLeafElement(target) && !isElementProtected(target)) {
        if (!this.activeTextEdit || this.activeTextEdit.element !== target) {
          this.startLocalizedSectionEdit(target as HTMLElement, e as MouseEvent);
        }
      }
    }
  }

  private handleScrollOrResize(): void {
    this.updatePositions();
  }

  /**
   * Apply confirmed text edit received from parent bridge (OD_EDIT_APPLY_CONFIRMED).
   */
  public applyConfirmedValue(
    blockId: string,
    field: string | null,
    itemIndex: number | null | undefined,
    value: string,
    targetSelector?: string,
    targetOdId?: string | null
  ): boolean {
    if (!blockId || !this.doc) return false;
    const blockEl = this.doc.querySelector(`[data-od-block-id="${blockId}"]`);
    if (!blockEl) return false;
    let targetEl: Element = blockEl;
    if (field === "html_content" || blockEl.getAttribute("data-od-block-type") === "open_design_section") {
      let found: Element | null = null;
      if (targetOdId) {
        found = blockEl.querySelector(`[data-od-id="${targetOdId}"]`);
      }
      if (!found && targetSelector) {
        try {
          found = blockEl.querySelector(targetSelector);
        } catch (_) {}
      }
      if (found) targetEl = found;
    } else if (field) {
      let fieldSel = `[data-od-field="${field}"]`;
      if (itemIndex !== undefined && itemIndex !== null) {
        fieldSel += `[data-od-item-index="${itemIndex}"]`;
      }
      const fieldEl = blockEl.querySelector(fieldSel);
      if (fieldEl) targetEl = fieldEl;
    }
    if (typeof value === "string") {
      targetEl.textContent = value;
      if (this.activeTextEdit && this.activeTextEdit.element === targetEl) {
        this.activeTextEdit.originalText = value;
        this.updateCounterAndToolbar();
      }
    }
    this.updatePositions();
    return true;
  }

  /**
   * Roll back rejected text edit received from parent bridge (OD_EDIT_REJECTED).
   */
  public rollbackValue(
    blockId: string,
    field: string | null,
    itemIndex: number | null | undefined,
    value: string,
    targetSelector?: string,
    targetOdId?: string | null
  ): boolean {
    if (!blockId || !this.doc) return false;
    const blockEl = this.doc.querySelector(`[data-od-block-id="${blockId}"]`);
    if (!blockEl) return false;
    let targetEl: Element = blockEl;
    if (field === "html_content" || blockEl.getAttribute("data-od-block-type") === "open_design_section") {
      let found: Element | null = null;
      if (targetOdId) {
        found = blockEl.querySelector(`[data-od-id="${targetOdId}"]`);
      }
      if (!found && targetSelector) {
        try {
          found = blockEl.querySelector(targetSelector);
        } catch (_) {}
      }
      if (found) targetEl = found;
    } else if (field) {
      let fieldSel = `[data-od-field="${field}"]`;
      if (itemIndex !== undefined && itemIndex !== null) {
        fieldSel += `[data-od-item-index="${itemIndex}"]`;
      }
      const fieldEl = blockEl.querySelector(fieldSel);
      if (fieldEl) targetEl = fieldEl;
    }
    if (typeof value === "string") {
      targetEl.textContent = value;
      if (this.activeTextEdit && this.activeTextEdit.element === targetEl) {
        this.activeTextEdit.originalText = value;
        this.updateCounterAndToolbar();
      }
    }
    this.updatePositions();
    return true;
  }

  /**
   * Sync external value into preview element (OD_EDIT_SYNC_VALUE).
   */
  public syncValue(
    blockId: string,
    field: string,
    itemIndex: number | null | undefined,
    value: string
  ): boolean {
    return this.applyConfirmedValue(blockId, field, itemIndex, value);
  }

  /**
   * Destroy the overlay engine and clean up all listeners and DOM nodes.
   */
  public destroy(): void {
    if (this.isDestroyed) return;
    this.isDestroyed = true;

    if (this.activeTextEdit) {
      this.finishActiveTextEdit(false);
    }

    if (this.inlineToolbar && this.inlineToolbar.parentElement) {
      this.inlineToolbar.parentElement.removeChild(this.inlineToolbar);
    }
    this.inlineToolbar = null;
    this.toolbarCounter = null;
    this.toolbarBtnSave = null;
    this.toolbarBtnCancel = null;

    if (this.doc && this.enablePointerEvents) {
      this.doc.removeEventListener("pointerover", this.boundPointerOver, true);
      this.doc.removeEventListener("pointerout", this.boundPointerOut, true);
      this.doc.removeEventListener("click", this.boundClick, true);
      this.doc.removeEventListener("dblclick", this.boundDblClick, true);

      const win = this.doc.defaultView || (typeof window !== "undefined" ? window : null);
      if (win) {
        win.removeEventListener("scroll", this.boundScrollOrResize, { passive: true } as any);
        win.removeEventListener("resize", this.boundScrollOrResize, { passive: true } as any);
      }
    }

    const parent = this.layer?.parentElement || (this.layer as any)?.parentNode;
    if (parent && this.layer) {
      parent.removeChild(this.layer);
    }

    this.layer = null;
    this.hoverBox = null;
    this.selectedBox = null;
    this.activeHoverEl = null;
    this.activeSelectedEl = null;
  }
}
