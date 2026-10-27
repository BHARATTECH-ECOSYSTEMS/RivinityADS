import React, { forwardRef, useId } from "react";

export interface SelectOption {
  value: string | number;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  hint?: string;
  options?: SelectOption[];
  isRequired?: boolean;
  fullWidth?: boolean;
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      label,
      error,
      hint,
      id,
      options,
      children,
      isRequired,
      fullWidth = true,
      placeholder,
      className = "",
      disabled,
      ...props
    },
    ref
  ) => {
    const generatedId = useId();
    const selectId = id || generatedId;
    const hintId = hint ? `${selectId}-hint` : undefined;
    const errorId = error ? `${selectId}-error` : undefined;
    const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

    const selectClasses = [
      "form-select",
      error ? "has-error" : "",
      fullWidth ? "form-select-full" : "",
      className,
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <div className={`form-group ${fullWidth ? "form-group-full" : ""}`}>
        {label && (
          <label htmlFor={selectId} className="form-label">
            {label}
            {isRequired && <span className="form-required-asterisk" aria-hidden="true">*</span>}
          </label>
        )}
        <div className="select-wrapper">
          <select
            ref={ref}
            id={selectId}
            className={selectClasses}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-required={isRequired || props.required}
            aria-describedby={describedBy}
            {...props}
          >
            {placeholder && (
              <option value="" disabled>
                {placeholder}
              </option>
            )}
            {options
              ? options.map((opt) => (
                  <option key={String(opt.value)} value={opt.value} disabled={opt.disabled}>
                    {opt.label}
                  </option>
                ))
              : children}
          </select>
          <span className="select-chevron" aria-hidden="true">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </span>
        </div>
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

Select.displayName = "Select";

export default Select;
