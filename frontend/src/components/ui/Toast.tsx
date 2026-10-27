import React from "react";
import { AlertVariant } from "./Alert";

export interface ToastProps {
  id?: string;
  variant?: AlertVariant;
  title?: string;
  message: string;
  onClose?: () => void;
  className?: string;
}

export function Toast({
  variant = "info",
  title,
  message,
  onClose,
  className = "",
}: ToastProps) {
  const role = variant === "error" || variant === "warning" ? "alert" : "status";

  return (
    <div
      className={`toast toast--${variant} ${className}`}
      role={role}
      aria-live={variant === "error" ? "assertive" : "polite"}
    >
      <div className="toast-body">
        {title && <h5 className="toast-title">{title}</h5>}
        <p className="toast-message">{message}</p>
      </div>
      {onClose && (
        <button
          type="button"
          className="toast-close"
          onClick={onClose}
          aria-label="Close notification"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path
              fillRule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
}

export default Toast;
