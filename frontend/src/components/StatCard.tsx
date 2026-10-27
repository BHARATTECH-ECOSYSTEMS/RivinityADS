import React from "react";

export interface StatCardProps {
  title: string;
  value: string | number;
  hint?: string;
  icon?: React.ReactNode;
  className?: string;
}

export default function StatCard({
  title,
  value,
  hint,
  icon,
  className = "",
}: StatCardProps) {
  return (
    <div className={`stat ${className}`}>
      <div className="stat-header">
        <div className="stat-label eyebrow label">{title}</div>
        {icon && <div className="stat-icon" aria-hidden="true">{icon}</div>}
      </div>
      <div className="stat-value value">{value}</div>
      {hint && <div className="stat-hint hint">{hint}</div>}
    </div>
  );
}
