import React from "react";

export type StatusBadgeVariant =
  | "active"
  | "paused"
  | "archived"
  | "pending"
  | "draft"
  | "success"
  | "warning"
  | "error"
  | "info"
  | "default";

export interface StatusBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  status?: number | string;
  label?: string;
  variant?: StatusBadgeVariant;
  showDot?: boolean;
  size?: "sm" | "md";
  className?: string;
}

function resolveVariant(status?: number | string, explicitVariant?: StatusBadgeVariant): StatusBadgeVariant {
  if (explicitVariant) return explicitVariant;
  if (status === undefined || status === null) return "default";

  if (typeof status === "number") {
    if (status === 1) return "active";
    if (status === 0) return "paused";
    return "default";
  }

  const normalized = String(status).toLowerCase().trim();
  switch (normalized) {
    case "1":
    case "active":
    case "approved":
    case "running":
    case "enabled":
    case "verified":
    case "success":
      return "active";
    case "0":
    case "paused":
    case "disabled":
    case "suspended":
      return "paused";
    case "archived":
    case "deleted":
      return "archived";
    case "pending":
    case "under_review":
    case "review":
      return "pending";
    case "draft":
      return "draft";
    case "warning":
    case "at_risk":
      return "warning";
    case "error":
    case "rejected":
    case "failed":
      return "error";
    case "info":
      return "info";
    default:
      return "default";
  }
}

function resolveLabel(status?: number | string, explicitLabel?: string): string {
  if (explicitLabel) {
    const s = String(explicitLabel).trim();
    return s.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  if (status === undefined || status === null) return "Unknown";

  if (typeof status === "number") {
    if (status === 1) return "Active";
    if (status === 0) return "Paused";
    return `Status ${status}`;
  }

  const s = String(status).trim();
  if (!s) return "Unknown";
  // Capitalize first letter of each word/separator
  return s
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StatusBadge({
  status,
  label: customLabel,
  variant: explicitVariant,
  showDot = true,
  size = "sm",
  className = "",
  ...props
}: StatusBadgeProps) {
  const resolvedVariant = resolveVariant(status, explicitVariant);
  const textLabel = resolveLabel(status, customLabel);

  const classNames = [
    "status-badge",
    `status-badge--${resolvedVariant}`,
    `status-badge--${size}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={classNames}
      aria-label={`Status: ${textLabel}`}
      {...props}
    >
      {showDot && <span className="status-badge-dot" aria-hidden="true" />}
      <span className="status-badge-text">{textLabel}</span>
    </span>
  );
}

export default StatusBadge;
