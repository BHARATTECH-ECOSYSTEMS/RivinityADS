import { STATUS } from "../types";
import StatusBadge from "./ui/StatusBadge";

export interface BadgeProps {
  status: number | string;
  label?: string;
  className?: string;
}

export default function Badge({ status, label: customLabel, className = "" }: BadgeProps) {
  const numericStatus = typeof status === "number" ? status : parseInt(status, 10);
  const statusLabel = customLabel || (STATUS[numericStatus] ?? String(status));

  // Render backward-compatible badge class while leveraging StatusBadge semantic structure
  return (
    <StatusBadge
      status={status}
      label={statusLabel}
      className={`badge ${statusLabel} ${className}`}
    />
  );
}
