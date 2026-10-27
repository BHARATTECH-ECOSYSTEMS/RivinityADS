import React from "react";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padding?: "none" | "sm" | "md" | "lg";
  variant?: "default" | "subtle" | "bordered";
  title?: string;
  subtitle?: string;
  footer?: React.ReactNode;
  className?: string;
}

export function Card({
  children,
  padding = "md",
  variant = "default",
  title,
  subtitle,
  footer,
  className = "",
  ...props
}: CardProps) {
  const classNames = [
    "card",
    `card-padding-${padding}`,
    `card-variant-${variant}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classNames} {...props}>
      {(title || subtitle) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {subtitle && <CardDescription>{subtitle}</CardDescription>}
        </CardHeader>
      )}
      {children}
      {footer && <CardFooter>{footer}</CardFooter>}
    </div>
  );
}

export interface CardHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  actions?: React.ReactNode;
}

export function CardHeader({
  children,
  actions,
  className = "",
  ...props
}: CardHeaderProps) {
  return (
    <div className={`card-header ${className}`} {...props}>
      <div className="card-header-main">{children}</div>
      {actions && <div className="card-header-actions">{actions}</div>}
    </div>
  );
}

export interface CardTitleProps extends React.HTMLAttributes<HTMLHeadingElement> {
  as?: "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
}

export function CardTitle({
  as: Component = "h3",
  children,
  className = "",
  ...props
}: CardTitleProps) {
  return (
    <Component className={`card-title ${className}`} {...props}>
      {children}
    </Component>
  );
}

export function CardDescription({
  children,
  className = "",
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={`card-description ${className}`} {...props}>
      {children}
    </p>
  );
}

export function CardBody({
  children,
  className = "",
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`card-body ${className}`} {...props}>
      {children}
    </div>
  );
}

export function CardFooter({
  children,
  className = "",
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`card-footer ${className}`} {...props}>
      {children}
    </div>
  );
}

export default Card;
