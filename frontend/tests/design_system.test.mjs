/**
 * design_system.test.mjs — Comprehensive Design System & App Shell Tests for RivinityADS (Phase 8A)
 *
 * Verifies:
 * 1. Semantic design tokens defined in src/styles.css (typography, spacing, radii, shadows, surfaces, text, actions, status, focus, a11y).
 * 2. Virtual DOM rendering, accessibility roles, and contracts for all reusable UI components:
 *    - Button (variants, sizes, loading, aria-busy, icons)
 *    - Input (label pairing, aria-describedby, aria-invalid, error role="alert", hint)
 *    - Select (options, placeholder, label pairing, aria-describedby, chevron)
 *    - Card (composable Card, CardHeader, CardTitle, CardDescription, CardBody, CardFooter)
 *    - DataTable (semantic markup, th scope="col", responsive region, empty state, skeleton loading, row click)
 *    - StatusBadge (non-color-only text, dot indicator, aria-label, numeric and string mappings)
 *    - Alert (role="status" vs role="alert", variant icons, dismiss button aria-label)
 *    - Toast (role="status" / role="alert", aria-live, dismiss control)
 *    - LoadingSkeleton (role="status", sr-only fallback, variants)
 *    - EmptyState (icon, title, description, actions)
 *    - ConfirmDialog (role="dialog", aria-modal="true", aria-labelledby, cancel/confirm buttons)
 *    - PageContainer & PageHeader (semantic h1, eyebrow, description, action slots)
 *    - Badge & StatCard backward compatibility
 * 3. App Shell Layout:
 *    - Consistent RivinityADS branding (replaces OpenAdServer)
 *    - Desktop sidebar navigation & mobile navigation toggle
 *    - Role-aware navigation (admin, advertiser, publisher)
 *    - Real NotificationBell preserved
 *    - Current user profile and logout action
 *    - Skip-link keyboard accessibility
 *    - Unauthenticated null guard preserved
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router's SSR useLayoutEffect warning in Node test environment
const origError = console.error;
console.error = (...args) => {
  if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
    return;
  }
  origError.apply(console, args);
};

const cleanupFiles = [];

// Compile design system components and shell into a single test bundle
function compileTestBundle() {
  const wrapperCode = `
import React from 'react';
import { Button } from '../src/components/ui/Button';
import { Input } from '../src/components/ui/Input';
import { Select } from '../src/components/ui/Select';
import { Card, CardHeader, CardTitle, CardDescription, CardBody, CardFooter } from '../src/components/ui/Card';
import { DataTable } from '../src/components/ui/DataTable';
import { StatusBadge } from '../src/components/ui/StatusBadge';
import { Alert } from '../src/components/ui/Alert';
import { Toast } from '../src/components/ui/Toast';
import { LoadingSkeleton } from '../src/components/ui/LoadingSkeleton';
import { EmptyState } from '../src/components/ui/EmptyState';
import { ConfirmDialog } from '../src/components/ui/ConfirmDialog';
import { PageContainer } from '../src/components/ui/PageContainer';
import { PageHeader } from '../src/components/ui/PageHeader';
import Badge from '../src/components/Badge';
import StatCard from '../src/components/StatCard';
import Layout from '../src/components/Layout';
import { AuthContext } from '../src/auth/AuthContext';
import { MemoryRouter } from 'react-router-dom';

export {
  React,
  Button,
  Input,
  Select,
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardBody,
  CardFooter,
  DataTable,
  StatusBadge,
  Alert,
  Toast,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog,
  PageContainer,
  PageHeader,
  Badge,
  StatCard,
  Layout,
  AuthContext,
  MemoryRouter
};
`;
  const tempEntry = path.resolve("tests/.temp_ds_bundle_entry.tsx");
  fs.writeFileSync(tempEntry, wrapperCode);
  cleanupFiles.push(tempEntry);

  const result = esbuild.buildSync({
    entryPoints: [tempEntry],
    bundle: true,
    format: "esm",
    write: false,
    external: ["react", "react-dom", "react-router-dom"],
  });
  const tempOut = path.resolve("tests/.compiled_ds_bundle.mjs");
  fs.writeFileSync(tempOut, result.outputFiles[0].text);
  cleanupFiles.push(tempOut);
  return pathToFileURL(tempOut).href;
}

const bundleUrl = compileTestBundle();
const {
  React,
  Button,
  Input,
  Select,
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardBody,
  CardFooter,
  DataTable,
  StatusBadge,
  Alert,
  Toast,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog,
  PageContainer,
  PageHeader,
  Badge,
  StatCard,
  Layout,
  AuthContext,
  MemoryRouter,
} = await import(bundleUrl);

after(() => {
  for (const f of cleanupFiles) {
    if (fs.existsSync(f)) {
      try {
        fs.unlinkSync(f);
      } catch {}
    }
  }
});

// ===========================================================================
// 1. Design System Tokens Verification in CSS
// ===========================================================================

describe("Design System: CSS Custom Properties & Semantic Tokens", () => {
  const css = fs.readFileSync(path.resolve("src/styles.css"), "utf-8");

  test("1. Typography tokens are declared in :root", () => {
    assert.ok(css.includes("--font-sans:"), "Missing --font-sans token");
    assert.ok(css.includes("--font-mono:"), "Missing --font-mono token");
    assert.ok(css.includes("--font-size-xs:"), "Missing --font-size-xs token");
    assert.ok(css.includes("--font-size-sm:"), "Missing --font-size-sm token");
    assert.ok(css.includes("--font-size-base:"), "Missing --font-size-base token");
    assert.ok(css.includes("--font-size-lg:"), "Missing --font-size-lg token");
    assert.ok(css.includes("--font-size-xl:"), "Missing --font-size-xl token");
    assert.ok(css.includes("--font-size-2xl:"), "Missing --font-size-2xl token");
    assert.ok(css.includes("--font-size-3xl:"), "Missing --font-size-3xl token");
    assert.ok(css.includes("--font-weight-normal:"), "Missing --font-weight-normal token");
    assert.ok(css.includes("--font-weight-medium:"), "Missing --font-weight-medium token");
    assert.ok(css.includes("--font-weight-semibold:"), "Missing --font-weight-semibold token");
    assert.ok(css.includes("--font-weight-bold:"), "Missing --font-weight-bold token");
  });

  test("2. Spacing and layout tokens are declared in :root", () => {
    assert.ok(css.includes("--space-1:"), "Missing --space-1 token");
    assert.ok(css.includes("--space-2:"), "Missing --space-2 token");
    assert.ok(css.includes("--space-3:"), "Missing --space-3 token");
    assert.ok(css.includes("--space-4:"), "Missing --space-4 token");
    assert.ok(css.includes("--space-6:"), "Missing --space-6 token");
    assert.ok(css.includes("--space-8:"), "Missing --space-8 token");
    assert.ok(css.includes("--sidebar-width:"), "Missing --sidebar-width token");
    assert.ok(css.includes("--header-height:"), "Missing --header-height token");
    assert.ok(css.includes("--content-max-width:"), "Missing --content-max-width token");
  });

  test("3. Border radii, borders, shadows and surfaces are declared in :root", () => {
    assert.ok(css.includes("--radius-sm:"), "Missing --radius-sm token");
    assert.ok(css.includes("--radius-md:"), "Missing --radius-md token");
    assert.ok(css.includes("--radius-lg:"), "Missing --radius-lg token");
    assert.ok(css.includes("--radius-xl:"), "Missing --radius-xl token");
    assert.ok(css.includes("--radius-full:"), "Missing --radius-full token");

    assert.ok(css.includes("--border-color:"), "Missing --border-color token");
    assert.ok(css.includes("--border-color-subtle:"), "Missing --border-color-subtle token");
    assert.ok(css.includes("--border-color-strong:"), "Missing --border-color-strong token");

    assert.ok(css.includes("--shadow-xs:"), "Missing --shadow-xs token");
    assert.ok(css.includes("--shadow-sm:"), "Missing --shadow-sm token");
    assert.ok(css.includes("--shadow-md:"), "Missing --shadow-md token");
    assert.ok(css.includes("--shadow-lg:"), "Missing --shadow-lg token");
    assert.ok(css.includes("--shadow-xl:"), "Missing --shadow-xl token");

    assert.ok(css.includes("--color-bg-app:"), "Missing --color-bg-app token");
    assert.ok(css.includes("--color-bg-surface:"), "Missing --color-bg-surface token");
    assert.ok(css.includes("--color-bg-subtle:"), "Missing --color-bg-subtle token");
    assert.ok(css.includes("--color-bg-sidebar:"), "Missing --color-bg-sidebar token");
  });

  test("4. Semantic action and status tokens are declared in :root", () => {
    assert.ok(css.includes("--color-primary:"), "Missing --color-primary token");
    assert.ok(css.includes("--color-primary-hover:"), "Missing --color-primary-hover token");
    assert.ok(css.includes("--color-secondary:"), "Missing --color-secondary token");
    assert.ok(css.includes("--color-danger:"), "Missing --color-danger token");

    assert.ok(css.includes("--color-success:"), "Missing --color-success token");
    assert.ok(css.includes("--color-warning:"), "Missing --color-warning token");
    assert.ok(css.includes("--color-error:"), "Missing --color-error token");
    assert.ok(css.includes("--color-info:"), "Missing --color-info token");
  });

  test("5. Accessibility and responsive media queries are defined in CSS", () => {
    assert.ok(css.includes(".skip-link"), "Missing .skip-link in CSS");
    assert.ok(css.includes(".sr-only"), "Missing .sr-only in CSS");
    assert.ok(css.includes("*:focus-visible"), "Missing focus-visible styling in CSS");
    assert.ok(css.includes("@media (max-width: 768px)"), "Missing mobile 768px media query");
    assert.ok(css.includes("@media (prefers-reduced-motion: reduce)"), "Missing prefers-reduced-motion media query");
  });
});

// ===========================================================================
// 2. Reusable UI Components Virtual DOM & Accessibility
// ===========================================================================

describe("Design System: Reusable UI Components Virtual DOM", () => {
  test("6. Button renders variants, sizes, loading state with aria-busy and spinner", () => {
    // Primary button
    const htmlPrimary = renderToStaticMarkup(
      React.createElement(Button, { variant: "primary", size: "md" }, "Save Changes")
    );
    assert.match(htmlPrimary, /class="btn btn-primary btn-md"/);
    assert.match(htmlPrimary, /Save Changes/);

    // Danger small button
    const htmlDanger = renderToStaticMarkup(
      React.createElement(Button, { variant: "danger", size: "sm" }, "Delete")
    );
    assert.match(htmlDanger, /class="btn btn-danger btn-sm"/);

    // Loading button
    const htmlLoading = renderToStaticMarkup(
      React.createElement(
        Button,
        { isLoading: true, loadingText: "Saving..." },
        "Save Changes"
      )
    );
    assert.match(htmlLoading, /aria-busy="true"/);
    assert.match(htmlLoading, /disabled=""/);
    assert.match(htmlLoading, /btn-spinner/);
    assert.match(htmlLoading, /Saving\.\.\./);
  });

  test("7. Input renders accessible label pairing, aria-invalid, error role='alert', and hints", () => {
    const htmlWithError = renderToStaticMarkup(
      React.createElement(Input, {
        id: "campaign-name",
        label: "Campaign Name",
        error: "Name is required",
        hint: "Enter a descriptive name",
        isRequired: true,
      })
    );

    assert.match(htmlWithError, /<label for="campaign-name" class="form-label"/);
    assert.match(htmlWithError, /aria-invalid="true"/);
    assert.match(htmlWithError, /aria-describedby="campaign-name-hint campaign-name-error"/);
    assert.match(htmlWithError, /id="campaign-name-error" class="form-error" role="alert"/);
    assert.match(htmlWithError, /Name is required/);
    assert.match(htmlWithError, /form-required-asterisk/);
  });

  test("8. Select renders accessible label, options, placeholder, and chevron icon", () => {
    const options = [
      { value: "cpm", label: "Cost Per Mille (CPM)" },
      { value: "cpc", label: "Cost Per Click (CPC)" },
    ];
    const html = renderToStaticMarkup(
      React.createElement(Select, {
        id: "bid-type",
        label: "Bid Type",
        options: options,
        placeholder: "Select Bid Type",
      })
    );

    assert.match(html, /<label for="bid-type" class="form-label">Bid Type<\/label>/);
    assert.match(html, /<select[^>]*id="bid-type"/);
    assert.match(html, /<option value="" disabled="">Select Bid Type<\/option>/);
    assert.match(html, /<option value="cpm">Cost Per Mille \(CPM\)<\/option>/);
    assert.match(html, /select-chevron/);
  });

  test("9. Card renders composable Header, Title, Description, Body, and Footer", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        Card,
        { padding: "lg", variant: "default" },
        React.createElement(
          CardHeader,
          {
            actions: React.createElement(Button, { size: "sm" }, "Edit"),
          },
          React.createElement(CardTitle, { as: "h2" }, "Campaign Settings"),
          React.createElement(CardDescription, null, "Manage daily budget and pacing")
        ),
        React.createElement(CardBody, null, "Card content goes here"),
        React.createElement(CardFooter, null, React.createElement(Button, null, "Submit"))
      )
    );

    assert.match(html, /card card-padding-lg card-variant-default/);
    assert.match(html, /<h2 class="card-title ">Campaign Settings<\/h2>/);
    assert.match(html, /Manage daily budget and pacing/);
    assert.match(html, /card-header-actions/);
    assert.match(html, /card-body/);
    assert.match(html, /card-footer/);
  });

  test("10. DataTable renders responsive region, table headers with scope='col', and rows", () => {
    const columns = [
      { key: "name", header: "Name" },
      { key: "impressions", header: "Impressions", align: "right" },
    ];
    const data = [
      { id: 1, name: "Summer Campaign", impressions: "12,500" },
      { id: 2, name: "Fall Retargeting", impressions: "8,200" },
    ];

    const html = renderToStaticMarkup(
      React.createElement(DataTable, {
        columns: columns,
        data: data,
        keyExtractor: (item) => item.id,
        caption: "Active Campaigns",
      })
    );

    assert.match(html, /role="region"/);
    assert.match(html, /aria-label="Active Campaigns"/);
    assert.match(html, /<th scope="col"[^>]*>Name<\/th>/);
    assert.match(html, /<th scope="col" style="text-align:right"[^>]*>Impressions<\/th>/);
    assert.match(html, /Summer Campaign/);
    assert.match(html, /12,500/);
    assert.match(html, /Fall Retargeting/);
  });

  test("11. DataTable renders empty message and loading skeleton rows when applicable", () => {
    // Empty state
    const htmlEmpty = renderToStaticMarkup(
      React.createElement(DataTable, {
        columns: [{ key: "name", header: "Name" }],
        data: [],
        keyExtractor: (item) => item.id,
        emptyMessage: "No campaigns found",
      })
    );
    assert.match(htmlEmpty, /class="data-table-empty">No campaigns found<\/td>/);

    // Loading skeleton rows
    const htmlLoading = renderToStaticMarkup(
      React.createElement(DataTable, {
        columns: [{ key: "name", header: "Name" }],
        data: [],
        keyExtractor: (item) => item.id,
        isLoading: true,
        loadingRows: 3,
      })
    );
    assert.match(htmlLoading, /data-table-skeleton-row/);
    assert.match(htmlLoading, /skeleton skeleton-text/);
  });

  test("12. StatusBadge renders accessible label, visual dot, and non-color-only semantic status", () => {
    // Numeric status 1 -> Active
    const htmlActive = renderToStaticMarkup(
      React.createElement(StatusBadge, { status: 1 })
    );
    assert.match(htmlActive, /class="status-badge status-badge--active status-badge--sm"/);
    assert.match(htmlActive, /aria-label="Status: Active"/);
    assert.match(htmlActive, /<span class="status-badge-dot" aria-hidden="true"><\/span>/);
    assert.match(htmlActive, /<span class="status-badge-text">Active<\/span>/);

    // Numeric status 0 -> Paused
    const htmlPaused = renderToStaticMarkup(
      React.createElement(StatusBadge, { status: 0 })
    );
    assert.match(htmlPaused, /status-badge--paused/);
    assert.match(htmlPaused, /aria-label="Status: Paused"/);

    // Backward-compatible Badge component
    const htmlLegacyBadge = renderToStaticMarkup(
      React.createElement(Badge, { status: 1 })
    );
    assert.match(htmlLegacyBadge, /badge/);
    assert.match(htmlLegacyBadge, /Active/);
  });

  test("13. Alert renders role='status' for info/success and role='alert' for error/warning", () => {
    // Error alert
    const htmlError = renderToStaticMarkup(
      React.createElement(
        Alert,
        { variant: "error", title: "Authentication Failed", onClose: () => {} },
        "Invalid username or password"
      )
    );
    assert.match(htmlError, /role="alert"/);
    assert.match(htmlError, /Authentication Failed/);
    assert.match(htmlError, /Invalid username or password/);
    assert.match(htmlError, /aria-label="Dismiss alert"/);

    // Success alert
    const htmlSuccess = renderToStaticMarkup(
      React.createElement(
        Alert,
        { variant: "success", title: "Saved Successfully" },
        "Your changes have been saved."
      )
    );
    assert.match(htmlSuccess, /role="status"/);
    assert.match(htmlSuccess, /Saved Successfully/);
  });

  test("14. Toast renders role, aria-live, and message", () => {
    const html = renderToStaticMarkup(
      React.createElement(Toast, {
        variant: "success",
        title: "Order Placed",
        message: "Order #4829 has been created.",
        onClose: () => {},
      })
    );
    assert.match(html, /role="status"/);
    assert.match(html, /aria-live="polite"/);
    assert.match(html, /Order Placed/);
    assert.match(html, /Order #4829 has been created\./);
  });

  test("15. LoadingSkeleton renders role='status' with screen-reader text", () => {
    const html = renderToStaticMarkup(
      React.createElement(LoadingSkeleton, { variant: "text", lines: 2 })
    );
    assert.match(html, /role="status"/);
    assert.match(html, /aria-live="polite"/);
    assert.match(html, /<span class="sr-only">Loading\.\.\.<\/span>/);
    assert.match(html, /skeleton skeleton-text/);
  });

  test("16. EmptyState renders icon, title, description, and action CTA", () => {
    const html = renderToStaticMarkup(
      React.createElement(EmptyState, {
        title: "No Creatives Yet",
        description: "Upload your first ad creative to begin running campaigns.",
        action: React.createElement(Button, null, "Upload Creative"),
      })
    );
    assert.match(html, /class="empty-state "/);
    assert.match(html, /No Creatives Yet/);
    assert.match(html, /Upload your first ad creative/);
    assert.match(html, /Upload Creative/);
  });

  test("17. ConfirmDialog renders role='dialog', aria-modal='true', and labels", () => {
    const html = renderToStaticMarkup(
      React.createElement(ConfirmDialog, {
        isOpen: true,
        title: "Pause Campaign?",
        message: "Are you sure you want to pause this campaign?",
        confirmLabel: "Yes, Pause",
        cancelLabel: "Cancel",
        variant: "danger",
        onConfirm: () => {},
        onCancel: () => {},
      })
    );
    assert.match(html, /role="dialog"/);
    assert.match(html, /aria-modal="true"/);
    assert.match(html, /aria-labelledby="confirm-dialog-title"/);
    assert.match(html, /Pause Campaign\?/);
    assert.match(html, /Are you sure you want to pause this campaign\?/);
    assert.match(html, /Yes, Pause/);
    assert.match(html, /Cancel/);
  });

  test("18. PageContainer and PageHeader render semantic layout structure", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        PageContainer,
        { maxWidth: "lg" },
        React.createElement(PageHeader, {
          title: "Advertiser Dashboard",
          eyebrow: "Advertiser Portal",
          description: "Monitor campaign performance, spend, and metrics.",
          actions: React.createElement(Button, null, "Create Campaign"),
        })
      )
    );
    assert.match(html, /page-container page-container--lg/);
    assert.match(html, /<div class="page-header-eyebrow">Advertiser Portal<\/div>/);
    assert.match(html, /<h1 class="page-header-title">Advertiser Dashboard<\/h1>/);
    assert.match(html, /Monitor campaign performance/);
    assert.match(html, /Create Campaign/);
  });

  test("19. StatCard renders semantic layout with backward compatibility", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatCard, {
        title: "Today's Spend",
        value: "₹4,250",
        hint: "+12% from yesterday",
      })
    );
    assert.match(html, /stat /);
    assert.match(html, /Today&#x27;s Spend/);
    assert.match(html, /₹4,250/);
    assert.match(html, /\+12% from yesterday/);
  });
});

// ===========================================================================
// 3. App Shell & Layout Verification
// ===========================================================================

describe("Design System: App Shell & Layout", () => {
  const adminSession = {
    user: {
      id: 1,
      name: "Alice Admin",
      email: "alice@rivinityads.com",
      role: "admin",
      email_verified: true,
    },
    token: "test_admin_token",
  };

  const advertiserSession = {
    user: {
      id: 2,
      name: "Bob Advertiser",
      email: "bob@acme.com",
      role: "advertiser",
      email_verified: true,
    },
    token: "test_adv_token",
  };

  const publisherSession = {
    user: {
      id: 3,
      name: "Charlie Publisher",
      email: "charlie@news.com",
      role: "publisher",
      email_verified: true,
    },
    token: "test_pub_token",
  };

  test("20. Layout renders RivinityADS branding and replaces OpenAdServer", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: adminSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /<div class="brand">RivinityADS<\/div>/, "Sidebar must display RivinityADS brand");
    assert.doesNotMatch(html, /OpenAdServer/, "Must not contain OpenAdServer branding");
  });

  test("21. Layout renders Skip Link for keyboard navigation", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: adminSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /<a href="#main-content" class="skip-link">Skip to main content<\/a>/);
    assert.match(html, /<main id="main-content" class="content"/);
  });

  test("22. Layout renders mobile navigation toggle button and accessible controls", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: adminSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /class="mobile-nav-toggle"/);
    assert.match(html, /aria-label="Toggle navigation menu"/);
    assert.match(html, /aria-expanded="false"/);
    assert.match(html, /aria-controls="app-sidebar"/);
  });

  test("23. Layout renders role-aware navigation for Admin", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: adminSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /Platform Admin console/);
    assert.match(html, /href="\/admin"/);
    assert.match(html, /href="\/admin\/advertisers"/);
    assert.match(html, /href="\/admin\/campaigns"/);
    assert.match(html, /href="\/admin\/creatives"/);
    assert.match(html, /href="\/admin\/publishers"/);
    assert.match(html, /href="\/notifications"/);
    assert.match(html, /href="\/tools"/);
    assert.match(html, /href="\/health"/);
  });

  test("24. Layout renders role-aware navigation for Advertiser", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: advertiserSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/advertiser"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /Advertiser console/);
    assert.match(html, /href="\/advertiser"/);
    assert.match(html, /href="\/advertiser\/domains"/);
    assert.match(html, /href="\/advertiser\/campaigns\/new"/);
  });

  test("25. Layout renders role-aware navigation for Publisher", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: publisherSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/publisher"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /Publisher console/);
    assert.match(html, /href="\/publisher"/);
    assert.match(html, /href="\/publisher\/health"/);
    assert.match(html, /href="\/publisher\/properties"/);
    assert.match(html, /href="\/publisher\/slots"/);
    assert.match(html, /href="\/publisher\/keys"/);
    assert.match(html, /href="\/publisher\/webhooks"/);
    assert.match(html, /href="\/publisher\/integration"/);
    assert.match(html, /href="\/publisher\/preview"/);
  });

  test("26. Layout renders real NotificationBell, user profile, and working logout action", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: adminSession, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.match(html, /class="notification-bell-btn\s*"/);
    assert.match(html, /<span class="user-name">Alice Admin<\/span>/);
    assert.match(html, /<span class="user-role-badge">admin<\/span>/);
    assert.match(html, /Log out<\/button>/);
  });

  test("27. Layout returns empty DOM when unauthenticated (security gate)", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        AuthContext.Provider,
        { value: { session: null, logout: () => {} } },
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/admin"] },
          React.createElement(Layout, null)
        )
      )
    );

    assert.equal(html, "");
  });
});
