import React, { useEffect, useRef } from "react";
import Button from "./Button";

export interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  confirmText?: string;
  cancelLabel?: string;
  cancelText?: string;
  variant?: "danger" | "primary";
  isLoading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  className?: string;
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel,
  confirmText,
  cancelLabel,
  cancelText,
  variant = "primary",
  isLoading = false,
  onConfirm,
  onCancel,
  className = "",
}: ConfirmDialogProps) {
  const resolvedConfirmLabel = confirmText || confirmLabel || "Confirm";
  const resolvedCancelLabel = cancelText || cancelLabel || "Cancel";
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (isOpen) {
      previousActiveElement.current = document.activeElement as HTMLElement | null;
      // Focus dialog on open
      const firstFocusable = dialogRef.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      firstFocusable?.focus();

      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape" && !isLoading) {
          e.preventDefault();
          onCancel();
        }
      };

      document.addEventListener("keydown", handleKeyDown);
      return () => {
        document.removeEventListener("keydown", handleKeyDown);
        previousActiveElement.current?.focus?.();
      };
    }
  }, [isOpen, isLoading, onCancel]);

  if (!isOpen) return null;

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isLoading) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        className={`modal-dialog ${className}`}
      >
        <div className="modal-header">
          <h3 id="confirm-dialog-title" className="modal-title">
            {title}
          </h3>
          <button
            type="button"
            className="modal-close"
            onClick={onCancel}
            disabled={isLoading}
            aria-label="Close dialog"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>
        <div className="modal-body">
          <div className="confirm-dialog-message">{message}</div>
        </div>
        <div className="modal-footer">
          <Button
            variant="secondary"
            onClick={onCancel}
            disabled={isLoading}
          >
            {resolvedCancelLabel}
          </Button>
          <Button
            variant={variant === "danger" ? "danger" : "primary"}
            onClick={onConfirm}
            isLoading={isLoading}
          >
            {resolvedConfirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default ConfirmDialog;
