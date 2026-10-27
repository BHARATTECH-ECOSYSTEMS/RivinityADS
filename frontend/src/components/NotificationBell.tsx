import React, { useEffect, useRef, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  getSafeInternalLink,
} from "../api/client";
import { formatCategoryLabel, formatRelativeTime } from "../lib/notificationFormatters";
import type { NotificationItem } from "../types";

export default function NotificationBell() {
  const [isOpen, setIsOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState<number>(0);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isMarkingAll, setIsMarkingAll] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // 1. Initial unread count fetch on mount
  const refreshUnreadCount = useCallback(async () => {
    try {
      const res = await getUnreadNotificationCount();
      setUnreadCount(res.unread_count);
    } catch {
      // Graceful fallback for unread badge
    }
  }, []);

  useEffect(() => {
    void refreshUnreadCount();
  }, [refreshUnreadCount]);

  // 2. Fetch recent notifications when opening popover
  const loadRecentNotifications = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [listRes, countRes] = await Promise.all([
        listNotifications({ page: 1, page_size: 5 }),
        getUnreadNotificationCount(),
      ]);
      setNotifications(listRes.items);
      setUnreadCount(countRes.unread_count);
    } catch (err: any) {
      setError(err?.message || "Failed to load notifications");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleToggle = () => {
    const nextState = !isOpen;
    setIsOpen(nextState);
    if (nextState) {
      void loadRecentNotifications();
    }
  };

  // 3. Close popover on click outside or Escape
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  // 4. Mark single notification as read
  const handleMarkRead = async (id: number, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      const updated = await markNotificationRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, ...updated, is_read: true } : n))
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch {
      // Failed to mark read; keep state intact
    }
  };

  // 5. Mark all as read
  const handleMarkAllRead = async () => {
    setIsMarkingAll(true);
    try {
      await markAllNotificationsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      setUnreadCount(0);
    } catch {
      // Fail safely
    } finally {
      setIsMarkingAll(false);
    }
  };

  // 6. Navigate to internal resource safely
  const handleNotificationClick = async (notif: NotificationItem) => {
    if (!notif.is_read) {
      void handleMarkRead(notif.id);
    }
    const safeLink = getSafeInternalLink(notif.link);
    setIsOpen(false);
    if (safeLink) {
      navigate(safeLink);
    } else {
      navigate("/notifications");
    }
  };

  return (
    <div className="notification-bell-container" ref={containerRef}>
      <button
        type="button"
        className={`notification-bell-btn ${isOpen ? "active" : ""}`}
        onClick={handleToggle}
        aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : "Notifications"}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>

        {unreadCount > 0 && (
          <span className="notification-badge-count" aria-hidden="true">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          className="notification-popover"
          role="dialog"
          aria-label="Notification center"
          aria-modal="false"
        >
          <div className="notification-popover-header">
            <div className="notification-popover-title">
              <span>Notifications</span>
              {unreadCount > 0 && (
                <span className="badge active">{unreadCount} new</span>
              )}
            </div>
            {unreadCount > 0 && (
              <button
                type="button"
                className="btn secondary small"
                onClick={handleMarkAllRead}
                disabled={isMarkingAll}
                aria-label="Mark all notifications as read"
              >
                {isMarkingAll ? "Marking..." : "Mark all read"}
              </button>
            )}
          </div>

          <div className="notification-popover-body">
            {isLoading && (
              <div style={{ padding: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
                <div className="skeleton-box" style={{ height: "48px" }} />
                <div className="skeleton-box" style={{ height: "48px" }} />
                <div className="skeleton-box" style={{ height: "48px" }} />
              </div>
            )}

            {!isLoading && error && (
              <div style={{ padding: "20px", textAlign: "center" }}>
                <p className="error" style={{ marginBottom: "10px" }}>{error}</p>
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={loadRecentNotifications}
                >
                  Retry
                </button>
              </div>
            )}

            {!isLoading && !error && notifications.length === 0 && (
              <div className="notification-empty-state">
                <div className="notification-empty-icon" aria-hidden="true">🔔</div>
                <div style={{ fontWeight: 600, color: "#0f172a" }}>No notifications yet</div>
                <div style={{ fontSize: "12px", marginTop: "4px" }}>
                  You're all caught up on platform activity!
                </div>
              </div>
            )}

            {!isLoading && !error && notifications.length > 0 && (
              notifications.map((item) => (
                <div
                  key={item.id}
                  className={`notification-popover-item ${item.is_read ? "" : "is-unread"}`}
                  onClick={() => handleNotificationClick(item)}
                  style={{ cursor: "pointer" }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      handleNotificationClick(item);
                    }
                  }}
                  aria-label={`${item.is_read ? "Read" : "Unread"} notification: ${item.title}`}
                >
                  <div className="notification-item-header">
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      {!item.is_read && <span className="notification-unread-dot" aria-label="Unread" />}
                      <span className="notification-item-title">{item.title}</span>
                    </div>
                    <span className="notification-time">
                      {formatRelativeTime(item.created_at)}
                    </span>
                  </div>

                  <div className="notification-item-msg">{item.message}</div>

                  <div className="notification-item-actions">
                    <span className={`badge category-tag`}>
                      {formatCategoryLabel(item.category)}
                    </span>
                    <span className={`badge severity-${item.severity}`}>
                      {item.severity}
                    </span>
                    {!item.is_read && (
                      <button
                        type="button"
                        className="btn secondary small"
                        style={{ marginLeft: "auto", padding: "2px 8px", fontSize: "11px" }}
                        onClick={(e) => handleMarkRead(item.id, e)}
                        aria-label={`Mark ${item.title} as read`}
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="notification-popover-footer">
            <Link
              to="/notifications"
              onClick={() => setIsOpen(false)}
            >
              View all notifications →
            </Link>
            <Link
              to="/notifications?tab=preferences"
              onClick={() => setIsOpen(false)}
              style={{ color: "#64748b" }}
            >
              Preferences
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
