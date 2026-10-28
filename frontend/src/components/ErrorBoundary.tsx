import React, { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    window.location.href = "/";
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#0b0f19",
          color: "#f8fafc",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          padding: "24px",
        }}>
          <div style={{
            maxWidth: "480px",
            width: "100%",
            backgroundColor: "#1e293b",
            borderRadius: "12px",
            padding: "32px",
            boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
            border: "1px solid #334155",
            textAlign: "center",
          }}>
            <div style={{ fontSize: "40px", marginBottom: "16px" }}>⚠️</div>
            <h1 style={{ fontSize: "20px", fontWeight: 600, marginBottom: "8px" }}>Something went wrong</h1>
            <p style={{ color: "#94a3b8", fontSize: "14px", lineHeight: 1.5, marginBottom: "24px" }}>
              An unexpected error occurred while rendering this page.
            </p>
            {this.state.error && (
              <pre style={{
                textAlign: "left",
                backgroundColor: "#0f172a",
                color: "#f87171",
                padding: "12px",
                borderRadius: "8px",
                fontSize: "12px",
                overflowX: "auto",
                marginBottom: "24px",
              }}>
                {this.state.error.message}
              </pre>
            )}
            <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
              <button
                type="button"
                onClick={() => window.location.reload()}
                style={{
                  backgroundColor: "#3b82f6",
                  color: "#ffffff",
                  border: "none",
                  padding: "10px 20px",
                  borderRadius: "8px",
                  fontSize: "14px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Reload Page
              </button>
              <button
                type="button"
                onClick={this.handleReset}
                style={{
                  backgroundColor: "transparent",
                  color: "#94a3b8",
                  border: "1px solid #475569",
                  padding: "10px 20px",
                  borderRadius: "8px",
                  fontSize: "14px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Go to Home
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
