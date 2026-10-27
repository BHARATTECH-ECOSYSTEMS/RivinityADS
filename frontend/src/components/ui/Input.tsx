import React, { forwardRef, useId } from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  isRequired?: boolean;
  fullWidth?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      error,
      hint,
      id,
      isRequired,
      fullWidth = true,
      className = "",
      disabled,
      ...props
    },
    ref
  ) => {
    const generatedId = useId();
    const inputId = id || generatedId;
    const hintId = hint ? `${inputId}-hint` : undefined;
    const errorId = error ? `${inputId}-error` : undefined;
    const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

    const inputClasses = [
      "form-input",
      error ? "has-error" : "",
      fullWidth ? "form-input-full" : "",
      className,
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <div className={`form-group ${fullWidth ? "form-group-full" : ""}`}>
        {label && (
          <label htmlFor={inputId} className="form-label">
            {label}
            {isRequired && <span className="form-required-asterisk" aria-hidden="true">*</span>}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={inputClasses}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-required={isRequired || props.required}
          aria-describedby={describedBy}
          {...props}
        />
        {hint && !error && (
          <p id={hintId} className="form-hint">
            {hint}
          </p>
        )}
        {error && (
          <p id={errorId} className="form-error" role="alert">
            <svg
              className="form-error-icon"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
                clipRule="evenodd"
              />
            </svg>
            <span className="form-error-text">{error}</span>
          </p>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";

export default Input;
