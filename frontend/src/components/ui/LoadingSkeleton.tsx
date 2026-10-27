import React from "react";

export interface LoadingSkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "text" | "rect" | "circle";
  width?: string | number;
  height?: string | number;
  borderRadius?: string | number;
  lines?: number;
  className?: string;
}

export function LoadingSkeleton({
  variant = "text",
  width,
  height,
  borderRadius,
  lines = 1,
  className = "",
  style,
  ...props
}: LoadingSkeletonProps) {
  const getStyle = (isLastLine = false): React.CSSProperties => {
    const customStyle: React.CSSProperties = { ...style };
    if (width !== undefined) customStyle.width = isLastLine && lines > 1 ? "75%" : width;
    if (height !== undefined) customStyle.height = height;
    if (borderRadius !== undefined) customStyle.borderRadius = borderRadius;
    return customStyle;
  };

  if (variant === "text" && lines > 1) {
    return (
      <div
        className={`skeleton-group ${className}`}
        role="status"
        aria-live="polite"
        {...props}
      >
        <span className="sr-only">Loading...</span>
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="skeleton skeleton-text"
            style={getStyle(i === lines - 1)}
            aria-hidden="true"
          />
        ))}
      </div>
    );
  }

  return (
    <div
      className={`skeleton skeleton-${variant} ${className}`}
      style={getStyle()}
      role="status"
      aria-live="polite"
      {...props}
    >
      <span className="sr-only">Loading...</span>
    </div>
  );
}

export default LoadingSkeleton;
