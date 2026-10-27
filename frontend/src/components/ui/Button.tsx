import React, { forwardRef } from "react";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  loadingText?: string;
  icon?: React.ReactNode;
  iconPosition?: "left" | "right";
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      children,
      variant = "primary",
      size = "md",
      isLoading = false,
      loadingText,
      icon,
      iconPosition = "left",
      fullWidth = false,
      disabled,
      className = "",
      type = "button",
      ...props
    },
    ref
  ) => {
    const classNames = [
      "btn",
      `btn-${variant}`,
      `btn-${size}`,
      fullWidth ? "btn-full-width" : "",
      isLoading ? "btn-loading" : "",
      className,
    ]
      .filter(Boolean)
      .join(" ");

    const isDisabled = disabled || isLoading;

    return (
      <button
        ref={ref}
        type={type}
        className={classNames}
        disabled={isDisabled}
        aria-busy={isLoading ? "true" : undefined}
        {...props}
      >
        {isLoading && (
          <span className="btn-spinner" aria-hidden="true">
            <svg
              className="spinner-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" />
            </svg>
          </span>
        )}
        {!isLoading && icon && iconPosition === "left" && (
          <span className="btn-icon btn-icon-left" aria-hidden="true">
            {icon}
          </span>
        )}
        <span className="btn-text">
          {isLoading && loadingText ? loadingText : children}
        </span>
        {!isLoading && icon && iconPosition === "right" && (
          <span className="btn-icon btn-icon-right" aria-hidden="true">
            {icon}
          </span>
        )}
      </button>
    );
  }
);

Button.displayName = "Button";

export default Button;
