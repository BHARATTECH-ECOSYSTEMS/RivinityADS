import { useEffect, useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import {
  getMailingLists,
  deleteMailingList,
  getListContacts,
  deleteContact,
  getMarketingCampaigns,
} from "../../api/client";
import type {
  EmailMarketingCampaign,
  EmailMarketingContact,
  EmailMarketingList,
} from "../../types";

import CreateListModal from "./components/CreateListModal";
import AddContactModal from "./components/AddContactModal";
import CsvImportModal from "./components/CsvImportModal";
import CampaignEditorModal from "./components/CampaignEditorModal";
import CampaignPreviewModal from "./components/CampaignPreviewModal";
import TestSendModal from "./components/TestSendModal";
import LaunchConfirmationModal from "./components/LaunchConfirmationModal";
import CampaignAnalyticsModal from "./components/CampaignAnalyticsModal";

interface EmailMarketingProps {
  workspace?: "advertiser" | "platform";
}

export default function EmailMarketing({ workspace = "advertiser" }: EmailMarketingProps) {
  const { session } = useAuth();
  const [lists, setLists] = useState<EmailMarketingList[]>([]);
  const [campaigns, setCampaigns] = useState<EmailMarketingCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successToast, setSuccessToast] = useState<string | null>(null);

  // Tab state
  const [activeTab, setActiveTab] = useState<"overview" | "lists" | "campaigns">("overview");

  // Contact list drilldown state
  const [selectedList, setSelectedList] = useState<EmailMarketingList | null>(null);
  const [contacts, setContacts] = useState<EmailMarketingContact[]>([]);
  const [contactsLoading, setContactsLoading] = useState(false);
  const [contactSearchQuery, setContactSearchQuery] = useState("");
  const [contactOffset, setContactOffset] = useState(0);
  const CONTACT_PAGE_LIMIT = 25;

  // Modals state
  const [createListOpen, setCreateListOpen] = useState(false);
  const [addContactOpen, setAddContactOpen] = useState(false);
  const [csvImportOpen, setCsvImportOpen] = useState(false);
  const [campaignEditorOpen, setCampaignEditorOpen] = useState(false);
  const [editingCampaign, setEditingCampaign] = useState<EmailMarketingCampaign | null>(null);
  const [previewCampaign, setPreviewCampaign] = useState<EmailMarketingCampaign | null>(null);
  const [testSendCampaign, setTestSendCampaign] = useState<EmailMarketingCampaign | null>(null);
  const [launchCampaign, setLaunchCampaign] = useState<EmailMarketingCampaign | null>(null);
  const [analyticsCampaign, setAnalyticsCampaign] = useState<EmailMarketingCampaign | null>(null);

  const isPlatform = workspace === "platform" || session?.user.role === "admin";
  const title = isPlatform ? "Platform Marketing" : "Email Marketing";
  const subtitle = isPlatform
    ? "Manage platform-wide communications and administrative marketing campaigns."
    : "Manage your permission-based contact lists and email campaigns.";

  const showToast = (msg: string) => {
    setSuccessToast(msg);
    setTimeout(() => setSuccessToast(null), 4000);
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [listsData, campaignsData] = await Promise.all([
        getMailingLists(),
        getMarketingCampaigns(),
      ]);
      setLists(listsData);
      setCampaigns(campaignsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load email marketing data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [workspace]);

  // Load contacts for selected list
  const loadContacts = async (listId: number, query = "", offset = 0) => {
    setContactsLoading(true);
    try {
      const data = await getListContacts(listId, {
        limit: CONTACT_PAGE_LIMIT,
        offset,
        query: query.trim() || undefined,
      });
      setContacts(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load contacts.");
    } finally {
      setContactsLoading(false);
    }
  };

  const handleSelectDrilldownList = (list: EmailMarketingList) => {
    setSelectedList(list);
    setContactSearchQuery("");
    setContactOffset(0);
    void loadContacts(list.id, "", 0);
  };

  const handleSearchContacts = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedList) return;
    setContactOffset(0);
    void loadContacts(selectedList.id, contactSearchQuery, 0);
  };

  const handleDeleteList = async (list: EmailMarketingList) => {
    if (!window.confirm(`Are you sure you want to delete mailing list "${list.name}"? This action cannot be undone.`)) {
      return;
    }
    try {
      await deleteMailingList(list.id);
      showToast(`Mailing list "${list.name}" deleted successfully.`);
      if (selectedList?.id === list.id) {
        setSelectedList(null);
      }
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete list.");
    }
  };

  const handleDeleteContact = async (contact: EmailMarketingContact) => {
    if (!selectedList) return;
    if (!window.confirm(`Unsubscribe and remove contact ${contact.email}?`)) {
      return;
    }
    try {
      await deleteContact(selectedList.id, contact.id);
      showToast(`Contact ${contact.email} removed.`);
      void loadContacts(selectedList.id, contactSearchQuery, contactOffset);
      void loadData(); // refresh contact count on list
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete contact.");
    }
  };

  const totalContacts = lists.reduce((sum, item) => sum + (item.contact_count || 0), 0);
  const activeCampaignsCount = campaigns.filter((c) => c.status === "running" || c.status === "completed").length;

  return (
    <div className="email-marketing-page" data-testid="email-marketing-page">
      {/* Toast Notification */}
      {successToast && (
        <div
          role="status"
          style={{
            position: "fixed",
            top: "20px",
            right: "20px",
            background: "#15803d",
            color: "#ffffff",
            padding: "10px 16px",
            borderRadius: "6px",
            boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
            zIndex: 9999,
            fontSize: "13px",
            fontWeight: 500,
          }}
          data-testid="toast-success"
        >
          {successToast}
        </div>
      )}

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "12px" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <h1 style={{ margin: 0 }}>{title}</h1>
              <span
                className="badge"
                style={{
                  background: isPlatform ? "#4f46e5" : "#0284c7",
                  color: "#ffffff",
                  fontSize: "12px",
                  padding: "2px 8px",
                  borderRadius: "4px",
                }}
              >
                {isPlatform ? "Platform Workspace" : "Advertiser Workspace"}
              </span>
            </div>
            <p className="muted" style={{ margin: "4px 0 0" }}>{subtitle}</p>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              className="btn secondary small"
              onClick={() => void loadData()}
              disabled={loading}
              data-testid="refresh-btn"
            >
              {loading ? "Refreshing..." : "Refresh"}
            </button>
            <button
              type="button"
              className="btn primary small"
              onClick={() => {
                setEditingCampaign(null);
                setCampaignEditorOpen(true);
              }}
              data-testid="btn-create-campaign-header"
            >
              New Campaign
            </button>
          </div>
        </div>
      </div>

      {/* Acceptable Use Policy Banner */}
      <div
        className="card"
        style={{
          borderLeft: "4px solid #0284c7",
          backgroundColor: "rgba(2, 132, 199, 0.05)",
          marginBottom: "20px",
          padding: "14px 18px",
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: "4px", color: "#0369a1" }}>
          Permission-Based Marketing Policy
        </div>
        <p style={{ margin: 0, fontSize: "13px", color: "#334155", lineHeight: 1.5 }}>
          Only email recipients whose affirmative marketing consent has been documented.
          Prohibited sources (scraped, purchased, co-registration, or third-party broker lists) are strictly forbidden
          and rejected at ingestion. All outgoing marketing sends require explicit pre-flight confirmation and mandatory unsubscribe headers.
        </p>
      </div>

      {/* Error Alert */}
      {error && (
        <div
          role="alert"
          className="card"
          style={{
            borderLeft: "4px solid #ef4444",
            backgroundColor: "rgba(239, 68, 68, 0.05)",
            marginBottom: "20px",
            padding: "12px 16px",
            color: "#b91c1c",
          }}
          data-testid="error-alert"
        >
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Metrics Summary Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "16px",
          marginBottom: "24px",
        }}
      >
        <div className="card" style={{ padding: "16px" }}>
          <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Mailing Lists
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px" }} data-testid="metric-lists-count">
            {loading ? "..." : lists.length}
          </div>
        </div>

        <div className="card" style={{ padding: "16px" }}>
          <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Total Contacts
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px" }} data-testid="metric-contacts-count">
            {loading ? "..." : totalContacts}
          </div>
        </div>

        <div className="card" style={{ padding: "16px" }}>
          <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Total Campaigns
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px" }} data-testid="metric-campaigns-count">
            {loading ? "..." : campaigns.length}
          </div>
        </div>

        <div className="card" style={{ padding: "16px" }}>
          <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Launched / Sent
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px", color: "#16a34a" }} data-testid="metric-active-campaigns">
            {loading ? "..." : activeCampaignsCount}
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div style={{ display: "flex", gap: "8px", borderBottom: "1px solid #e2e8f0", marginBottom: "20px" }}>
        <button
          type="button"
          onClick={() => {
            setSelectedList(null);
            setActiveTab("overview");
          }}
          className={`btn ${activeTab === "overview" ? "primary" : "secondary"} small`}
          style={{ borderRadius: "4px 4px 0 0", borderBottom: activeTab === "overview" ? "2px solid #2563eb" : "none" }}
          data-testid="tab-overview"
        >
          Overview
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("lists")}
          className={`btn ${activeTab === "lists" ? "primary" : "secondary"} small`}
          style={{ borderRadius: "4px 4px 0 0", borderBottom: activeTab === "lists" ? "2px solid #2563eb" : "none" }}
          data-testid="tab-lists"
        >
          Contact Lists ({lists.length})
        </button>
        <button
          type="button"
          onClick={() => {
            setSelectedList(null);
            setActiveTab("campaigns");
          }}
          className={`btn ${activeTab === "campaigns" ? "primary" : "secondary"} small`}
          style={{ borderRadius: "4px 4px 0 0", borderBottom: activeTab === "campaigns" ? "2px solid #2563eb" : "none" }}
          data-testid="tab-campaigns"
        >
          Campaigns ({campaigns.length})
        </button>
      </div>

      {/* Tab Content */}
      {loading ? (
        <div className="card" style={{ padding: "40px", textAlign: "center" }}>
          <p className="muted" style={{ margin: 0 }}>Connecting to tenant email marketing service...</p>
        </div>
      ) : (
        <>
          {/* 1. OVERVIEW TAB */}
          {activeTab === "overview" && (
            <div className="card" style={{ padding: "20px" }} data-testid="view-overview">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                <h3 style={{ margin: 0 }}>Overview &amp; Quick Actions</h3>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button type="button" className="btn secondary small" onClick={() => setCreateListOpen(true)} data-testid="btn-overview-create-list">
                    + Add List
                  </button>
                  <button
                    type="button"
                    className="btn primary small"
                    onClick={() => {
                      setEditingCampaign(null);
                      setCampaignEditorOpen(true);
                    }}
                    data-testid="btn-overview-create-campaign"
                  >
                    + Draft Campaign
                  </button>
                </div>
              </div>

              <p style={{ color: "#475569", lineHeight: 1.6, marginTop: 0 }}>
                Manage audience segments, import consent-verified contacts, compose responsive campaigns,
                test against server-allowlisted recipients, and launch with enforced suppression checking.
              </p>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px", marginTop: "16px" }}>
                <div style={{ border: "1px solid #e2e8f0", padding: "14px", borderRadius: "6px" }}>
                  <strong style={{ fontSize: "14px" }}>Audience Health</strong>
                  <p style={{ margin: "6px 0", fontSize: "13px", color: "#64748b" }}>
                    {lists.length} lists containing {totalContacts} consented contacts.
                  </p>
                  <button
                    type="button"
                    className="btn secondary small"
                    style={{ marginTop: "6px" }}
                    onClick={() => setActiveTab("lists")}
                  >
                    Manage Lists →
                  </button>
                </div>

                <div style={{ border: "1px solid #e2e8f0", padding: "14px", borderRadius: "6px" }}>
                  <strong style={{ fontSize: "14px" }}>Campaign Activity</strong>
                  <p style={{ margin: "6px 0", fontSize: "13px", color: "#64748b" }}>
                    {campaigns.filter((c) => c.status === "draft").length} drafts, {activeCampaignsCount} launched/sent.
                  </p>
                  <button
                    type="button"
                    className="btn secondary small"
                    style={{ marginTop: "6px" }}
                    onClick={() => setActiveTab("campaigns")}
                  >
                    View Campaigns →
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 2. LISTS & CONTACTS TAB */}
          {activeTab === "lists" && (
            <div className="card" style={{ padding: "20px" }} data-testid="view-lists">
              {selectedList ? (
                /* Drilldown: Contacts View for Selected List */
                <div data-testid="contacts-drilldown-view">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
                    <div>
                      <button
                        type="button"
                        className="btn secondary small"
                        onClick={() => setSelectedList(null)}
                        style={{ marginRight: "10px" }}
                        data-testid="btn-back-to-lists"
                      >
                        ← Back to Lists
                      </button>
                      <h3 style={{ display: "inline", margin: 0 }}>
                        Contacts in "{selectedList.name}"
                      </h3>
                      <span className="badge" style={{ marginLeft: "8px" }}>{selectedList.type}</span>
                    </div>

                    <div style={{ display: "flex", gap: "8px" }}>
                      <button
                        type="button"
                        className="btn secondary small"
                        onClick={() => setAddContactOpen(true)}
                        data-testid="btn-add-contact"
                      >
                        + Add Contact
                      </button>
                      <button
                        type="button"
                        className="btn primary small"
                        onClick={() => setCsvImportOpen(true)}
                        data-testid="btn-import-csv"
                      >
                        Import CSV...
                      </button>
                    </div>
                  </div>

                  {/* Server-Side Contact Search Form */}
                  <form onSubmit={handleSearchContacts} style={{ display: "flex", gap: "8px", marginBottom: "16px" }}>
                    <input
                      type="text"
                      className="input"
                      placeholder="Search contacts by email or name..."
                      value={contactSearchQuery}
                      onChange={(e) => setContactSearchQuery(e.target.value)}
                      style={{ flex: 1, padding: "8px", boxSizing: "border-box" }}
                      data-testid="input-contact-search"
                    />
                    <button type="submit" className="btn secondary small" data-testid="btn-submit-search">
                      Search
                    </button>
                    {contactSearchQuery && (
                      <button
                        type="button"
                        className="btn secondary small"
                        onClick={() => {
                          setContactSearchQuery("");
                          setContactOffset(0);
                          void loadContacts(selectedList.id, "", 0);
                        }}
                      >
                        Clear
                      </button>
                    )}
                  </form>

                  {/* Contacts Table */}
                  {contactsLoading ? (
                    <div style={{ textAlign: "center", padding: "20px 0" }}>
                      <p className="muted">Loading contacts...</p>
                    </div>
                  ) : contacts.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "30px 0" }} data-testid="empty-contacts">
                      <p className="muted" style={{ margin: 0 }}>
                        {contactSearchQuery ? "No contacts match your search." : "No contacts in this list yet."}
                      </p>
                    </div>
                  ) : (
                    <>
                      <div style={{ overflowX: "auto" }}>
                        <table className="table" style={{ width: "100%", borderCollapse: "collapse" }}>
                          <thead>
                            <tr style={{ borderBottom: "1px solid #e2e8f0", textAlign: "left" }}>
                              <th style={{ padding: "8px" }}>ID</th>
                              <th style={{ padding: "8px" }}>Email</th>
                              <th style={{ padding: "8px" }}>Name</th>
                              <th style={{ padding: "8px" }}>Status</th>
                              <th style={{ padding: "8px" }}>Consent Source</th>
                              <th style={{ padding: "8px" }}>Created</th>
                              <th style={{ padding: "8px", textAlign: "right" }}>Actions</th>
                            </tr>
                          </thead>
                          <tbody>
                            {contacts.map((c) => (
                              <tr key={c.id} style={{ borderBottom: "1px solid #f1f5f9" }} data-testid={`contact-row-${c.id}`}>
                                <td style={{ padding: "8px" }}>#{c.id}</td>
                                <td style={{ padding: "8px", fontWeight: 600 }}>{c.email}</td>
                                <td style={{ padding: "8px" }}>{c.name || "-"}</td>
                                <td style={{ padding: "8px" }}>
                                  <span
                                    className="badge"
                                    style={{
                                      background: c.status === "subscribed" ? "#dcfce7" : "#fee2e2",
                                      color: c.status === "subscribed" ? "#15803d" : "#b91c1c",
                                    }}
                                  >
                                    {c.status}
                                  </span>
                                </td>
                                <td style={{ padding: "8px", fontSize: "12px", color: "#64748b" }}>{c.consent_source}</td>
                                <td style={{ padding: "8px", fontSize: "12px", color: "#64748b" }}>
                                  {new Date(c.created_at).toLocaleDateString()}
                                </td>
                                <td style={{ padding: "8px", textAlign: "right" }}>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    style={{ color: "#dc2626", borderColor: "#fca5a5" }}
                                    onClick={() => void handleDeleteContact(c)}
                                    data-testid={`btn-delete-contact-${c.id}`}
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {/* Pagination Controls */}
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "14px" }}>
                        <span className="muted" style={{ fontSize: "12px" }}>
                          Showing offset {contactOffset} (Page Limit: {CONTACT_PAGE_LIMIT})
                        </span>
                        <div style={{ display: "flex", gap: "8px" }}>
                          <button
                            type="button"
                            className="btn secondary small"
                            disabled={contactOffset === 0}
                            onClick={() => {
                              const newOffset = Math.max(0, contactOffset - CONTACT_PAGE_LIMIT);
                              setContactOffset(newOffset);
                              void loadContacts(selectedList.id, contactSearchQuery, newOffset);
                            }}
                            data-testid="btn-contacts-prev"
                          >
                            Previous
                          </button>
                          <button
                            type="button"
                            className="btn secondary small"
                            disabled={contacts.length < CONTACT_PAGE_LIMIT}
                            onClick={() => {
                              const newOffset = contactOffset + CONTACT_PAGE_LIMIT;
                              setContactOffset(newOffset);
                              void loadContacts(selectedList.id, contactSearchQuery, newOffset);
                            }}
                            data-testid="btn-contacts-next"
                          >
                            Next
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                /* Master: Lists Table */
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                    <h3 style={{ margin: 0 }}>Mailing Lists</h3>
                    <button
                      type="button"
                      className="btn primary small"
                      onClick={() => setCreateListOpen(true)}
                      data-testid="btn-create-list"
                    >
                      + Create List
                    </button>
                  </div>

                  {lists.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "30px 0" }} data-testid="empty-lists">
                      <p className="muted" style={{ margin: "0 0 10px" }}>No contact lists created yet.</p>
                      <button type="button" className="btn primary small" onClick={() => setCreateListOpen(true)}>
                        Create First List
                      </button>
                    </div>
                  ) : (
                    <div style={{ overflowX: "auto" }}>
                      <table className="table" style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead>
                          <tr style={{ borderBottom: "1px solid #e2e8f0", textAlign: "left" }}>
                            <th style={{ padding: "8px" }}>ID</th>
                            <th style={{ padding: "8px" }}>Name</th>
                            <th style={{ padding: "8px" }}>Type</th>
                            <th style={{ padding: "8px" }}>Opt-in</th>
                            <th style={{ padding: "8px" }}>Subscribers</th>
                            <th style={{ padding: "8px" }}>Created</th>
                            <th style={{ padding: "8px", textAlign: "right" }}>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {lists.map((l) => (
                            <tr key={l.id} style={{ borderBottom: "1px solid #f1f5f9" }} data-testid={`list-row-${l.id}`}>
                              <td style={{ padding: "8px" }}>#{l.id}</td>
                              <td style={{ padding: "8px", fontWeight: 600 }}>{l.name}</td>
                              <td style={{ padding: "8px" }}><span className="badge">{l.type}</span></td>
                              <td style={{ padding: "8px" }}>{l.optin}</td>
                              <td style={{ padding: "8px" }}>{l.contact_count}</td>
                              <td style={{ padding: "8px", color: "#64748b", fontSize: "12px" }}>
                                {new Date(l.created_at).toLocaleDateString()}
                              </td>
                              <td style={{ padding: "8px", textAlign: "right" }}>
                                <div style={{ display: "inline-flex", gap: "6px" }}>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    onClick={() => handleSelectDrilldownList(l)}
                                    data-testid={`btn-view-contacts-${l.id}`}
                                  >
                                    View Contacts
                                  </button>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    style={{ color: "#dc2626", borderColor: "#fca5a5" }}
                                    onClick={() => void handleDeleteList(l)}
                                    data-testid={`btn-delete-list-${l.id}`}
                                  >
                                    Delete
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 3. CAMPAIGNS TAB */}
          {activeTab === "campaigns" && (
            <div className="card" style={{ padding: "20px" }} data-testid="view-campaigns">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                <h3 style={{ margin: 0 }}>Marketing Campaigns</h3>
                <button
                  type="button"
                  className="btn primary small"
                  onClick={() => {
                    setEditingCampaign(null);
                    setCampaignEditorOpen(true);
                  }}
                  data-testid="btn-new-campaign"
                >
                  + New Campaign
                </button>
              </div>

              {campaigns.length === 0 ? (
                <div style={{ textAlign: "center", padding: "30px 0" }} data-testid="empty-campaigns">
                  <p className="muted" style={{ margin: "0 0 10px" }}>No marketing campaigns found.</p>
                  <button
                    type="button"
                    className="btn primary small"
                    onClick={() => {
                      setEditingCampaign(null);
                      setCampaignEditorOpen(true);
                    }}
                  >
                    Draft First Campaign
                  </button>
                </div>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table className="table" style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #e2e8f0", textAlign: "left" }}>
                        <th style={{ padding: "8px" }}>ID</th>
                        <th style={{ padding: "8px" }}>Campaign Name</th>
                        <th style={{ padding: "8px" }}>Subject</th>
                        <th style={{ padding: "8px" }}>Status</th>
                        <th style={{ padding: "8px" }}>Recipients</th>
                        <th style={{ padding: "8px" }}>Created</th>
                        <th style={{ padding: "8px", textAlign: "right" }}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {campaigns.map((c) => (
                        <tr key={c.id} style={{ borderBottom: "1px solid #f1f5f9" }} data-testid={`campaign-row-${c.id}`}>
                          <td style={{ padding: "8px" }}>#{c.id}</td>
                          <td style={{ padding: "8px", fontWeight: 600 }}>{c.name}</td>
                          <td style={{ padding: "8px" }}>{c.subject}</td>
                          <td style={{ padding: "8px" }}>
                            <span
                              className="badge"
                              style={{
                                background: c.status === "completed" || c.status === "running" ? "#dcfce7" : "#f1f5f9",
                                color: c.status === "completed" || c.status === "running" ? "#15803d" : "#475569",
                              }}
                            >
                              {c.status}
                            </span>
                          </td>
                          <td style={{ padding: "8px" }}>{c.total_recipients}</td>
                          <td style={{ padding: "8px", color: "#64748b", fontSize: "12px" }}>
                            {new Date(c.created_at).toLocaleDateString()}
                          </td>
                          <td style={{ padding: "8px", textAlign: "right" }}>
                            <div style={{ display: "inline-flex", gap: "6px" }}>
                              {c.status === "draft" ? (
                                <>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    onClick={() => {
                                      setEditingCampaign(c);
                                      setCampaignEditorOpen(true);
                                    }}
                                    data-testid={`btn-edit-camp-${c.id}`}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    onClick={() => setPreviewCampaign(c)}
                                    data-testid={`btn-preview-camp-${c.id}`}
                                  >
                                    Preview
                                  </button>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    onClick={() => setTestSendCampaign(c)}
                                    data-testid={`btn-testsend-camp-${c.id}`}
                                  >
                                    Test Send
                                  </button>
                                  <button
                                    type="button"
                                    className="btn primary small"
                                    onClick={() => setLaunchCampaign(c)}
                                    data-testid={`btn-launch-camp-${c.id}`}
                                  >
                                    Launch...
                                  </button>
                                </>
                              ) : (
                                <>
                                  <button
                                    type="button"
                                    className="btn secondary small"
                                    onClick={() => setPreviewCampaign(c)}
                                    data-testid={`btn-preview-camp-${c.id}`}
                                  >
                                    View Preview
                                  </button>
                                  <button
                                    type="button"
                                    className="btn primary small"
                                    onClick={() => setAnalyticsCampaign(c)}
                                    data-testid={`btn-analytics-camp-${c.id}`}
                                  >
                                    Analytics
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* ── Modals ───────────────────────────────────────────────────────────── */}

      {/* Create List Modal */}
      <CreateListModal
        isOpen={createListOpen}
        onClose={() => setCreateListOpen(false)}
        onCreated={(newList) => {
          showToast(`Mailing list "${newList.name}" created.`);
          void loadData();
        }}
      />

      {/* Add Contact Modal */}
      {selectedList && (
        <AddContactModal
          isOpen={addContactOpen}
          listId={selectedList.id}
          listName={selectedList.name}
          onClose={() => setAddContactOpen(false)}
          onAdded={(contact) => {
            showToast(`Contact ${contact.email} added successfully.`);
            void loadContacts(selectedList.id, contactSearchQuery, contactOffset);
            void loadData();
          }}
        />
      )}

      {/* CSV Import Modal */}
      {selectedList && (
        <CsvImportModal
          isOpen={csvImportOpen}
          listId={selectedList.id}
          listName={selectedList.name}
          onClose={() => setCsvImportOpen(false)}
          onImportComplete={(result) => {
            showToast(`CSV import completed: ${result.valid_count} contacts added.`);
            void loadContacts(selectedList.id, contactSearchQuery, contactOffset);
            void loadData();
          }}
        />
      )}

      {/* Campaign Editor Modal (New or Edit Draft) */}
      <CampaignEditorModal
        isOpen={campaignEditorOpen}
        lists={lists}
        campaign={editingCampaign}
        onClose={() => {
          setCampaignEditorOpen(false);
          setEditingCampaign(null);
        }}
        onSaved={(saved) => {
          showToast(`Campaign "${saved.name}" saved as draft.`);
          void loadData();
        }}
      />

      {/* Campaign Preview Modal */}
      <CampaignPreviewModal
        isOpen={Boolean(previewCampaign)}
        campaign={previewCampaign}
        onClose={() => setPreviewCampaign(null)}
        onOpenTestSend={() => {
          setTestSendCampaign(previewCampaign);
        }}
        onOpenLaunch={() => {
          setLaunchCampaign(previewCampaign);
        }}
      />

      {/* Test Send Modal */}
      <TestSendModal
        isOpen={Boolean(testSendCampaign)}
        campaign={testSendCampaign}
        onClose={() => setTestSendCampaign(null)}
        onSuccess={() => {
          showToast("Test email preview sent to allowlisted recipients.");
        }}
      />

      {/* Launch Confirmation Modal */}
      <LaunchConfirmationModal
        isOpen={Boolean(launchCampaign)}
        campaign={launchCampaign}
        targetListName={lists.find((l) => l.id === launchCampaign?.list_id)?.name}
        onClose={() => setLaunchCampaign(null)}
        onLaunched={(launched) => {
          showToast(`Campaign "${launched.name}" launched successfully!`);
          void loadData();
        }}
      />

      {/* Campaign Analytics Modal */}
      <CampaignAnalyticsModal
        isOpen={Boolean(analyticsCampaign)}
        campaign={analyticsCampaign}
        onClose={() => setAnalyticsCampaign(null)}
      />
    </div>
  );
}
