/**
 * Formatters and metadata for notifications and preference categories.
 */

export const CATEGORY_METADATA: Record<
  string,
  { label: string; description: string; icon: string }
> = {
  billing: {
    label: "Billing & Invoicing",
    description: "Invoices, wallet deposits, payment confirmations, and budget alerts.",
    icon: "💳",
  },
  campaign: {
    label: "Campaigns & Delivery",
    description: "Campaign review status, activation, pause triggers, and spend limits.",
    icon: "📢",
  },
  creative: {
    label: "Creatives & Moderation",
    description: "Ad approvals, policy rejections, and moderation review feedback.",
    icon: "🎨",
  },
  optimization: {
    label: "Optimization Suggestions",
    description: "AI-driven performance recommendations and automated budget reallocations.",
    icon: "⚡",
  },
  domain: {
    label: "Custom Domains",
    description: "DNS verification challenges, validation results, and domain health.",
    icon: "🌐",
  },
  security: {
    label: "Account Security (Mandatory)",
    description: "Critical security notices, password reset confirmations, and login alerts.",
    icon: "🔒",
  },
  publisher: {
    label: "Publisher Placements",
    description: "Ad unit performance, revenue milestones, and weekly placement digests.",
    icon: "📰",
  },
  system: {
    label: "System & Maintenance",
    description: "Platform updates, scheduled maintenance windows, and service alerts.",
    icon: "⚙️",
  },
  webhook: {
    label: "Webhooks & Integrations",
    description: "Endpoint delivery failures, retries, and webhook connection errors.",
    icon: "🔌",
  },
};

export function formatCategoryLabel(category: string): string {
  return CATEGORY_METADATA[category]?.label || category.charAt(0).toUpperCase() + category.slice(1);
}

export function getCategoryDescription(category: string): string {
  return CATEGORY_METADATA[category]?.description || "Notifications for this category.";
}

export function formatRelativeTime(dateString: string): string {
  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;

    const now = new Date();
    const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSeconds < 45) return "Just now";
    if (diffSeconds < 90) return "1m ago";
    if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
    if (diffSeconds < 7200) return "1h ago";
    if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
    if (diffSeconds < 172800) return "Yesterday";
    if (diffSeconds < 604800) return `${Math.floor(diffSeconds / 86400)}d ago`;

    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: date.getFullYear() !== now.getFullYear() ? "numeric" : undefined,
    });
  } catch {
    return dateString;
  }
}
