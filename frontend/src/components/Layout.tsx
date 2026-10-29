import { useState, useEffect } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import VerificationBanner from "./VerificationBanner";
import NotificationBell from "./NotificationBell";
import type { Role } from "../types";

const NAV: Record<Role, { to: string; label: string }[]> = {
  admin: [
    { to: "/admin", label: "Dashboard" },
    { to: "/admin/advertisers", label: "Advertisers" },
    { to: "/admin/campaigns", label: "Campaigns" },
    { to: "/admin/creatives", label: "Creative moderation" },
    { to: "/admin/publishers", label: "Publishers" },
    { to: "/admin/websites", label: "Websites" },
    { to: "/admin/email-marketing", label: "Platform Marketing" },
    { to: "/admin/finance", label: "Financial reconciliation" },
  ],
  advertiser: [
    { to: "/advertiser", label: "Dashboard" },
    { to: "/advertiser/domains", label: "Domains" },
    { to: "/advertiser/campaigns/new", label: "New Campaign" },
    { to: "/advertiser/email-marketing", label: "Email Marketing" },
  ],
  publisher: [
    { to: "/publisher", label: "Dashboard" },
    { to: "/publisher/websites", label: "Websites" },
    { to: "/publisher/health", label: "Placement Health" },
    { to: "/publisher/properties", label: "Properties" },
    { to: "/publisher/slots", label: "Ad Units" },
    { to: "/publisher/keys", label: "API Keys" },
    { to: "/publisher/webhooks", label: "Webhooks" },
    { to: "/publisher/integration", label: "Integration" },
    { to: "/publisher/preview", label: "Live Preview" },
  ],
};

const SHARED = [
  { to: "/notifications", label: "Notifications" },
  { to: "/tools", label: "Ad Tester" },
  { to: "/health", label: "System Health" },
];

const ROLE_LABEL: Record<Role, string> = {
  admin: "Platform Admin",
  advertiser: "Advertiser",
  publisher: "Publisher",
};

export default function Layout() {
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  // Close mobile navigation drawer whenever route location changes
  useEffect(() => {
    setIsMobileNavOpen(false);
  }, [location.pathname]);

  // Handle Escape key to close mobile drawer
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isMobileNavOpen) {
        setIsMobileNavOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isMobileNavOpen]);

  if (!session) return null;
  const { user } = session;

  return (
    <div className="shell">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      {/* Mobile drawer backdrop */}
      {isMobileNavOpen && (
        <div
          className="mobile-nav-backdrop"
          onClick={() => setIsMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Main Sidebar Navigation */}
      <aside
        id="app-sidebar"
        className={`sidebar ${isMobileNavOpen ? "is-open" : ""}`}
        aria-label="Main Navigation"
      >
        <div className="sidebar-brand-wrapper">
          <div className="brand">RivinityADS</div>
          <button
            type="button"
            className="sidebar-close-btn"
            onClick={() => setIsMobileNavOpen(false)}
            aria-label="Close navigation menu"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <nav>
          {(NAV[user.role] || []).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/admin" || item.to === "/advertiser" || item.to === "/publisher"}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
          <div className="nav-section">Tools</div>
          {SHARED.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main Workspace Area */}
      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="mobile-nav-toggle"
              onClick={() => setIsMobileNavOpen((prev) => !prev)}
              aria-label="Toggle navigation menu"
              aria-expanded={isMobileNavOpen}
              aria-controls="app-sidebar"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <line x1="3" y1="6" x2="21" y2="6" strokeLinecap="round" />
                <line x1="3" y1="12" x2="21" y2="12" strokeLinecap="round" />
                <line x1="3" y1="18" x2="21" y2="18" strokeLinecap="round" />
              </svg>
            </button>
            <span className="muted console-tag">{(ROLE_LABEL[user.role] || user.role)} console</span>
          </div>

          <div className="actions topbar-actions">
            <NotificationBell />
            <div className="user-profile">
              <span className="user-name">{user.name}</span>
              <span className="user-role-badge">{user.role}</span>
            </div>
            <button
              type="button"
              className="btn secondary small logout-btn"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              Log out
            </button>
          </div>
        </header>

        <VerificationBanner />

        <main id="main-content" className="content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
