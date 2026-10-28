import React, { useState, useEffect, useCallback, useRef } from "react";
import { useParams, Link, useLocation } from "react-router-dom";
import {
  ProjectData,
  PageSummary,
  PageData,
  BlockItem,
  ThemeConfig,
  WebsiteStatusMap,
} from "../../types";
import {
  getWebsiteProject,
  updateWebsiteProject,
  listWebsitePages,
  getWebsitePage,
  createWebsitePage,
  updateWebsitePage,
  deleteWebsitePage,
  reorderWebsitePages,
  publishWebsiteProject,
  unpublishWebsiteProject,
  rollbackWebsiteVersion,
  generateWebsitePreviewToken,
} from "../../api/client";
import { CanvasBlockRenderer } from "../../components/website/CanvasBlockRenderer";
import { BlockPropertyForm } from "../../components/website/BlockPropertyForm";
import { BlockPickerModal } from "../../components/website/BlockPickerModal";
import { PageSettingsForm } from "../../components/website/PageSettingsForm";
import { ThemeSettingsForm } from "../../components/website/ThemeSettingsForm";
import { PublishModal } from "../../components/website/PublishModal";
import { VersionHistoryDrawer } from "../../components/website/VersionHistoryDrawer";

const BASE_URL = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? "";

export const WebsiteEditor: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const projectId = parseInt(id || "0", 10);
  const location = useLocation();
  const basePath = location.pathname.startsWith("/admin") ? "/admin/websites" : "/publisher/websites";

  // Core Data States
  const [project, setProject] = useState<ProjectData | null>(null);
  const [pages, setPages] = useState<PageSummary[]>([]);
  const [activePage, setActivePage] = useState<PageData | null>(null);
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);

  // Baseline copies for dirty-state detection
  const baselinePageRef = useRef<string>("");
  const baselineProjectRef = useRef<string>("");

  // Editor UI View States
  const [activeTabLeft, setActiveTabLeft] = useState<"pages" | "blocks">("blocks");
  const [activeTabRight, setActiveTabRight] = useState<"inspector" | "pageSettings" | "themeSettings">("inspector");
  const [viewMode, setViewMode] = useState<"canvas" | "preview">("canvas");
  const [previewViewport, setPreviewViewport] = useState<"desktop" | "tablet" | "mobile">("desktop");

  // Save & Loading States
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  // Preview Token State
  const [previewToken, setPreviewToken] = useState<string | null>(null);
  const [previewTokenExpiry, setPreviewTokenExpiry] = useState<number | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Modal States
  const [isBlockPickerOpen, setIsBlockPickerOpen] = useState(false);
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isUnpublishing, setIsUnpublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [isVersionHistoryOpen, setIsVersionHistoryOpen] = useState(false);

  // Add Page Modal State
  const [isAddPageOpen, setIsAddPageOpen] = useState(false);
  const [newPageTitle, setNewPageTitle] = useState("");
  const [newPageSlug, setNewPageSlug] = useState("");
  const [newPageIsHome, setNewPageIsHome] = useState(false);
  const [addPageError, setAddPageError] = useState<string | null>(null);
  const [isCreatingPage, setIsCreatingPage] = useState(false);

  // Unsaved Navigation Modal
  const [showUnsavedPrompt, setShowUnsavedPrompt] = useState(false);
  const [pendingPageSwitchId, setPendingPageSwitchId] = useState<number | null>(null);

  // 1. Initial Data Fetch
  useEffect(() => {
    if (projectId > 0) {
      loadProjectAndPages();
    }
  }, [projectId]);

  const loadProjectAndPages = async (preservePageId?: number) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const proj = await getWebsiteProject(projectId);
      setProject(proj);
      baselineProjectRef.current = JSON.stringify({
        name: proj.name,
        theme_config: proj.theme_config,
      });

      const pagesList = await listWebsitePages(projectId);
      setPages(pagesList);

      const targetPageId = preservePageId || (pagesList.find((p) => p.is_home)?.id ?? pagesList[0]?.id);
      if (targetPageId) {
        const fullPage = await getWebsitePage(projectId, targetPageId);
        setActivePage(fullPage);
        baselinePageRef.current = JSON.stringify(fullPage);
        if (fullPage.blocks?.blocks?.length > 0) {
          setSelectedBlockId(fullPage.blocks.blocks[0].id);
        } else {
          setSelectedBlockId(null);
        }
      }
      setIsDirty(false);
    } catch (err: any) {
      setLoadError(err?.response?.data?.detail || err.message || "Failed to load website editor");
    } finally {
      setIsLoading(false);
    }
  };

  // 2. Unload / Navigation Guards & Shortcuts
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "You have unsaved changes. Are you sure you want to leave?";
        return e.returnValue;
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  // Keyboard shortcut Ctrl+S / Cmd+S
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (isDirty && !isSaving) {
          handleSave();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isDirty, isSaving]);

  // Discard current page edits and revert to saved baseline
  const handleDiscardCurrentChanges = () => {
    if (baselinePageRef.current) {
      try {
        const basePage = JSON.parse(baselinePageRef.current);
        setActivePage(basePage);
      } catch {}
    }
    if (baselineProjectRef.current && project) {
      try {
        const baseProj = JSON.parse(baselineProjectRef.current);
        setProject((prev) =>
          prev ? { ...prev, name: baseProj.name, theme_config: baseProj.theme_config } : null
        );
      } catch {}
    }
    setIsDirty(false);
    setSaveError(null);
  };

  // 3. Switch Page Handler with Dirty Check
  const handleSelectPage = async (pageId: number) => {
    if (activePage?.id === pageId) return;
    if (isDirty) {
      setPendingPageSwitchId(pageId);
      setShowUnsavedPrompt(true);
      return;
    }
    await switchPageDirect(pageId);
  };

  const switchPageDirect = async (pageId: number) => {
    setIsLoading(true);
    try {
      // Revert any discarded project/theme changes back to saved baseline
      if (baselineProjectRef.current && project) {
        try {
          const baseProj = JSON.parse(baselineProjectRef.current);
          setProject((prev) =>
            prev ? { ...prev, name: baseProj.name, theme_config: baseProj.theme_config } : null
          );
        } catch {}
      }
      const pageData = await getWebsitePage(projectId, pageId);
      setActivePage(pageData);
      baselinePageRef.current = JSON.stringify(pageData);
      setIsDirty(false);
      if (pageData.blocks?.blocks?.length > 0) {
        setSelectedBlockId(pageData.blocks.blocks[0].id);
      } else {
        setSelectedBlockId(null);
      }
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail || err.message || "Failed to load page");
    } finally {
      setIsLoading(false);
    }
  };

  // 4. Save Changes to Page & Project
  const handleSave = async (): Promise<boolean> => {
    if (isSaving || !activePage || !project) return false;
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      // 1. Save Page only if modified
      const currentPageSnap = JSON.stringify(activePage);
      if (currentPageSnap !== baselinePageRef.current) {
        const updatedPage = await updateWebsitePage(projectId, activePage.id, {
          title: activePage.title,
          slug: activePage.slug,
          is_home: activePage.is_home,
          seo_meta: activePage.seo_meta,
          blocks: activePage.blocks,
        });
        setActivePage(updatedPage);
        baselinePageRef.current = JSON.stringify(updatedPage);
      }

      // 2. Save Project theme/name only if modified
      const currentProjSnap = JSON.stringify({
        name: project.name,
        theme_config: project.theme_config,
      });
      if (currentProjSnap !== baselineProjectRef.current) {
        const updatedProj = await updateWebsiteProject(projectId, {
          name: project.name,
          theme_config: project.theme_config,
        });
        setProject(updatedProj);
        baselineProjectRef.current = currentProjSnap;
      }

      // 3. Refresh page list summary
      const updatedList = await listWebsitePages(projectId);
      setPages(updatedList);

      setIsDirty(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);

      // If we are currently in preview mode, refresh preview token
      if (viewMode === "preview") {
        fetchPreviewToken();
      }
      return true;
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail || err.message || "Failed to save changes");
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  // 5. Preview Token Acquisition
  const fetchPreviewToken = useCallback(async () => {
    setIsLoadingPreview(true);
    setPreviewError(null);
    try {
      const res = await generateWebsitePreviewToken(projectId);
      setPreviewToken(res.preview_token);
      setPreviewTokenExpiry(Date.now() + res.expires_in * 1000);
    } catch (err: any) {
      setPreviewError(
        err?.response?.data?.detail || err.message || "Failed to generate preview token"
      );
    } finally {
      setIsLoadingPreview(false);
    }
  }, [projectId]);

  // Handle Switch to Preview Mode
  const handleToggleViewMode = (mode: "canvas" | "preview") => {
    setViewMode(mode);
    if (mode === "preview") {
      // If token expired or not set, fetch a fresh one
      if (!previewToken || !previewTokenExpiry || Date.now() > previewTokenExpiry - 30000) {
        fetchPreviewToken();
      }
    }
  };

  // 6. Block Manipulation Handlers
  const handleAddBlock = (newBlock: BlockItem) => {
    if (!activePage) return;
    const currentBlocks = activePage.blocks?.blocks || [];
    const nextBlocks = [...currentBlocks, newBlock];
    setActivePage({
      ...activePage,
      blocks: {
        ...activePage.blocks,
        schema_version: activePage.blocks?.schema_version || 1,
        blocks: nextBlocks,
      },
    });
    setSelectedBlockId(newBlock.id);
    setActiveTabRight("inspector");
    setIsDirty(true);
  };

  const handleUpdateBlock = (updatedBlock: BlockItem) => {
    if (!activePage) return;
    const nextBlocks = (activePage.blocks?.blocks || []).map((b) =>
      b.id === updatedBlock.id ? updatedBlock : b
    );
    setActivePage({
      ...activePage,
      blocks: {
        ...activePage.blocks,
        schema_version: activePage.blocks?.schema_version || 1,
        blocks: nextBlocks,
      },
    });
    setIsDirty(true);
  };

  const handleDeleteBlock = (blockId: string) => {
    if (!activePage) return;
    const nextBlocks = (activePage.blocks?.blocks || []).filter((b) => b.id !== blockId);
    setActivePage({
      ...activePage,
      blocks: {
        ...activePage.blocks,
        schema_version: activePage.blocks?.schema_version || 1,
        blocks: nextBlocks,
      },
    });
    if (selectedBlockId === blockId) {
      setSelectedBlockId(nextBlocks[0]?.id || null);
    }
    setIsDirty(true);
  };

  const handleMoveBlock = (blockId: string, direction: "up" | "down") => {
    if (!activePage) return;
    const blocks = [...(activePage.blocks?.blocks || [])];
    const index = blocks.findIndex((b) => b.id === blockId);
    if (index === -1) return;
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= blocks.length) return;

    const temp = blocks[index];
    blocks[index] = blocks[targetIndex];
    blocks[targetIndex] = temp;

    setActivePage({
      ...activePage,
      blocks: {
        ...activePage.blocks,
        schema_version: activePage.blocks?.schema_version || 1,
        blocks,
      },
    });
    setIsDirty(true);
  };

  // 7. Page Settings Handlers
  const handleUpdatePageSettings = (updated: PageData) => {
    setActivePage(updated);
    setIsDirty(true);
  };

  const handleDeletePage = async (pageId: number) => {
    try {
      await deleteWebsitePage(projectId, pageId);
      await loadProjectAndPages();
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail || err.message || "Failed to delete page");
    }
  };

  const handleMovePage = async (pageId: number, direction: "up" | "down") => {
    const list = [...pages];
    const index = list.findIndex((p) => p.id === pageId);
    if (index === -1) return;
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= list.length) return;

    const temp = list[index];
    list[index] = list[targetIndex];
    list[targetIndex] = temp;

    try {
      const reordered = await reorderWebsitePages(
        projectId,
        list.map((p) => p.id)
      );
      setPages(reordered);
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail || err.message || "Failed to reorder pages");
    }
  };

  const handleCreatePageSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddPageError(null);
    if (!newPageTitle.trim()) {
      setAddPageError("Page title is required");
      return;
    }
    setIsCreatingPage(true);
    try {
      const created = await createWebsitePage(projectId, {
        title: newPageTitle.trim(),
        slug: newPageIsHome ? "" : newPageSlug.trim().toLowerCase(),
        is_home: newPageIsHome,
      });
      setIsAddPageOpen(false);
      setNewPageTitle("");
      setNewPageSlug("");
      setNewPageIsHome(false);
      await loadProjectAndPages(created.id);
    } catch (err: any) {
      setAddPageError(err?.response?.data?.detail || err.message || "Failed to create page");
    } finally {
      setIsCreatingPage(false);
    }
  };

  // 8. Theme Settings Handler
  const handleUpdateTheme = (updatedTheme: ThemeConfig) => {
    if (!project) return;
    setProject({
      ...project,
      theme_config: updatedTheme,
    });
    setIsDirty(true);
  };

  const handleUpdateProjectName = (name: string) => {
    if (!project) return;
    setProject({
      ...project,
      name,
    });
    setIsDirty(true);
  };

  // 9. Publish & Version Rollback Handlers
  const handlePublish = async (changelogNotes?: string) => {
    setIsPublishing(true);
    setPublishError(null);
    try {
      // First save any unsaved changes
      if (isDirty && activePage) {
        const saved = await handleSave();
        if (!saved) return;
      }
      const updatedProj = await publishWebsiteProject(projectId, changelogNotes);
      setProject(updatedProj);
    } catch (err: any) {
      setPublishError(err?.response?.data?.detail || err.message || "Publication failed");
    } finally {
      setIsPublishing(false);
    }
  };

  const handleUnpublish = async () => {
    setIsUnpublishing(true);
    setPublishError(null);
    try {
      const updatedProj = await unpublishWebsiteProject(projectId);
      setProject(updatedProj);
      setIsPublishModalOpen(false);
    } catch (err: any) {
      setPublishError(err?.response?.data?.detail || err.message || "Unpublish failed");
    } finally {
      setIsUnpublishing(false);
    }
  };

  const handleRollback = async (versionId: number) => {
    await rollbackWebsiteVersion(projectId, versionId);
    await loadProjectAndPages();
    setIsVersionHistoryOpen(false);
  };

  // Loading & Error States
  if (isLoading && !project) {
    return (
      <div style={{ padding: "4rem 1rem", textAlign: "center", color: "var(--text-muted)" }}>
        Loading website editor...
      </div>
    );
  }

  if (loadError || !project) {
    return (
      <div className="card" style={{ maxWidth: "600px", margin: "3rem auto", textAlign: "center", padding: "2rem" }}>
        <h3 style={{ color: "var(--error)", marginBottom: "0.5rem" }}>Unable to load website</h3>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", marginBottom: "1.5rem" }}>
          {loadError || "The requested website project could not be found."}
        </p>
        <Link to={basePath} className="btn btn-secondary">
          ← Back to Websites
        </Link>
      </div>
    );
  }

  const selectedBlock = activePage?.blocks?.blocks?.find((b) => b.id === selectedBlockId) || null;
  const currentBlocks = activePage?.blocks?.blocks || [];

  const previewPagePath = activePage?.is_home ? "/" : `/${activePage?.slug || ""}`;
  const previewIframeSrc = previewToken
    ? `${BASE_URL}/api/v1/public/website/preview/${previewToken}?path=${encodeURIComponent(previewPagePath)}`
    : "";

  return (
    <div className="wb-shell" data-testid="website-editor">
      {/* ── TOP TOOLBAR ──────────────────────────────────────────────────────── */}
      <header className="wb-toolbar" data-testid="website-toolbar">
        {/* Left: Back & Project Info */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.875rem" }}>
          <Link
            to={basePath}
            className="btn btn-secondary btn-sm"
            style={{ fontSize: "0.8125rem", padding: "0.25rem 0.6rem", textDecoration: "none" }}
            title="Return to websites list"
          >
            ← Sites
          </Link>

          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontWeight: 700, fontSize: "1rem" }}>{project.name}</span>
              <span
                className={`wb-badge ${project.status === 1 ? "wb-badge-published" : "wb-badge-draft"}`}
              >
                {(WebsiteStatusMap[project.status] || "draft").toUpperCase()}
              </span>
            </div>
            <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)" }}>
              <code>{project.slug}</code> • {pages.length} page{pages.length !== 1 ? "s" : ""}
            </div>
          </div>
        </div>

        {/* Center: Canvas / Preview Switcher & Viewport Size */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div style={{ display: "flex", background: "var(--bg-secondary)", borderRadius: "var(--radius-sm)", padding: "2px" }}>
            <button
              type="button"
              className={`btn btn-sm ${viewMode === "canvas" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => handleToggleViewMode("canvas")}
              style={{ fontSize: "0.75rem", padding: "0.25rem 0.75rem", border: "none" }}
              data-testid="toggle-canvas-mode"
            >
              Canvas
            </button>
            <button
              type="button"
              className={`btn btn-sm ${viewMode === "preview" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => handleToggleViewMode("preview")}
              style={{ fontSize: "0.75rem", padding: "0.25rem 0.75rem", border: "none" }}
              data-testid="toggle-preview-mode"
            >
              Live Preview
            </button>
          </div>

          {viewMode === "preview" && (
            <div style={{ display: "flex", background: "var(--bg-secondary)", borderRadius: "var(--radius-sm)", padding: "2px" }}>
              <button
                type="button"
                className={`btn btn-sm ${previewViewport === "desktop" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setPreviewViewport("desktop")}
                style={{ fontSize: "0.6875rem", padding: "0.2rem 0.5rem", border: "none" }}
                title="Desktop (100%)"
              >
                🖥️ Desktop
              </button>
              <button
                type="button"
                className={`btn btn-sm ${previewViewport === "tablet" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setPreviewViewport("tablet")}
                style={{ fontSize: "0.6875rem", padding: "0.2rem 0.5rem", border: "none" }}
                title="Tablet (768px)"
              >
                📱 Tablet
              </button>
              <button
                type="button"
                className={`btn btn-sm ${previewViewport === "mobile" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setPreviewViewport("mobile")}
                style={{ fontSize: "0.6875rem", padding: "0.2rem 0.5rem", border: "none" }}
                title="Mobile (375px)"
              >
                📲 Mobile
              </button>
            </div>
          )}
        </div>

        {/* Right: Dirty Status, Save, History, Publish */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
          {isDirty ? (
            <span
              style={{ fontSize: "0.75rem", color: "var(--warning)", display: "flex", alignItems: "center", gap: "0.25rem" }}
              data-testid="unsaved-badge"
            >
              ● Unsaved changes
            </span>
          ) : saveSuccess ? (
            <span style={{ fontSize: "0.75rem", color: "var(--success)" }} data-testid="saved-badge">
              ✓ Saved
            </span>
          ) : (
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              All changes saved
            </span>
          )}

          {isDirty && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleDiscardCurrentChanges}
              disabled={isSaving}
              style={{ fontSize: "0.8125rem", color: "var(--text-muted)" }}
              data-testid="discard-changes-btn"
              title="Discard unsaved edits and restore last saved state"
            >
              Discard
            </button>
          )}

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleSave}
            disabled={isSaving || !isDirty}
            style={{ fontSize: "0.8125rem" }}
            data-testid="save-website-btn"
          >
            {isSaving ? "Saving..." : "Save Draft"}
          </button>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setIsVersionHistoryOpen(true)}
            style={{ fontSize: "0.8125rem" }}
            data-testid="open-history-btn"
          >
            History
          </button>

          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setIsPublishModalOpen(true)}
            style={{ fontSize: "0.8125rem" }}
            data-testid="open-publish-btn"
          >
            {project.status === 1 ? "Publish Settings" : "Publish"}
          </button>
        </div>
      </header>

      {/* Save Error Alert Banner */}
      {saveError && (
        <div
          style={{
            padding: "0.5rem 1rem",
            backgroundColor: "rgba(239, 68, 68, 0.1)",
            borderBottom: "1px solid rgba(239, 68, 68, 0.3)",
            color: "var(--error)",
            fontSize: "0.8125rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Error saving changes: {saveError}</span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setSaveError(null)}
            style={{ fontSize: "0.6875rem", padding: "0.1rem 0.4rem" }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── WORKSPACE BODY ───────────────────────────────────────────────────── */}
      <div className="wb-layout">
        {/* ── LEFT PANEL (Pages & Blocks) ──────────────────────────────────── */}
        <aside className="wb-panel-left" data-testid="website-panel-left">
          {/* Left Panel Tabs */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: "0.75rem" }}>
            <button
              type="button"
              className="btn"
              onClick={() => setActiveTabLeft("pages")}
              style={{
                flex: 1,
                fontSize: "0.8125rem",
                padding: "0.5rem",
                borderRadius: 0,
                borderBottom: activeTabLeft === "pages" ? "2px solid var(--primary)" : "none",
                fontWeight: activeTabLeft === "pages" ? 700 : 500,
                color: activeTabLeft === "pages" ? "var(--primary)" : "var(--text-secondary)",
                background: "transparent",
              }}
              data-testid="tab-left-pages"
            >
              Pages ({pages.length})
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setActiveTabLeft("blocks")}
              style={{
                flex: 1,
                fontSize: "0.8125rem",
                padding: "0.5rem",
                borderRadius: 0,
                borderBottom: activeTabLeft === "blocks" ? "2px solid var(--primary)" : "none",
                fontWeight: activeTabLeft === "blocks" ? 700 : 500,
                color: activeTabLeft === "blocks" ? "var(--primary)" : "var(--text-secondary)",
                background: "transparent",
              }}
              data-testid="tab-left-blocks"
            >
              Blocks ({currentBlocks.length})
            </button>
          </div>

          {/* Left Tab: Pages */}
          {activeTabLeft === "pages" && (
            <div className="space-y-3">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase" }}>
                  Navigation Pages
                </span>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setIsAddPageOpen(true)}
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                  data-testid="add-page-btn"
                >
                  + Add Page
                </button>
              </div>

              <div className="space-y-1">
                {pages.map((p, idx) => {
                  const isActive = activePage?.id === p.id;
                  return (
                    <div
                      key={p.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "0.5rem 0.75rem",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid",
                        borderColor: isActive ? "var(--primary)" : "var(--border)",
                        backgroundColor: isActive ? "rgba(37, 99, 235, 0.08)" : "var(--bg-card)",
                        cursor: "pointer",
                        transition: "all 0.15s ease",
                      }}
                      onClick={() => handleSelectPage(p.id)}
                      data-testid={`page-nav-item-${p.id}`}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                          <span style={{ fontWeight: isActive ? 700 : 500, fontSize: "0.8125rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {p.title}
                          </span>
                          {p.is_home && (
                            <span className="wb-badge" style={{ fontSize: "0.625rem", padding: "0.1rem 0.35rem" }}>
                              Home
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)" }}>
                          {p.is_home ? "/" : `/${p.slug}`}
                        </div>
                      </div>

                      {/* Accessible Reorder controls */}
                      <div style={{ display: "flex", gap: "0.25rem", marginLeft: "0.5rem" }} onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={idx === 0}
                          onClick={() => handleMovePage(p.id, "up")}
                          style={{ padding: "0.1rem 0.3rem", fontSize: "0.6875rem" }}
                          title="Move page up"
                          aria-label={`Move ${p.title} up`}
                        >
                          ▲
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={idx === pages.length - 1}
                          onClick={() => handleMovePage(p.id, "down")}
                          style={{ padding: "0.1rem 0.3rem", fontSize: "0.6875rem" }}
                          title="Move page down"
                          aria-label={`Move ${p.title} down`}
                        >
                          ▼
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Left Tab: Blocks Hierarchy */}
          {activeTabLeft === "blocks" && (
            <div className="space-y-3">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase" }}>
                  Current Page Blocks
                </span>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => setIsBlockPickerOpen(true)}
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                  data-testid="open-block-picker-btn"
                >
                  + Add Block
                </button>
              </div>

              {currentBlocks.length === 0 ? (
                <div style={{ textAlign: "center", padding: "2rem 0.5rem", color: "var(--text-muted)", fontSize: "0.8125rem" }}>
                  No blocks on this page. Click "+ Add Block" to begin building.
                </div>
              ) : (
                <div className="space-y-1">
                  {currentBlocks.map((blk, idx) => {
                    const isSelected = selectedBlockId === blk.id;
                    return (
                      <div
                        key={blk.id}
                        className={`wb-block-card ${isSelected ? "selected" : ""}`}
                        onClick={() => {
                          setSelectedBlockId(blk.id);
                          setActiveTabRight("inspector");
                        }}
                        style={{
                          opacity: blk.is_visible ? 1 : 0.6,
                        }}
                        data-testid={`block-tree-item-${blk.id}`}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                            <span style={{ fontWeight: 600, fontSize: "0.8125rem", textTransform: "capitalize" }}>
                              {blk.type.replace("_", " ")}
                            </span>
                            {!blk.is_visible && (
                              <span style={{ fontSize: "0.625rem", color: "var(--text-muted)" }}>
                                (hidden)
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)", fontFamily: "monospace" }}>
                            {blk.id}
                          </div>
                        </div>

                        {/* Accessible Move Up / Down */}
                        <div style={{ display: "flex", gap: "0.25rem", marginLeft: "0.5rem" }} onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            disabled={idx === 0}
                            onClick={() => handleMoveBlock(blk.id, "up")}
                            style={{ padding: "0.1rem 0.3rem", fontSize: "0.6875rem" }}
                            title="Move block up"
                            aria-label={`Move ${blk.type} up`}
                          >
                            ▲
                          </button>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            disabled={idx === currentBlocks.length - 1}
                            onClick={() => handleMoveBlock(blk.id, "down")}
                            style={{ padding: "0.1rem 0.3rem", fontSize: "0.6875rem" }}
                            title="Move block down"
                            aria-label={`Move ${blk.type} down`}
                          >
                            ▼
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </aside>

        {/* ── CENTER STAGE (Canvas / Iframe Preview) ────────────────────────── */}
        <main className="wb-center-stage" data-testid="website-center-stage">
          {viewMode === "canvas" ? (
            /* Visual Canvas */
            <div style={{ maxWidth: "960px", margin: "0 auto", padding: "1.5rem 1rem" }}>
              {/* Active Page Header Bar */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "0.75rem 1rem",
                  backgroundColor: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-md)",
                  marginBottom: "1.5rem",
                }}
              >
                <div>
                  <h2 style={{ fontSize: "1.125rem", fontWeight: 700, margin: 0 }}>
                    Editing: {activePage?.title}
                  </h2>
                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    Path: <code>{activePage?.is_home ? "/" : `/${activePage?.slug}`}</code>
                  </div>
                </div>

                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => {
                      setActiveTabRight("pageSettings");
                    }}
                    style={{ fontSize: "0.75rem" }}
                  >
                    Page Settings
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => setIsBlockPickerOpen(true)}
                    style={{ fontSize: "0.75rem" }}
                    data-testid="canvas-add-block-btn"
                  >
                    + Add Block
                  </button>
                </div>
              </div>

              {/* Blocks Render Canvas */}
              {currentBlocks.length === 0 ? (
                <div
                  className="card"
                  style={{
                    textAlign: "center",
                    padding: "4rem 2rem",
                    border: "2px dashed var(--border)",
                    backgroundColor: "transparent",
                  }}
                >
                  <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>🧱</div>
                  <h3 style={{ fontSize: "1.125rem", fontWeight: 700, margin: "0 0 0.5rem" }}>
                    This page has no blocks yet
                  </h3>
                  <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)", marginBottom: "1.25rem" }}>
                    Add headers, hero banners, capability grids, galleries, forms, or ad placeholders.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => setIsBlockPickerOpen(true)}
                  >
                    Add First Block
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  {currentBlocks.map((blk, idx) => (
                    <CanvasBlockRenderer
                      key={blk.id}
                      block={blk}
                      isSelected={selectedBlockId === blk.id}
                      theme={project.theme_config}
                      onSelect={() => {
                        setSelectedBlockId(blk.id);
                        setActiveTabRight("inspector");
                      }}
                      onMoveUp={() => handleMoveBlock(blk.id, "up")}
                      onMoveDown={() => handleMoveBlock(blk.id, "down")}
                      onDelete={() => handleDeleteBlock(blk.id)}
                      onToggleVisibility={() => handleUpdateBlock({ ...blk, is_visible: !blk.is_visible })}
                      canMoveUp={idx > 0}
                      canMoveDown={idx < currentBlocks.length - 1}
                    />
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Live Framed Preview Iframe */
            <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: "1rem" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "0.5rem 1rem",
                  backgroundColor: "rgba(37, 99, 235, 0.08)",
                  border: "1px solid rgba(37, 99, 235, 0.2)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.75rem",
                  marginBottom: "0.75rem",
                }}
              >
                <div>
                  <strong>Live Preview Mode:</strong> Showing draft snapshot rendered through Phase 10C. Ad placements are mock placeholders.
                </div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={fetchPreviewToken}
                  disabled={isLoadingPreview}
                  style={{ fontSize: "0.6875rem", padding: "0.2rem 0.5rem" }}
                  data-testid="refresh-preview-btn"
                >
                  {isLoadingPreview ? "Refreshing..." : "↻ Refresh Token"}
                </button>
              </div>

              {previewError ? (
                <div
                  className="card"
                  style={{
                    padding: "2rem",
                    textAlign: "center",
                    margin: "auto",
                    maxWidth: "500px",
                  }}
                >
                  <h4 style={{ color: "var(--error)", margin: "0 0 0.5rem" }}>Preview Unavailable</h4>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
                    {previewError}
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={fetchPreviewToken}
                  >
                    Retry Preview Generation
                  </button>
                </div>
              ) : (
                <div
                  className={`wb-device-wrapper ${previewViewport}`}
                  style={{
                    flex: 1,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "flex-start",
                    overflow: "hidden",
                  }}
                >
                  <iframe
                    src={previewIframeSrc}
                    title="Website Draft Preview"
                    sandbox="allow-same-origin allow-forms"
                    style={{
                      width:
                        previewViewport === "mobile"
                          ? "375px"
                          : previewViewport === "tablet"
                          ? "768px"
                          : "100%",
                      height: "100%",
                      border: "1px solid var(--border)",
                      borderRadius: previewViewport !== "desktop" ? "var(--radius-md)" : "0",
                      backgroundColor: project.theme_config.background_color || "#ffffff",
                      boxShadow: previewViewport !== "desktop" ? "0 10px 25px rgba(0, 0, 0, 0.15)" : "none",
                    }}
                    data-testid="preview-iframe"
                  />
                </div>
              )}
            </div>
          )}
        </main>

        {/* ── RIGHT PANEL (Inspector / Page / Theme Settings) ──────────────── */}
        <aside className="wb-panel-right" data-testid="website-panel-right">
          {/* Right Panel Tabs */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: "0.75rem" }}>
            <button
              type="button"
              className="btn"
              onClick={() => setActiveTabRight("inspector")}
              style={{
                flex: 1,
                fontSize: "0.75rem",
                padding: "0.5rem 0.25rem",
                borderRadius: 0,
                borderBottom: activeTabRight === "inspector" ? "2px solid var(--primary)" : "none",
                fontWeight: activeTabRight === "inspector" ? 700 : 500,
                color: activeTabRight === "inspector" ? "var(--primary)" : "var(--text-secondary)",
                background: "transparent",
              }}
              data-testid="tab-right-inspector"
            >
              Block
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setActiveTabRight("pageSettings")}
              style={{
                flex: 1,
                fontSize: "0.75rem",
                padding: "0.5rem 0.25rem",
                borderRadius: 0,
                borderBottom: activeTabRight === "pageSettings" ? "2px solid var(--primary)" : "none",
                fontWeight: activeTabRight === "pageSettings" ? 700 : 500,
                color: activeTabRight === "pageSettings" ? "var(--primary)" : "var(--text-secondary)",
                background: "transparent",
              }}
              data-testid="tab-right-page-settings"
            >
              Page & SEO
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setActiveTabRight("themeSettings")}
              style={{
                flex: 1,
                fontSize: "0.75rem",
                padding: "0.5rem 0.25rem",
                borderRadius: 0,
                borderBottom: activeTabRight === "themeSettings" ? "2px solid var(--primary)" : "none",
                fontWeight: activeTabRight === "themeSettings" ? 700 : 500,
                color: activeTabRight === "themeSettings" ? "var(--primary)" : "var(--text-secondary)",
                background: "transparent",
              }}
              data-testid="tab-right-theme-settings"
            >
              Site & Theme
            </button>
          </div>

          {/* Right Tab Content */}
          {activeTabRight === "inspector" && (
            <div>
              {selectedBlock ? (
                <BlockPropertyForm
                  block={selectedBlock}
                  onChange={handleUpdateBlock}
                  onDelete={handleDeleteBlock}
                  onClose={() => setSelectedBlockId(null)}
                />
              ) : (
                <div style={{ textAlign: "center", padding: "3rem 1rem", color: "var(--text-muted)" }}>
                  <div style={{ fontSize: "1.75rem", marginBottom: "0.5rem" }}>👈</div>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "0.25rem" }}>
                    No Block Selected
                  </div>
                  <div style={{ fontSize: "0.75rem", lineHeight: 1.4 }}>
                    Click any block on the canvas or in the left hierarchy list to edit its fields.
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTabRight === "pageSettings" && activePage && (
            <PageSettingsForm
              page={activePage}
              canDelete={pages.length > 1}
              onChange={handleUpdatePageSettings}
              onDeletePage={handleDeletePage}
            />
          )}

          {activeTabRight === "themeSettings" && (
            <ThemeSettingsForm
              projectName={project.name}
              theme={project.theme_config}
              onProjectNameChange={handleUpdateProjectName}
              onChange={handleUpdateTheme}
            />
          )}
        </aside>
      </div>

      {/* ── MODALS ───────────────────────────────────────────────────────────── */}
      {/* Block Picker Modal */}
      <BlockPickerModal
        isOpen={isBlockPickerOpen}
        onClose={() => setIsBlockPickerOpen(false)}
        onAddBlock={handleAddBlock}
      />

      {/* Add Page Modal */}
      {isAddPageOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            backgroundColor: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "1rem",
            backdropFilter: "blur(2px)",
          }}
          data-testid="add-page-modal"
        >
          <div
            className="card"
            style={{
              width: "100%",
              maxWidth: "460px",
              padding: "1.5rem",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
              <h3 style={{ fontSize: "1.125rem", fontWeight: 700, margin: 0 }}>Add New Page</h3>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setIsAddPageOpen(false)}
                disabled={isCreatingPage}
              >
                ✕
              </button>
            </div>

            {addPageError && (
              <div
                style={{
                  padding: "0.625rem",
                  borderRadius: "var(--radius-sm)",
                  backgroundColor: "rgba(239, 68, 68, 0.1)",
                  color: "var(--error)",
                  fontSize: "0.8125rem",
                  marginBottom: "1rem",
                }}
              >
                {addPageError}
              </div>
            )}

            <form onSubmit={handleCreatePageSubmit} className="space-y-3">
              <div>
                <label className="form-label" style={{ fontSize: "0.8125rem" }}>
                  Page Title <span style={{ color: "var(--error)" }}>*</span>
                </label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Services"
                  value={newPageTitle}
                  required
                  maxLength={255}
                  onChange={(e) => {
                    const title = e.target.value;
                    setNewPageTitle(title);
                    if (!newPageSlug || newPageSlug === newPageTitle.toLowerCase().replace(/[^a-z0-9]+/g, "-")) {
                      setNewPageSlug(title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""));
                    }
                  }}
                  data-testid="new-page-title-input"
                />
              </div>

              <div>
                <label className="form-label" style={{ fontSize: "0.8125rem" }}>
                  URL Slug
                </label>
                <div style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
                  <span style={{ color: "var(--text-muted)", fontSize: "0.8125rem" }}>/</span>
                  <input
                    type="text"
                    className="form-input"
                    placeholder={newPageIsHome ? "(root homepage)" : "services"}
                    disabled={newPageIsHome}
                    value={newPageSlug}
                    maxLength={100}
                    onChange={(e) => setNewPageSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                    data-testid="new-page-slug-input"
                  />
                </div>
              </div>

              <div>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer", fontSize: "0.8125rem" }}>
                  <input
                    type="checkbox"
                    checked={newPageIsHome}
                    onChange={(e) => setNewPageIsHome(e.target.checked)}
                  />
                  <span>Make this the default homepage</span>
                </label>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem", paddingTop: "0.5rem" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsAddPageOpen(false)}
                  disabled={isCreatingPage}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isCreatingPage}
                  data-testid="confirm-add-page-btn"
                >
                  {isCreatingPage ? "Creating..." : "Create Page"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Publish Modal */}
      <PublishModal
        isOpen={isPublishModalOpen}
        project={project}
        isPublishing={isPublishing}
        isUnpublishing={isUnpublishing}
        error={publishError}
        onClose={() => setIsPublishModalOpen(false)}
        onPublish={handlePublish}
        onUnpublish={handleUnpublish}
      />

      {/* Version History Drawer */}
      <VersionHistoryDrawer
        isOpen={isVersionHistoryOpen}
        projectId={project.id}
        currentPublishedVersionId={project.current_published_version_id}
        onClose={() => setIsVersionHistoryOpen(false)}
        onRollback={handleRollback}
      />

      {/* Unsaved Changes Page Switch Warning */}
      {showUnsavedPrompt && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            backgroundColor: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "1rem",
            backdropFilter: "blur(2px)",
          }}
          data-testid="unsaved-changes-dialog"
        >
          <div className="card" style={{ width: "100%", maxWidth: "440px", padding: "1.5rem" }}>
            <h3 style={{ fontSize: "1.125rem", fontWeight: 700, margin: "0 0 0.5rem", color: "var(--warning)" }}>
              Unsaved Changes
            </h3>
            <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "0 0 1.25rem" }}>
              You have unsaved changes on the current page. If you switch to another page without saving, your edits will be lost.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setShowUnsavedPrompt(false);
                  setPendingPageSwitchId(null);
                }}
              >
                Keep Editing
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                style={{ color: "var(--error)" }}
                onClick={async () => {
                  setShowUnsavedPrompt(false);
                  if (pendingPageSwitchId) {
                    await switchPageDirect(pendingPageSwitchId);
                    setPendingPageSwitchId(null);
                  }
                }}
                data-testid="discard-and-switch-btn"
              >
                Discard & Switch
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={async () => {
                  const saved = await handleSave();
                  if (saved) {
                    setShowUnsavedPrompt(false);
                    if (pendingPageSwitchId) {
                      await switchPageDirect(pendingPageSwitchId);
                      setPendingPageSwitchId(null);
                    }
                  }
                }}
                data-testid="save-and-switch-btn"
              >
                Save & Switch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
