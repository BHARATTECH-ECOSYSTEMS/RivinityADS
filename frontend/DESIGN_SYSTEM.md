# RivinityADS Frontend Design System & Component Guide (Phase 8A)

This document specifies the design tokens, reusable components, and architectural conventions established in **Phase 8A** of RivinityADS (`openadserver-frontend`).

---

## 1. Design Philosophy

RivinityADS is a professional, high-performance AdTech SaaS platform. The UI follows these core design principles:

- **Information Hierarchy**: Compact, data-dense layouts without unnecessary ornamentation or visual clutter.
- **Consistent Semantic Tokens**: Color, spacing, borders, shadows, and typography are defined via CSS custom properties (`:root`).
- **Accessible by Default**: WCAG 2.1 AA compliant. All interactive controls feature visible `:focus-visible` styling, screen reader utilities (`.sr-only`), keyboard activation (`Enter`/`Space`), and status indicators that do not rely on color alone.
- **Responsive Layouts**: Desktop navigation utilizes a left-side persistent sidebar; mobile viewports (`<= 768px`) gracefully transition to a responsive sliding drawer with backdrop and hamburger toggle.
- **Motion Restraint**: Animations strictly respect `prefers-reduced-motion: reduce`.

---

## 2. Design Tokens (`src/styles.css`)

All design tokens are defined in `:root` and prefixed with semantic categories:

### 2.1 Typography
```css
--font-sans: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;

--font-size-xs: 0.75rem;     /* 12px - Badges, captions, helper hints */
--font-size-sm: 0.875rem;    /* 14px - Body text, buttons, table cells */
--font-size-base: 1rem;      /* 16px - Card titles, form labels, emphasized text */
--font-size-lg: 1.125rem;    /* 18px - Section headers, brand logo */
--font-size-xl: 1.25rem;     /* 20px - Sub-headings */
--font-size-2xl: 1.5rem;     /* 24px - Page titles (H1), primary stats */
--font-size-3xl: 1.875rem;   /* 30px - Large metrics */

--font-weight-normal: 400;
--font-weight-medium: 500;
--font-weight-semibold: 600;
--font-weight-bold: 700;
```

### 2.2 Spacing & Layout
```css
--space-1: 0.25rem;  /* 4px */
--space-2: 0.5rem;   /* 8px */
--space-3: 0.75rem;  /* 12px */
--space-4: 1rem;     /* 16px */
--space-5: 1.25rem;  /* 20px */
--space-6: 1.5rem;   /* 24px */
--space-8: 2rem;     /* 32px */
--space-10: 2.5rem;  /* 40px */
--space-12: 3rem;    /* 48px */

--sidebar-width: 240px;
--header-height: 56px;
--content-max-width: 1200px;
```

### 2.3 Surfaces, Borders & Shadows
```css
--radius-sm: 4px;    /* Chips, code snippets */
--radius-md: 6px;    /* Inputs, selects, small buttons */
--radius-lg: 8px;    /* Standard buttons, dropdowns, alerts */
--radius-xl: 12px;   /* Cards, modal dialogs */
--radius-full: 9999px; /* Badges, pill indicators, avatars */

--border-color: #e2e8f0;        /* Slate-200 */
--border-color-subtle: #f1f5f9; /* Slate-100 */
--border-color-strong: #cbd5e1; /* Slate-300 */

--shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.05);
--shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -1px rgba(0, 0, 0, 0.04);
--shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.03);
--shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
```

### 2.4 Semantic Actions & Status
```css
/* Actions */
--color-primary: #2563eb;       /* Primary blue */
--color-primary-hover: #1d4ed8;
--color-secondary: #f1f5f9;     /* Subtle gray */
--color-danger: #dc2626;        /* Destructive red */

/* Status States */
--color-success: #16a34a;       /* Active / OK */
--color-warning: #d97706;       /* Pending / Warning */
--color-error: #dc2626;         /* Paused / Error */
--color-info: #2563eb;          /* Informational */
```

---

## 3. Reusable UI Components (`src/components/ui/`)

### 3.1 Button
Located at [`src/components/ui/Button.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Button.tsx).

Variants: `primary`, `secondary`, `outline`, `ghost`, `danger`.
Sizes: `sm`, `md`, `lg`.

```tsx
import { Button } from "../components/ui";

<Button variant="primary" size="md" onClick={handleSave}>
  Save Campaign
</Button>

<Button variant="secondary" size="sm" icon={<EditIcon />}>
  Edit
</Button>

<Button variant="danger" isLoading={isDeleting} loadingText="Deleting...">
  Delete
</Button>
```

### 3.2 Form Controls: Input & Select
Located at [`src/components/ui/Input.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Input.tsx) and [`src/components/ui/Select.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Select.tsx).

Features built-in label pairing, `useId` automatic fallback, helper text (`hint`), and accessible validation error states (`aria-invalid="true"`, `role="alert"`).

```tsx
import { Input, Select } from "../components/ui";

<Input
  id="campaign-name"
  label="Campaign Name"
  placeholder="e.g. Q4 Black Friday Sale"
  hint="Must be between 3 and 100 characters."
  error={formErrors.name}
  isRequired
  value={name}
  onChange={(e) => setName(e.target.value)}
/>

<Select
  id="bid-type"
  label="Bidding Model"
  options={[
    { value: 1, label: "CPM (Cost Per Mille)" },
    { value: 2, label: "CPC (Cost Per Click)" },
    { value: 3, label: "CPA (Cost Per Action)" },
  ]}
  value={bidType}
  onChange={(e) => setBidType(Number(e.target.value))}
/>
```

### 3.3 Card System
Located at [`src/components/ui/Card.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Card.tsx).

Composable architecture with `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardBody`, and `CardFooter`.

```tsx
import { Card, CardHeader, CardTitle, CardDescription, CardBody, CardFooter, Button } from "../components/ui";

<Card padding="md">
  <CardHeader actions={<Button size="sm" variant="outline">Refresh</Button>}>
    <CardTitle as="h3">Audience Targeting</CardTitle>
    <CardDescription>Manage inclusion and exclusion targeting rules</CardDescription>
  </CardHeader>
  <CardBody>
    {/* Targeting content */}
  </CardBody>
  <CardFooter>
    <Button variant="primary">Save Changes</Button>
  </CardFooter>
</Card>
```

### 3.4 DataTable
Located at [`src/components/ui/DataTable.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/DataTable.tsx).

Accessible wrapper with horizontal overflow containment, `scope="col"` headers, loading skeleton rows, empty state messaging, and keyboard activation for clickable rows.

```tsx
import { DataTable } from "../components/ui";

const columns = [
  { key: "name", header: "Campaign" },
  { key: "status", header: "Status", render: (item) => <StatusBadge status={item.status} /> },
  { key: "budget", header: "Daily Budget", align: "right", render: (item) => money(item.budget_daily) },
];

<DataTable
  columns={columns}
  data={campaigns}
  keyExtractor={(c) => c.id}
  isLoading={loading}
  emptyMessage="No active campaigns found."
  onRowClick={(campaign) => navigate(`/campaigns/${campaign.id}`)}
/>
```

### 3.5 StatusBadge
Located at [`src/components/ui/StatusBadge.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/StatusBadge.tsx).

Ensures status is never conveyed by color alone: includes a semantic colored dot indicator, formatted title-cased text label, and an `aria-label`. Maps numbers (0 -> Paused, 1 -> Active) and strings (`active`, `paused`, `archived`, `pending`, `draft`, `error`, `success`).

```tsx
import { StatusBadge } from "../components/ui";

<StatusBadge status={1} />              {/* Active (Green) */}
<StatusBadge status={0} />              {/* Paused (Red) */}
<StatusBadge status="under_review" />   {/* Under Review (Amber) */}
```

### 3.6 Alert & Toast
Located at [`src/components/ui/Alert.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Alert.tsx) and [`src/components/ui/Toast.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/Toast.tsx).

Variants: `info`, `success`, `warning`, `error`. Automatically maps to `role="status"` or `role="alert"`.

```tsx
import { Alert } from "../components/ui";

<Alert variant="error" title="Payment Failed" onClose={() => clearError()}>
  Your wallet deposit could not be completed. Please try another payment method.
</Alert>
```

### 3.7 LoadingSkeleton & EmptyState
Located at [`src/components/ui/LoadingSkeleton.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/LoadingSkeleton.tsx) and [`src/components/ui/EmptyState.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/EmptyState.tsx).

Accessible placeholders with screen-reader text (`.sr-only`) and call-to-action empty states:

```tsx
import { LoadingSkeleton, EmptyState, Button } from "../components/ui";

{isLoading ? (
  <LoadingSkeleton variant="text" lines={3} />
) : items.length === 0 ? (
  <EmptyState
    title="No Ad Sets Created"
    description="Ad sets organize your targeting, budgets, and bid configurations."
    action={<Button variant="primary">Create Ad Set</Button>}
  />
) : (
  <ItemList items={items} />
)}
```

### 3.8 ConfirmDialog
Located at [`src/components/ui/ConfirmDialog.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/ui/ConfirmDialog.tsx).

Accessible modal dialog with `role="dialog"`, `aria-modal="true"`, Escape key dismissal, backdrop click closing, and focus management.

```tsx
import { ConfirmDialog } from "../components/ui";

<ConfirmDialog
  isOpen={isConfirmOpen}
  title="Archive Campaign?"
  message="Are you sure you want to archive this campaign? All active ad sets and creatives will cease delivery."
  confirmLabel="Archive"
  variant="danger"
  isLoading={isArchiving}
  onConfirm={handleArchive}
  onCancel={() => setIsConfirmOpen(false)}
/>
```

---

## 4. Application Shell Architecture (`Layout.tsx`)

Located at [`src/components/Layout.tsx`](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/frontend/src/components/Layout.tsx).

The application shell provides:
1. **Brand Identity**: Prominently displays "RivinityADS" across the sidebar and document title (`index.html`).
2. **Keyboard Skip Link**: Direct jump link to `#main-content` for keyboard accessibility.
3. **Role-Aware Navigation**:
   - **Admin**: Dashboard, Advertisers, Campaigns, Creative moderation, Publishers, Tools (Notifications, Ad Tester, System Health).
   - **Advertiser**: Dashboard, Domains, New Campaign, Tools.
   - **Publisher**: Dashboard, Placement Health, Properties, Ad Units, API Keys, Webhooks, Integration, Live Preview, Tools.
4. **Mobile Navigation**:
   - Hamburger toggle in compact topbar.
   - Sliding off-canvas drawer (`transform: translateX(0)`) on viewports `<= 768px`.
   - Backdrop overlay with blur.
   - Auto-closes on navigation and on `Escape` key press.
5. **Top Bar Context**:
   - Current role console indicator.
   - Live `NotificationBell` with unread count and popover.
   - User account name and role badge.
   - Secure Logout action.
6. **Authorization Guard**:
   - If `!session`, renders `null` immediately, preserving unauthenticated protection.

---

## 5. Backward Compatibility

All previous components and legacy class names remain 100% backward compatible:
- `Badge`: Re-exports `StatusBadge` while preserving `{ status: number }` signature and `.badge` styling.
- `StatCard`: Enhanced with design tokens, supporting `{ title, value, hint, icon }` while retaining `.stat`, `.label`, `.value`, and `.hint`.
- Legacy table, button, and notification classes continue to render seamlessly.
