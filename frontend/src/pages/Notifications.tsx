import React, { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  getNotificationPreferences,
  getSafeInternalLink,
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  updateNotificationPreferences,
} from "../api/client";
import {
  CATEGORY_METADATA,
  formatCategoryLabel,
  formatRelativeTime,
} from "../lib/notificationFormatters";
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreferenceItem,
} from "../types";

const ALL_CATEGORIES: NotificationCategory[] = [
  "billing",
  "campaign",
  "creative",
  "optimization",
  "domain",
  "security",
  "publisher",
  "system",
  "webhook",
];

export default function Notifications() {
  const [searchParams, setSearchParams] = useSearchParams();
  const currentTab = searchParams.get("tab") === "preferences" ? "preferences" : "notifications";

  // Feed State
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [page, setPage] = useState<number>(1);
  const [pageSize] = useState<number>(10);
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [unreadOnly, setUnreadOnly] = useState<boolean>(false);
  const [unreadCount, setUnreadCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [isMarkingAll, setIsMarkingAll] = useState<boolean>(false);

  // Preferences State
  const [preferences, setPreferences] = useState<Record<string, { in_app: boolean; email: boolean }>>({});
  const [initialPreferences, setInitialPreferences] = useState<Record<string, { in_app: boolean; email: boolean }>>({});
  const [isPrefLoading, setIsPrefLoading] = useState<boolean>(false);
  const [isPrefSaving, setIsPrefSaving] = useState<boolean>(false);
  const [prefError, setPrefError] = useState<string | null>(null);
  const [prefSuccess, setPrefSuccess] = useState<string | null>(null);

  // 1. Fetch Notification Feed
  const fetchFeed = useCallback(async (p: number, cat: string, unread: boolean) => {
    setIsLoading(true);
    setFeedError(null);
    try {
      const [listRes, countRes] = await Promise.all([
        listNotifications({
          page: p,
          page_size: pageSize,
          category: cat || undefined,
          unread_only: unread,
        }),
        getUnreadNotificationCount(),
      ]);

      setNotifications(listRes.items);
      setTotal(listRes.total);
      setTotalPages(listRes.total_pages || 1);
      setPage(listRes.page);
      setUnreadCount(countRes.unread_count);
    } catch (err: any) {
      setFeedError(err?.message || "Failed to load notifications");
    } finally {
      setIsLoading(false);
    }
  }, [pageSize]);

  useEffect(() => {
    if (currentTab === "notifications") {
      void fetchFeed(page, categoryFilter, unreadOnly);
    }
  }, [fetchFeed, page, categoryFilter, unreadOnly, currentTab]);

  // 2. Fetch Preferences
  const fetchPreferences = useCallback(async () => {
    setIsPrefLoading(true);
    setPrefError(null);
    try {
      const res = await getNotificationPreferences();
      const map: Record<string, { in_app: boolean; email: boolean }> = {};

      ALL_CATEGORIES.forEach((cat) => {
        map[cat] = { in_app: true, email: true };
      });

      res.preferences.forEach((p) => {
        if (!map[p.category]) {
          map[p.category] = { in_app: true, email: true };
        }
        if (p.channel === "in_app") {
          map[p.category].in_app = p.is_enabled;
        } else if (p.channel === "email") {
          map[p.category].email = p.is_enabled;
        }
      });

      // Security is always mandatory and enabled
      if (map["security"]) {
        map["security"].in_app = true;
        map["security"].email = true;
      }

      setPreferences(map);
      setInitialPreferences(JSON.parse(JSON.stringify(map)));
    } catch (err: any) {
      setPrefError(err?.message || "Failed to load notification preferences");
    } finally {
      setIsPrefLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentTab === "preferences") {
      void fetchPreferences();
    }
  }, [fetchPreferences, currentTab]);

  // Tab switching
  const handleTabChange = (tab: "notifications" | "preferences") => {
    setSearchParams({ tab });
  };

  // Mark single notification read
  const handleMarkRead = async (id: number) => {
    try {
      const updated = await markNotificationRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, ...updated, is_read: true } : n))
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch (err: any) {
      setFeedError(err?.message || "Failed to mark notification as read");
    }
  };

  // Mark all read
  const handleMarkAllRead = async () => {
    setIsMarkingAll(true);
    setFeedError(null);
    try {
      await markAllNotificationsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      setUnreadCount(0);
      void fetchFeed(page, categoryFilter, unreadOnly);
    } catch (err: any) {
      setFeedError(err?.message || "Failed to mark all as read");
    } finally {
      setIsMarkingAll(false);
    }
  };

  // Preference Toggle
  const handlePrefToggle = (cat: string, channel: "in_app" | "email") => {
    if (cat === "security") return; // Security is strictly mandatory

    setPreferences((prev) => ({
      ...prev,
      [cat]: {
        ...prev[cat],
        [channel]: !prev[cat]?.[channel],
      },
    }));
    setPrefSuccess(null);
  };

  // Save Preferences
  const handleSavePreferences = async () => {
    setIsPrefSaving(true);
    setPrefError(null);
    setPrefSuccess(null);

    const items: NotificationPreferenceItem[] = [];
    Object.entries(preferences).forEach(([category, channels]) => {
      // Do not attempt to disable security
      if (category !== "security") {
        items.push({ channel: "in_app", category, is_enabled: channels.in_app });
        items.push({ channel: "email", category, is_enabled: channels.email });
      }
    });

    try {
      await updateNotificationPreferences(items);
      setInitialPreferences(JSON.parse(JSON.stringify(preferences)));
      setPrefSuccess("Notification preferences saved successfully.");
    } catch (err: any) {
      setPrefError(err?.message || "Failed to save preferences");
    } finally {
      setIsPrefSaving(false);
    }
  };

  const hasUnsavedChanges = JSON.stringify(preferences) !== JSON.stringify(initialPreferences);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h1>Notification Center</h1>
          <div style={{ color: "#64748b", fontSize: "14px" }}>
            Stay informed with platform events, campaigns, billing, and optimization alerts.
          </div>
        </div>

        {unreadCount > 0 && (
          <span className="badge active" style={{ fontSize: "13px", padding: "4px 12px" }}>
            {unreadCount} Unread
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="notification-page-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={currentTab === "notifications"}
          className={`notification-page-tab ${currentTab === "notifications" ? "active" : ""}`}
          onClick={() => handleTabChange("notifications")}
        >
          All Notifications {unreadCount > 0 && `(${unreadCount})`}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={currentTab === "preferences"}
          className={`notification-page-tab ${currentTab === "preferences" ? "active" : ""}`}
          onClick={() => handleTabChange("preferences")}
        >
          Notification Preferences
        </button>
      </div>

      {/* TAB 1: NOTIFICATIONS FEED */}
      {currentTab === "notifications" && (
        <div>
          {/* Toolbar */}
          <div className="notification-toolbar">
            <div className="notification-toolbar-group">
              <label htmlFor="cat-filter" style={{ margin: 0, fontWeight: 600 }}>
                Category:
              </label>
              <select
                id="cat-filter"
                value={categoryFilter}
                onChange={(e) => {
                  setCategoryFilter(e.target.value);
                  setPage(1);
                }}
                style={{ width: "auto", minWidth: "180px" }}
              >
                <option value="">All Categories</option>
                {ALL_CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>
                    {formatCategoryLabel(cat)}
                  </option>
                ))}
              </select>

              <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer", margin: 0 }}>
                <input
                  type="checkbox"
                  checked={unreadOnly}
                  onChange={(e) => {
                    setUnreadOnly(e.target.checked);
                    setPage(1);
                  }}
                  style={{ width: "auto" }}
                />
                <span>Unread only</span>
              </label>
            </div>

            <div className="notification-toolbar-group">
              <button
                type="button"
                className="btn secondary"
                onClick={() => fetchFeed(page, categoryFilter, unreadOnly)}
                disabled={isLoading}
              >
                Refresh
              </button>
              <button
                type="button"
                className="btn"
                onClick={handleMarkAllRead}
                disabled={unreadCount === 0 || isMarkingAll}
              >
                {isMarkingAll ? "Marking..." : "Mark All as Read"}
              </button>
            </div>
          </div>

          {feedError && (
            <div className="card" style={{ background: "#fef2f2", borderColor: "#fecaca", color: "#991b1b" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>{feedError}</span>
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={() => fetchFeed(page, categoryFilter, unreadOnly)}
                >
                  Retry
                </button>
              </div>
            </div>
          )}

          {/* List Area */}
          {isLoading && (
            <div>
              <div className="skeleton-box" style={{ height: "80px", marginBottom: "12px" }} />
              <div className="skeleton-box" style={{ height: "80px", marginBottom: "12px" }} />
              <div className="skeleton-box" style={{ height: "80px", marginBottom: "12px" }} />
            </div>
          )}

          {!isLoading && notifications.length === 0 && (
            <div className="card notification-empty-state">
              <div className="notification-empty-icon" aria-hidden="true">🔔</div>
              <h3>No Notifications Found</h3>
              <p>
                {unreadOnly
                  ? "You have zero unread notifications. All caught up!"
                  : "No notifications recorded matching your current filter criteria."}
              </p>
            </div>
          )}

          {!isLoading &&
            notifications.map((n) => {
              const safeLink = getSafeInternalLink(n.link);
              return (
                <div key={n.id} className={`notification-full-card ${n.is_read ? "" : "is-unread"}`}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {!n.is_read && <span className="notification-unread-dot" aria-label="Unread" />}
                      <span style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a" }}>{n.title}</span>
                    </div>
                    <span className="notification-time">{formatRelativeTime(n.created_at)}</span>
                  </div>

                  <p style={{ margin: "8px 0 12px", color: "#334155", fontSize: "14px", lineHeight: "1.5" }}>
                    {n.message}
                  </p>

                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span className="badge category-tag">{formatCategoryLabel(n.category)}</span>
                      <span className={`badge severity-${n.severity}`}>{n.severity}</span>
                      {n.resource_type && n.resource_id && (
                        <span style={{ fontSize: "12px", color: "#64748b" }}>
                          {n.resource_type}: <code>{n.resource_id}</code>
                        </span>
                      )}
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {safeLink && (
                        <a
                          href={safeLink}
                          className="btn secondary small"
                          onClick={() => {
                            if (!n.is_read) handleMarkRead(n.id);
                          }}
                        >
                          View Resource →
                        </a>
                      )}
                      {!n.is_read && (
                        <button
                          type="button"
                          className="btn secondary small"
                          onClick={() => handleMarkRead(n.id)}
                          aria-label={`Mark notification ${n.id} as read`}
                        >
                          Mark as Read
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

          {/* Pagination */}
          {!isLoading && total > 0 && (
            <div className="notification-pagination">
              <div>
                Showing page <strong>{page}</strong> of <strong>{totalPages}</strong> ({total} total notifications)
              </div>
              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  type="button"
                  className="btn secondary small"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  ← Previous
                </button>
                <button
                  type="button"
                  className="btn secondary small"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next →
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TAB 2: PREFERENCES */}
      {currentTab === "preferences" && (
        <div className="card">
          <div style={{ marginBottom: "20px" }}>
            <h2>Delivery Channels & Subscriptions</h2>
            <p style={{ color: "#64748b", fontSize: "14px", margin: 0 }}>
              Control which categories trigger in-app center alerts and transactional email notices.
              Critical account security notices are mandatory and cannot be disabled.
            </p>
          </div>

          {prefError && (
            <div className="card" style={{ background: "#fef2f2", borderColor: "#fecaca", color: "#991b1b", marginBottom: "16px" }}>
              {prefError}
            </div>
          )}

          {prefSuccess && (
            <div className="card" style={{ background: "#f0fdf4", borderColor: "#bbf7d0", color: "#166534", marginBottom: "16px" }}>
              {prefSuccess}
            </div>
          )}

          {isPrefLoading ? (
            <div>
              <div className="skeleton-box" style={{ height: "60px", marginBottom: "10px" }} />
              <div className="skeleton-box" style={{ height: "60px", marginBottom: "10px" }} />
              <div className="skeleton-box" style={{ height: "60px", marginBottom: "10px" }} />
            </div>
          ) : (
            <div>
              <table className="pref-table" aria-label="Notification Preferences">
                <thead>
                  <tr>
                    <th style={{ width: "50%" }}>Category</th>
                    <th style={{ textAlign: "center", width: "25%" }}>In-App Notifications</th>
                    <th style={{ textAlign: "center", width: "25%" }}>Email Delivery</th>
                  </tr>
                </thead>
                <tbody>
                  {ALL_CATEGORIES.map((cat) => {
                    const meta = CATEGORY_METADATA[cat];
                    const isSecurity = cat === "security";
                    const inAppChecked = isSecurity ? true : preferences[cat]?.in_app ?? true;
                    const emailChecked = isSecurity ? true : preferences[cat]?.email ?? true;

                    return (
                      <tr key={cat}>
                        <td>
                          <div className="pref-category-title">
                            <span aria-hidden="true" style={{ marginRight: "6px" }}>{meta?.icon || "📌"}</span>
                            {meta?.label || formatCategoryLabel(cat)}
                            {isSecurity && (
                              <span className="badge mandatory-tag" style={{ marginLeft: "8px" }}>
                                Mandatory
                              </span>
                            )}
                          </div>
                          <div className="pref-category-desc">{meta?.description}</div>
                        </td>

                        <td style={{ textAlign: "center" }}>
                          <label className="pref-toggle-label">
                            <input
                              type="checkbox"
                              checked={inAppChecked}
                              disabled={isSecurity || isPrefSaving}
                              onChange={() => handlePrefToggle(cat, "in_app")}
                              aria-label={`In-App alerts for ${meta?.label || cat}`}
                            />
                            <span>{inAppChecked ? "Enabled" : "Disabled"}</span>
                          </label>
                        </td>

                        <td style={{ textAlign: "center" }}>
                          <label className="pref-toggle-label">
                            <input
                              type="checkbox"
                              checked={emailChecked}
                              disabled={isSecurity || isPrefSaving}
                              onChange={() => handlePrefToggle(cat, "email")}
                              aria-label={`Email notices for ${meta?.label || cat}`}
                            />
                            <span>{emailChecked ? "Enabled" : "Disabled"}</span>
                          </label>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              <div className="pref-actions-bar">
                <button
                  type="button"
                  className="btn secondary"
                  onClick={fetchPreferences}
                  disabled={!hasUnsavedChanges || isPrefSaving}
                >
                  Reset
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={handleSavePreferences}
                  disabled={!hasUnsavedChanges || isPrefSaving}
                >
                  {isPrefSaving ? "Saving..." : "Save Preferences"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
