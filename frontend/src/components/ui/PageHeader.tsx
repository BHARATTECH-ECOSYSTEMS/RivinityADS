import React from "react";

export interface PageHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  className = "",
  ...props
}: PageHeaderProps) {
  return (
    <header className={`page-header ${className}`} {...props}>
      <div className="page-header-main">
        {eyebrow && <div className="page-header-eyebrow">{eyebrow}</div>}
        <h1 className="page-header-title">{title}</h1>
        {description && (
          <p className="page-header-description">{description}</p>
        )}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}

export default PageHeader;
