import React from "react";

export interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className = "",
  ...props
}: EmptyStateProps) {
  return (
    <div className={`empty-state ${className}`} {...props}>
      {icon && (
        <div className="empty-state-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      <h3 className="empty-state-title">{title}</h3>
      {description && <p className="empty-state-description">{description}</p>}
      {action && <div className="empty-state-actions">{action}</div>}
    </div>
  );
}

export default EmptyState;
