import React from "react";

export interface PageContainerProps extends React.HTMLAttributes<HTMLDivElement> {
  maxWidth?: "sm" | "md" | "lg" | "full";
  className?: string;
  children: React.ReactNode;
}

export function PageContainer({
  maxWidth = "lg",
  className = "",
  children,
  ...props
}: PageContainerProps) {
  const classNames = [
    "page-container",
    `page-container--${maxWidth}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classNames} {...props}>
      {children}
    </div>
  );
}

export default PageContainer;
