import React, { useState, useEffect, useRef } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { ProjectSummary, WebsiteStatusMap, WebsiteTemplateSummary } from "../../types";
import {
  listWebsiteProjects,
  createWebsiteProject,
  deleteWebsiteProject,
  checkWebsiteSlug,
  listWebsiteTemplates,
} from "../../api/client";
import { TemplatePreviewModal } from "../../components/website/TemplatePreviewModal";

const TEMPLATE_CATEGORIES = [
  "All",
  "Landing & Marketing",
  "Publishing & Editorial",
  "Business & Operations",
  "Creative & Agency",
];

export const WebsiteList: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const basePath = location.pathname.startsWith("/admin") ? "/admin/websites" : "/publisher/websites";

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Create Modal State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState("saas-landing");
  const [isCheckingSlug, setIsCheckingSlug] = useState(false);
  const [slugStatus, setSlugStatus] = useState<{ available: boolean; message: string } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const lastSlugRef = useRef<string>("");

  // Templates Catalog State (Phase 2)
  const [templates, setTemplates] = useState<WebsiteTemplateSummary[]>([]);
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [templateSearch, setTemplateSearch] = useState("");
  const [previewTemplate, setPreviewTemplate] = useState<WebsiteTemplateSummary | null>(null);

  // Delete Modal State
  const [projectToDelete, setProjectToDelete] = useState<ProjectSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    loadProjects();
    loadTemplates();
  }, []);

  const loadTemplates = async () => {
    setIsLoadingTemplates(true);
    try {
      const data = await listWebsiteTemplates();
      setTemplates(data);
      if (data.length > 0) {
        setSelectedTemplate((prev) => prev || data[0].id);
      }
    } catch (err: any) {
      console.error("Failed to load templates catalog:", err);
    } finally {
      setIsLoadingTemplates(false);
    }
  };

  const loadProjects = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await listWebsiteProjects();
      setProjects(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || "Failed to load website projects");
    } finally {
      setIsLoading(false);
    }
  };

  const handleNameChange = (name: string) => {
    setCreateName(name);
    // Auto-suggest slug if user hasn't manually edited slug
    if (!createSlug || createSlug === autoSlug(createName)) {
      const slugCandidate = autoSlug(name);
      setCreateSlug(slugCandidate);
      if (slugCandidate.length >= 3) {
        checkSlugAvailability(slugCandidate);
      }
    }
  };

  const autoSlug = (text: string) => {
    return text
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 32);
  };

  const checkSlugAvailability = async (slug: string) => {
    const clean = slug.trim().toLowerCase();
    if (clean.length < 3) {
      setSlugStatus({ available: false, message: "Slug must be at least 3 characters" });
      return;
    }
    lastSlugRef.current = clean;
    setIsCheckingSlug(true);
    try {
      const res = await checkWebsiteSlug(clean);
      if (lastSlugRef.current === clean) {
        setSlugStatus({
          available: res.available,
          message: res.available
            ? `Subdomain '${res.slug}' is available!`
            : res.reason || `Subdomain '${res.slug}' is unavailable`,
        });
      }
    } catch (err: any) {
      if (lastSlugRef.current === clean) {
        setSlugStatus({
          available: false,
          message: err?.message || "Could not verify slug availability",
        });
      }
    } finally {
      if (lastSlugRef.current === clean) {
        setIsCheckingSlug(false);
      }
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);

    const cleanSlug = createSlug.trim().toLowerCase();
    if (!createName.trim()) {
      setCreateError("Website project name is required");
      return;
    }
    if (cleanSlug.length < 3) {
      setCreateError("Subdomain slug must be at least 3 characters");
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await createWebsiteProject({
        name: createName.trim(),
        slug: cleanSlug,
        template_id: selectedTemplate,
      });
      setIsCreateOpen(false);
      navigate(`${basePath}/${created.id}`);
    } catch (err: any) {
      setCreateError(err?.response?.data?.detail || err.message || "Failed to create website");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteProject = async () => {
    if (!projectToDelete) return;
    setIsDeleting(true);
    try {
      await deleteWebsiteProject(projectToDelete.id);
      setProjectToDelete(null);
      await loadProjects();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || "Failed to delete website");
    } finally {
      setIsDeleting(false);
    }
  };

  const filteredProjects = projects.filter(
    (p) =>
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.slug.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const filteredTemplates = templates.filter((t) => {
    const matchesCat = selectedCategory === "All" || t.category === selectedCategory;
    const matchesSearch =
      !templateSearch.trim() ||
      t.name.toLowerCase().includes(templateSearch.toLowerCase()) ||
      t.description.toLowerCase().includes(templateSearch.toLowerCase());
    return matchesCat && matchesSearch;
  });

  return (
    <div className="space-y-6" data-testid="website-list-page">
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem" }}>
        <div>
          <h1 style={{ fontSize: "1.75rem", fontWeight: 700, margin: 0 }}>Website Builder</h1>
          <p style={{ margin: "0.25rem 0 0", color: "var(--text-secondary)", fontSize: "0.875rem" }}>
            Design, customize, and publish fast responsive websites with visual block editing.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            setCreateName("");
            setCreateSlug("");
            setSlugStatus(null);
            setCreateError(null);
            setIsCreateOpen(true);
          }}
          data-testid="create-website-btn"
        >
          + Create Website
        </button>
      </div>

      {/* Search and Filters */}
      <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
        <input
          type="text"
          className="form-input"
          placeholder="Search by site name or slug..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ maxWidth: "360px" }}
        />
        <span style={{ fontSize: "0.8125rem", color: "var(--text-muted)" }}>
          Showing {filteredProjects.length} of {projects.length} sites
        </span>
      </div>

      {/* Error state */}
      {error && (
        <div
          style={{
            padding: "1rem",
            borderRadius: "var(--radius-md)",
            backgroundColor: "rgba(239, 68, 68, 0.1)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            color: "var(--error)",
            fontSize: "0.875rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Projects Grid / Table */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: "4rem 1rem", color: "var(--text-muted)" }}>
          Loading your websites...
        </div>
      ) : filteredProjects.length === 0 ? (
        <div
          className="card"
          style={{
            textAlign: "center",
            padding: "4rem 2rem",
            color: "var(--text-muted)",
          }}
          data-testid="website-empty-state"
        >
          <div style={{ fontSize: "2.5rem", marginBottom: "0.75rem" }}>🌐</div>
          <h3 style={{ fontSize: "1.125rem", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 0.5rem" }}>
            {projects.length === 0 ? "No websites created yet" : "No matching websites found"}
          </h3>
          <p style={{ margin: "0 auto 1.5rem", maxWidth: "420px", fontSize: "0.875rem", lineHeight: 1.5 }}>
            {projects.length === 0
              ? "Start building your brand or publisher property with visual block customization and instant subdomain hosting."
              : "Try adjusting your search query to locate your website."}
          </p>
          {projects.length === 0 && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setIsCreateOpen(true)}
            >
              Build Your First Website
            </button>
          )}
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: "1.25rem",
          }}
          data-testid="website-cards-grid"
        >
          {filteredProjects.map((p) => {
            const isPublished = p.status === 1;
            const updatedDate = new Date(p.updated_at).toLocaleDateString();

            return (
              <div
                key={p.id}
                className="card"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  transition: "transform 0.15s ease, box-shadow 0.15s ease",
                  padding: "1.25rem",
                }}
                data-testid={`website-card-${p.id}`}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
                    <span
                      className={`wb-badge ${isPublished ? "wb-badge-published" : "wb-badge-draft"}`}
                    >
                      {(WebsiteStatusMap[p.status] || "draft").toUpperCase()}
                    </span>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      Updated {updatedDate}
                    </span>
                  </div>

                  <h3 style={{ fontSize: "1.125rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                    {p.name}
                  </h3>

                  <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)", marginBottom: "0.75rem" }}>
                    Subdomain: <code style={{ color: "var(--primary)" }}>{p.slug}</code>
                  </div>

                  {isPublished && p.published_url && (
                    <div style={{ fontSize: "0.75rem", marginBottom: "0.75rem" }}>
                      <a
                        href={p.published_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--success)", textDecoration: "underline", display: "inline-flex", alignItems: "center", gap: "0.25rem" }}
                      >
                        Visit Live Site ↗
                      </a>
                    </div>
                  )}
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    borderTop: "1px solid var(--border)",
                    paddingTop: "0.875rem",
                    marginTop: "1rem",
                  }}
                >
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setProjectToDelete(p)}
                    style={{ fontSize: "0.75rem", color: "var(--error)", padding: "0.3rem 0.6rem" }}
                    data-testid={`delete-website-btn-${p.id}`}
                  >
                    Delete
                  </button>

                  <Link
                    to={`${basePath}/${p.id}`}
                    className="btn btn-primary btn-sm"
                    style={{ fontSize: "0.8125rem", textDecoration: "none" }}
                    data-testid={`open-editor-btn-${p.id}`}
                  >
                    Open Editor →
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create Website Modal */}
      {isCreateOpen && (
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
          data-testid="create-website-modal"
        >
          <div
            className="card"
            style={{
              width: "100%",
              maxWidth: "780px",
              maxHeight: "90vh",
              overflowY: "auto",
              padding: "1.75rem",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
              <div>
                <h2 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>Create New Website</h2>
                <p style={{ margin: "0.25rem 0 0", fontSize: "0.8125rem", color: "var(--text-muted)" }}>
                  Choose a brand name, subdomain slug, and starting layout template.
                </p>
              </div>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setIsCreateOpen(false)}
                disabled={isSubmitting}
                style={{ fontSize: "0.875rem", padding: "0.25rem 0.5rem" }}
              >
                ✕
              </button>
            </div>

            {createError && (
              <div
                style={{
                  padding: "0.75rem",
                  borderRadius: "var(--radius-sm)",
                  backgroundColor: "rgba(239, 68, 68, 0.1)",
                  border: "1px solid rgba(239, 68, 68, 0.3)",
                  color: "var(--error)",
                  fontSize: "0.8125rem",
                  marginBottom: "1rem",
                }}
              >
                {createError}
              </div>
            )}

            <form onSubmit={handleCreateSubmit} className="space-y-4">
              <div>
                <label className="form-label">
                  Website Name <span style={{ color: "var(--error)" }}>*</span>
                </label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Apex Studio"
                  value={createName}
                  maxLength={255}
                  required
                  onChange={(e) => handleNameChange(e.target.value)}
                  data-testid="create-site-name-input"
                />
              </div>

              <div>
                <label className="form-label">
                  Subdomain Slug <span style={{ color: "var(--error)" }}>*</span>
                </label>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="apex-studio"
                    value={createSlug}
                    maxLength={63}
                    required
                    onChange={(e) => {
                      const next = e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "");
                      setCreateSlug(next);
                      if (next.length >= 3) {
                        checkSlugAvailability(next);
                      } else {
                        setSlugStatus(null);
                      }
                    }}
                    onBlur={() => {
                      if (createSlug.length >= 3) checkSlugAvailability(createSlug);
                    }}
                    data-testid="create-site-slug-input"
                  />
                  <span style={{ fontSize: "0.8125rem", color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                    .platform.domain
                  </span>
                </div>
                {isCheckingSlug ? (
                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                    Checking availability...
                  </div>
                ) : slugStatus ? (
                  <div
                    style={{
                      fontSize: "0.75rem",
                      color: slugStatus.available ? "var(--success)" : "var(--error)",
                      marginTop: "0.25rem",
                    }}
                  >
                    {slugStatus.message}
                  </div>
                ) : null}
              </div>

              {/* Template Picker (Phase 2 Catalog) */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                  <label className="form-label" style={{ margin: 0 }}>
                    Select Starting Template ({filteredTemplates.length})
                  </label>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    {isLoadingTemplates ? "Loading catalog..." : "15 production templates"}
                  </span>
                </div>

                {/* Category Filter Tabs */}
                <div
                  style={{
                    display: "flex",
                    gap: "0.35rem",
                    overflowX: "auto",
                    paddingBottom: "0.5rem",
                    marginBottom: "0.75rem",
                  }}
                  data-testid="template-category-tabs"
                >
                  {TEMPLATE_CATEGORIES.map((cat) => (
                    <button
                      key={cat}
                      type="button"
                      className={`btn btn-sm ${selectedCategory === cat ? "btn-primary" : "btn-secondary"}`}
                      style={{
                        fontSize: "0.75rem",
                        padding: "0.2rem 0.65rem",
                        whiteSpace: "nowrap",
                        borderRadius: "999px",
                      }}
                      onClick={() => setSelectedCategory(cat)}
                      data-testid={`category-tab-${cat}`}
                    >
                      {cat}
                    </button>
                  ))}
                </div>

                {/* Template Search Filter */}
                <div style={{ marginBottom: "0.75rem" }}>
                  <input
                    type="text"
                    className="form-input"
                    style={{ fontSize: "0.8125rem", padding: "0.35rem 0.65rem" }}
                    placeholder="Search templates by name, keyword, or features..."
                    value={templateSearch}
                    onChange={(e) => setTemplateSearch(e.target.value)}
                    data-testid="template-search-input"
                  />
                </div>

                {/* Template Cards Grid */}
                <div
                  style={{
                    maxHeight: "300px",
                    overflowY: "auto",
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                    gap: "0.75rem",
                    padding: "2px",
                  }}
                  data-testid="template-catalog-grid"
                >
                  {isLoadingTemplates ? (
                    <div style={{ gridColumn: "1 / -1", padding: "2rem", textAlign: "center", color: "var(--text-muted)" }}>
                      Loading templates catalog...
                    </div>
                  ) : filteredTemplates.length === 0 ? (
                    <div style={{ gridColumn: "1 / -1", padding: "2rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.875rem" }}>
                      No templates match your search filter.
                    </div>
                  ) : (
                    filteredTemplates.map((tmpl) => {
                      const isSelected = selectedTemplate === tmpl.id;
                      return (
                        <div
                          key={tmpl.id}
                          onClick={() => setSelectedTemplate(tmpl.id)}
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            borderRadius: "var(--radius-md)",
                            border: "1.5px solid",
                            borderColor: isSelected ? "var(--primary)" : "var(--border)",
                            backgroundColor: isSelected ? "rgba(37, 99, 235, 0.04)" : "var(--bg-card)",
                            boxShadow: isSelected ? "0 0 0 1px var(--primary)" : "none",
                            cursor: "pointer",
                            transition: "all 0.15s ease",
                            overflow: "hidden",
                          }}
                          data-testid={`template-option-${tmpl.id}`}
                        >
                          {/* Thumbnail / Header */}
                          <div
                            style={{
                              height: "85px",
                              backgroundColor: "#f1f5f9",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              position: "relative",
                              overflow: "hidden",
                            }}
                          >
                            {tmpl.thumbnail_url ? (
                              <img
                                src={tmpl.thumbnail_url}
                                alt={tmpl.name}
                                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                              />
                            ) : (
                              <span style={{ fontSize: "2rem" }}>🎨</span>
                            )}
                            {tmpl.badge && (
                              <span
                                style={{
                                  position: "absolute",
                                  top: "6px",
                                  right: "6px",
                                  fontSize: "0.625rem",
                                  padding: "2px 6px",
                                  borderRadius: "999px",
                                  backgroundColor: "#fef3c7",
                                  color: "#92400e",
                                  fontWeight: 700,
                                }}
                              >
                                {tmpl.badge}
                              </span>
                            )}
                          </div>

                          {/* Card Content */}
                          <div style={{ padding: "0.75rem", flex: 1, display: "flex", flexDirection: "column" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.25rem", marginBottom: "0.25rem" }}>
                              <h4 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 700 }}>
                                {tmpl.name}
                              </h4>
                              <input
                                type="radio"
                                name="template_id"
                                checked={isSelected}
                                onChange={() => setSelectedTemplate(tmpl.id)}
                                style={{ marginTop: "2px" }}
                              />
                            </div>
                            <span style={{ fontSize: "0.6875rem", color: "var(--primary)", fontWeight: 600, marginBottom: "0.35rem" }}>
                              {tmpl.category}
                            </span>
                            <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)", margin: "0 0 0.75rem", lineHeight: 1.4, flex: 1 }}>
                              {tmpl.description}
                            </p>

                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              style={{ width: "100%", fontSize: "0.6875rem", padding: "0.25rem" }}
                              onClick={(e) => {
                                e.stopPropagation();
                                setPreviewTemplate(tmpl);
                              }}
                              data-testid={`preview-template-${tmpl.id}`}
                            >
                              👁 Preview Layout
                            </button>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", paddingTop: "0.75rem" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsCreateOpen(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSubmitting || (slugStatus !== null && !slugStatus.available)}
                  data-testid="submit-create-website-btn"
                >
                  {isSubmitting ? "Creating Website..." : "Create & Launch Editor"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {projectToDelete && (
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
          data-testid="delete-project-modal"
        >
          <div
            className="card"
            style={{
              width: "100%",
              maxWidth: "480px",
              padding: "1.5rem",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
            }}
          >
            <h3 style={{ fontSize: "1.125rem", fontWeight: 700, margin: "0 0 0.5rem", color: "var(--error)" }}>
              Delete Website Project
            </h3>
            <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "0 0 1.25rem" }}>
              Are you sure you want to delete <strong>{projectToDelete.name}</strong> (<code>{projectToDelete.slug}</code>)?
              This will archive the project and take any published pages offline.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setProjectToDelete(null)}
                disabled={isDeleting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleDeleteProject}
                disabled={isDeleting}
                style={{ background: "var(--error)", borderColor: "var(--error)" }}
                data-testid="confirm-delete-project-btn"
              >
                {isDeleting ? "Deleting..." : "Confirm Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Template Preview Modal (Phase 2) */}
      <TemplatePreviewModal
        isOpen={previewTemplate !== null}
        templateSummary={previewTemplate}
        onClose={() => setPreviewTemplate(null)}
        onSelectTemplate={(id) => setSelectedTemplate(id)}
      />
    </div>
  );
};
