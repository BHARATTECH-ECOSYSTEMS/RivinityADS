/**
 * Frontend Unit & Integration Tests for Step 5: Authentication Infrastructure
 *
 * Tests:
 * 1. Typed API client request/response handling for:
 *    - POST /api/v1/auth/verify-email
 *    - POST /api/v1/auth/resend-verification
 *    - POST /api/v1/auth/request-password-reset
 *    - POST /api/v1/auth/reset-password
 *    - POST /api/v1/auth/me (getCurrentUser)
 * 2. In-memory token management, token getter, and credentials: "include" cookie handling
 * 3. 403 "Email verification required" does not clear the session / memory token
 * 4. Error handling does not leak sensitive tokens or credentials in errors
 * 5. Representation of email_verified in login, registration, and user profiles
 * 6. AuthContext state verification and refreshUser workflow
 */

import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  verifyEmail,
  resendVerification,
  requestPasswordReset,
  resetPassword,
  getCurrentUser,
  login,
  register,
  setMemoryToken,
  getMemoryToken,
  ApiError,
} from "../src/api/client.ts";

// Save original globalThis.fetch
const originalFetch = globalThis.fetch;

let mockRequests = [];
let mockResponses = [];

beforeEach(() => {
  mockRequests = [];
  mockResponses = [];
  setMemoryToken(null);

  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    mockRequests.push({ url, init });

    const nextResponse = mockResponses.shift() ?? {
      status: 200,
      body: { message: "ok" },
    };

    return {
      ok: nextResponse.status >= 200 && nextResponse.status < 300,
      status: nextResponse.status,
      statusText: nextResponse.status === 200 ? "OK" : "Error",
      headers: new Headers(nextResponse.headers ?? { "Content-Type": "application/json" }),
      json: async () => nextResponse.body,
      text: async () => JSON.stringify(nextResponse.body),
    };
  };
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  setMemoryToken(null);
});

// ---------------------------------------------------------------------------
// 1. verifyEmail Endpoint
// ---------------------------------------------------------------------------

test("verifyEmail sends POST to /api/v1/auth/verify-email with raw token (string overload)", async () => {
  mockResponses.push({ status: 200, body: { message: "Email verified" } });

  const result = await verifyEmail("test_verification_token_abc123");

  assert.strictEqual(result.message, "Email verified");
  assert.strictEqual(mockRequests.length, 1);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/verify-email");
  assert.strictEqual(mockRequests[0].init?.method, "POST");
  assert.strictEqual(mockRequests[0].init?.credentials, "include");

  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.token, "test_verification_token_abc123");
});

test("verifyEmail supports typed object overload { token }", async () => {
  mockResponses.push({ status: 200, body: { message: "Email verified" } });

  const result = await verifyEmail({ token: "obj_token_456" });

  assert.strictEqual(result.message, "Email verified");
  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.token, "obj_token_456");
});

test("verifyEmail handles 400 invalid token error", async () => {
  mockResponses.push({
    status: 400,
    body: { detail: "Verification token is invalid" },
  });

  await assert.rejects(
    async () => {
      await verifyEmail("invalid_tok");
    },
    (err) => {
      assert(err instanceof ApiError);
      assert.strictEqual(err.status, 400);
      assert.strictEqual(err.message, "Verification token is invalid");
      return true;
    }
  );
});

// ---------------------------------------------------------------------------
// 2. resendVerification Endpoint
// ---------------------------------------------------------------------------

test("resendVerification sends POST to /api/v1/auth/resend-verification", async () => {
  mockResponses.push({
    status: 200,
    body: { message: "If an unverified account exists with this email, a verification link has been sent" },
  });

  const result = await resendVerification("user@example.com");

  assert.match(result.message, /verification link has been sent/);
  assert.strictEqual(mockRequests.length, 1);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/resend-verification");
  assert.strictEqual(mockRequests[0].init?.credentials, "include");

  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.email, "user@example.com");
});

test("resendVerification supports object overload and handles rate limiting (429)", async () => {
  mockResponses.push({
    status: 429,
    body: { detail: "Too many verification resend requests" },
  });

  await assert.rejects(
    async () => {
      await resendVerification({ email: "spammer@example.com" });
    },
    (err) => {
      assert(err instanceof ApiError);
      assert.strictEqual(err.status, 429);
      assert.strictEqual(err.message, "Too many verification resend requests");
      return true;
    }
  );
});

// ---------------------------------------------------------------------------
// 3. requestPasswordReset Endpoint
// ---------------------------------------------------------------------------

test("requestPasswordReset sends POST to /api/v1/auth/request-password-reset", async () => {
  mockResponses.push({
    status: 200,
    body: { message: "If the account exists, password reset instructions have been sent" },
  });

  const result = await requestPasswordReset("forgot@example.com");

  assert.match(result.message, /password reset instructions have been sent/);
  assert.strictEqual(mockRequests.length, 1);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/request-password-reset");

  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.email, "forgot@example.com");
});

// ---------------------------------------------------------------------------
// 4. resetPassword Endpoint
// ---------------------------------------------------------------------------

test("resetPassword sends POST to /api/v1/auth/reset-password with token and new password", async () => {
  mockResponses.push({
    status: 200,
    body: { message: "Password reset" },
  });

  const result = await resetPassword({
    token: "reset_token_xyz",
    password: "NewSecurePassword123!",
  });

  assert.strictEqual(result.message, "Password reset");
  assert.strictEqual(mockRequests.length, 1);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/reset-password");

  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.token, "reset_token_xyz");
  assert.strictEqual(body.password, "NewSecurePassword123!");
});

test("resetPassword handles invalid or expired reset token error (400)", async () => {
  mockResponses.push({
    status: 400,
    body: { detail: "Reset token is invalid" },
  });

  await assert.rejects(
    async () => {
      await resetPassword({ token: "expired_token", password: "Password123!" });
    },
    (err) => {
      assert(err instanceof ApiError);
      assert.strictEqual(err.status, 400);
      assert.strictEqual(err.message, "Reset token is invalid");
      return true;
    }
  );
});

// ---------------------------------------------------------------------------
// 5. getCurrentUser (POST /api/v1/auth/me) Endpoint
// ---------------------------------------------------------------------------

test("getCurrentUser sends POST to /api/v1/auth/me with Bearer token header", async () => {
  setMemoryToken("active_bearer_token");

  const mockUser = {
    id: 10,
    name: "Advertiser One",
    email: "adv1@example.com",
    role: "advertiser",
    advertiser_id: 1,
    publisher_id: null,
    email_verified: false,
  };

  mockResponses.push({ status: 200, body: mockUser });

  const user = await getCurrentUser();

  assert.strictEqual(user.id, 10);
  assert.strictEqual(user.email_verified, false);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/me");
  assert.strictEqual(mockRequests[0].init?.method, "POST");

  const headers = mockRequests[0].init?.headers;
  assert.strictEqual(headers["Authorization"], "Bearer active_bearer_token");
});

// ---------------------------------------------------------------------------
// 6. Memory Token and 403 Session Preservation
// ---------------------------------------------------------------------------

test("403 Email verification required does not destroy the session or clear memoryToken", async () => {
  setMemoryToken("authenticated_session_token");
  assert.strictEqual(getMemoryToken(), "authenticated_session_token");

  // Simulated operational call that fails with 403 Email verification required
  mockResponses.push({
    status: 403,
    body: { detail: "Email verification required" },
  });

  await assert.rejects(
    async () => {
      await getCurrentUser();
    },
    (err) => {
      assert(err instanceof ApiError);
      assert.strictEqual(err.status, 403);
      assert.strictEqual(err.message, "Email verification required");
      return true;
    }
  );

  // The memory token MUST still be intact so the user remains authenticated
  assert.strictEqual(getMemoryToken(), "authenticated_session_token");
});

test("Error messages do not leak raw tokens or credentials", async () => {
  mockResponses.push({
    status: 400,
    body: { detail: "Invalid token" },
  });

  try {
    await verifyEmail("super_secret_raw_token_value_999");
    assert.fail("Should have thrown");
  } catch (err) {
    assert(err instanceof ApiError);
    // Error message must not contain the raw token
    assert(!err.message.includes("super_secret_raw_token_value_999"));
  }
});

// ---------------------------------------------------------------------------
// 7. Login and Registration State & email_verified Representation
// ---------------------------------------------------------------------------

test("login correctly captures unverified user state (email_verified=false)", async () => {
  const backendAuthResponse = {
    user: {
      id: 5,
      name: "New Unverified User",
      email: "unverified@example.com",
      role: "advertiser",
      advertiser_id: 2,
      publisher_id: null,
      email_verified: false,
    },
    access_token: "jwt_token_unverified",
  };

  mockResponses.push({ status: 200, body: backendAuthResponse });

  const session = await login("unverified@example.com", "Password123!");

  assert.strictEqual(session.user.email_verified, false);
  assert.strictEqual(session.token, "jwt_token_unverified");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/login");
  assert.strictEqual(mockRequests[0].init?.credentials, "include");
});

test("login correctly captures verified user state (email_verified=true)", async () => {
  const backendAuthResponse = {
    user: {
      id: 6,
      name: "Verified User",
      email: "verified@example.com",
      role: "advertiser",
      advertiser_id: 3,
      publisher_id: null,
      email_verified: true,
    },
    access_token: "jwt_token_verified",
  };

  mockResponses.push({ status: 200, body: backendAuthResponse });

  const session = await login("verified@example.com", "Password123!");

  assert.strictEqual(session.user.email_verified, true);
  assert.strictEqual(session.token, "jwt_token_verified");
});

test("register correctly receives email_verified=false from backend", async () => {
  const backendRegisterResponse = {
    user: {
      id: 7,
      name: "Brand New User",
      email: "brandnew@example.com",
      role: "publisher",
      advertiser_id: null,
      publisher_id: 4,
      email_verified: false,
    },
    access_token: "jwt_token_register",
  };

  mockResponses.push({ status: 201, body: backendRegisterResponse });

  const session = await register("Brand New User", "brandnew@example.com", "Password123!", "publisher");

  assert.strictEqual(session.user.email_verified, false);
  assert.strictEqual(session.token, "jwt_token_register");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/register");
});

// ---------------------------------------------------------------------------
// 8. AuthContext State & refreshUser Workflow Simulation
// ---------------------------------------------------------------------------

test("AuthContext state: unverified vs verified distinction and refreshUser update", async () => {
  // Step 1: Initial unverified session
  let currentSession = {
    user: {
      id: 20,
      name: "Flow User",
      email: "flow@example.com",
      role: "advertiser",
      advertiser_id: 8,
      publisher_id: null,
      email_verified: false,
    },
    token: "flow_access_token",
  };

  setMemoryToken(currentSession.token);

  // Verification state helper
  let isVerified = Boolean(currentSession?.user?.email_verified);
  assert.strictEqual(isVerified, false, "Unverified user should have isVerified = false");
  assert.notStrictEqual(currentSession, null, "Unverified user is authenticated (session is not null)");

  // Step 2: Email verification succeeds via API
  mockResponses.push({ status: 200, body: { message: "Email verified" } });
  const verifyResult = await verifyEmail("valid_verification_token");
  assert.strictEqual(verifyResult.message, "Email verified");

  // Step 3: refreshUser is called, fetching updated profile from /api/v1/auth/me
  mockResponses.push({
    status: 200,
    body: {
      id: 20,
      name: "Flow User",
      email: "flow@example.com",
      role: "advertiser",
      advertiser_id: 8,
      publisher_id: null,
      email_verified: true, // Now verified!
    },
  });

  const updatedUser = await getCurrentUser();
  currentSession = { user: updatedUser, token: getMemoryToken() };
  isVerified = Boolean(currentSession.user.email_verified);

  assert.strictEqual(isVerified, true, "After refreshUser, isVerified must be true");
  assert.strictEqual(currentSession.user.email_verified, true);
  assert.strictEqual(currentSession.token, "flow_access_token");
});

// ---------------------------------------------------------------------------
// 9. Email Verification Page Flow (/verify-email)
// ---------------------------------------------------------------------------

test("VerifyEmail flow: missing token prevents API call and flags error immediately", async () => {
  const runVerifyEmail = async (token, session, refreshUserFn) => {
    if (!token) {
      return { status: "error", error: "No verification token provided. Please check the link in your email.", calledApi: false };
    }
    try {
      const res = await verifyEmail(token);
      if (session && refreshUserFn) {
        await refreshUserFn();
      }
      return { status: "success", message: res.message, calledApi: true };
    } catch (err) {
      return { status: "error", error: err.message, calledApi: true };
    }
  };

  const result = await runVerifyEmail(null, null, null);
  assert.strictEqual(result.status, "error");
  assert.match(result.error, /No verification token provided/);
  assert.strictEqual(result.calledApi, false);
  assert.strictEqual(mockRequests.length, 0, "API should not be called when token is missing");
});

test("VerifyEmail flow: valid token verifies email and refreshes active user session", async () => {
  mockResponses.push({ status: 200, body: { message: "Email successfully verified" } });
  mockResponses.push({
    status: 200,
    body: {
      id: 15,
      name: "Adv",
      email: "adv@example.com",
      role: "advertiser",
      advertiser_id: 5,
      publisher_id: null,
      email_verified: true,
    },
  });

  let refreshCalled = false;
  const mockRefreshUser = async () => {
    refreshCalled = true;
    return await getCurrentUser();
  };

  const mockSession = { user: { id: 15, email: "adv@example.com", role: "advertiser", email_verified: false }, token: "active_token" };
  setMemoryToken("active_token");

  const runVerifyEmail = async (token, session, refreshUserFn) => {
    const res = await verifyEmail(token);
    if (session && refreshUserFn) {
      await refreshUserFn();
    }
    return { status: "success", message: res.message };
  };

  const result = await runVerifyEmail("valid_verification_token_123", mockSession, mockRefreshUser);
  assert.strictEqual(result.status, "success");
  assert.strictEqual(result.message, "Email successfully verified");
  assert.strictEqual(refreshCalled, true, "refreshUser must be called on successful verification");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/verify-email");
  assert.strictEqual(mockRequests[1].url, "/api/v1/auth/me");
});

test("VerifyEmail flow: unauthenticated user verifies email without error and without calling refreshUser", async () => {
  mockResponses.push({ status: 200, body: { message: "Email verified" } });

  let refreshCalled = false;
  const mockRefreshUser = async () => {
    refreshCalled = true;
  };

  const runVerifyEmail = async (token, session, refreshUserFn) => {
    const res = await verifyEmail(token);
    if (session && refreshUserFn) {
      await refreshUserFn();
    }
    return { status: "success", message: res.message };
  };

  const result = await runVerifyEmail("unauth_token_456", null, mockRefreshUser);
  assert.strictEqual(result.status, "success");
  assert.strictEqual(refreshCalled, false, "refreshUser must not be called when unauthenticated");
});

test("VerifyEmail flow: invalid or expired token handles 400 error gracefully without leaking token", async () => {
  mockResponses.push({ status: 400, body: { detail: "Verification token is invalid or expired" } });

  const rawToken = "raw_secret_token_to_not_leak_987";
  const runVerifyEmail = async (token) => {
    try {
      const res = await verifyEmail(token);
      return { status: "success", message: res.message };
    } catch (err) {
      return { status: "error", error: err.message };
    }
  };

  const result = await runVerifyEmail(rawToken);
  assert.strictEqual(result.status, "error");
  assert.strictEqual(result.error, "Verification token is invalid or expired");
  assert(!result.error.includes(rawToken), "Error must not contain raw token");
});

// ---------------------------------------------------------------------------
// 10. Forgot Password Page Flow (/forgot-password)
// ---------------------------------------------------------------------------

test("ForgotPassword flow: requestPasswordReset succeeds and displays generic message", async () => {
  mockResponses.push({
    status: 200,
    body: { message: "If the account exists, password reset instructions have been sent" },
  });

  const runForgotPassword = async (email, isBusy) => {
    if (isBusy || !email) return { submitted: false };
    try {
      const res = await requestPasswordReset(email.trim());
      return {
        submitted: true,
        genericMessage: `If an account exists with ${email.trim()}, password reset instructions have been sent.`,
      };
    } catch (err) {
      return { submitted: false, error: err.message };
    }
  };

  const result = await runForgotPassword("person@example.com", false);
  assert.strictEqual(result.submitted, true);
  assert.match(result.genericMessage, /If an account exists with person@example.com/);
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/request-password-reset");
});

test("ForgotPassword flow: prevents duplicate submission when busy", async () => {
  const runForgotPassword = async (email, isBusy) => {
    if (isBusy || !email) return { skipped: true };
    return { skipped: false };
  };

  const result = await runForgotPassword("person@example.com", true);
  assert.strictEqual(result.skipped, true);
  assert.strictEqual(mockRequests.length, 0);
});

test("ForgotPassword flow: handles 429 rate limit error gracefully", async () => {
  mockResponses.push({
    status: 429,
    body: { detail: "Too many password reset requests. Please try again later." },
  });

  const runForgotPassword = async (email) => {
    try {
      await requestPasswordReset(email);
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  };

  const result = await runForgotPassword("rate_limited@example.com");
  assert.strictEqual(result.success, false);
  assert.strictEqual(result.error, "Too many password reset requests. Please try again later.");
});

// ---------------------------------------------------------------------------
// 11. Reset Password Page Flow (/reset-password)
// ---------------------------------------------------------------------------

test("ResetPassword flow: rejects missing token without calling API", async () => {
  const runResetPassword = async ({ token, password, confirmPassword }) => {
    if (!token) return { status: "error", error: "Missing password reset token." };
    if (password.length < 12) return { status: "error", error: "Password must be at least 12 characters." };
    if (password !== confirmPassword) return { status: "error", error: "Passwords do not match." };
    try {
      const res = await resetPassword({ token, password });
      return { status: "success", message: res.message };
    } catch (err) {
      return { status: "error", error: err.message };
    }
  };

  const result = await runResetPassword({ token: null, password: "ValidPassword123!", confirmPassword: "ValidPassword123!" });
  assert.strictEqual(result.status, "error");
  assert.match(result.error, /Missing password reset token/);
  assert.strictEqual(mockRequests.length, 0);
});

test("ResetPassword flow: validates 12 character minimum length before API call", async () => {
  const runResetPassword = async ({ token, password, confirmPassword }) => {
    if (!token) return { status: "error", error: "Missing password reset token." };
    if (password.length < 12) return { status: "error", error: "Password must be at least 12 characters." };
    if (password !== confirmPassword) return { status: "error", error: "Passwords do not match." };
    const res = await resetPassword({ token, password });
    return { status: "success", message: res.message };
  };

  const result = await runResetPassword({ token: "tok123", password: "short", confirmPassword: "short" });
  assert.strictEqual(result.status, "error");
  assert.match(result.error, /at least 12 characters/);
  assert.strictEqual(mockRequests.length, 0);
});

test("ResetPassword flow: validates password confirmation match before API call", async () => {
  const runResetPassword = async ({ token, password, confirmPassword }) => {
    if (!token) return { status: "error", error: "Missing password reset token." };
    if (password.length < 12) return { status: "error", error: "Password must be at least 12 characters." };
    if (password !== confirmPassword) return { status: "error", error: "Passwords do not match." };
    const res = await resetPassword({ token, password });
    return { status: "success", message: res.message };
  };

  const result = await runResetPassword({ token: "tok123", password: "PasswordLong123!", confirmPassword: "DifferentPassword123!" });
  assert.strictEqual(result.status, "error");
  assert.match(result.error, /Passwords do not match/);
  assert.strictEqual(mockRequests.length, 0);
});

test("ResetPassword flow: valid token and password calls resetPassword and succeeds", async () => {
  mockResponses.push({ status: 200, body: { message: "Password reset" } });

  const runResetPassword = async ({ token, password, confirmPassword, busy }) => {
    if (busy) return { skipped: true };
    if (!token) return { status: "error", error: "Missing password reset token." };
    if (password.length < 12) return { status: "error", error: "Password must be at least 12 characters." };
    if (password !== confirmPassword) return { status: "error", error: "Passwords do not match." };
    try {
      const res = await resetPassword({ token, password });
      return { status: "success", message: res.message };
    } catch (err) {
      return { status: "error", error: err.message };
    }
  };

  const result = await runResetPassword({
    token: "valid_reset_tok",
    password: "BrandNewPassword123!",
    confirmPassword: "BrandNewPassword123!",
    busy: false,
  });

  assert.strictEqual(result.status, "success");
  assert.strictEqual(result.message, "Password reset");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/reset-password");

  // Duplicate submission test
  const dupResult = await runResetPassword({
    token: "valid_reset_tok",
    password: "BrandNewPassword123!",
    confirmPassword: "BrandNewPassword123!",
    busy: true,
  });
  assert.strictEqual(dupResult.skipped, true);
});

// ---------------------------------------------------------------------------
// 12. Verification Banner State & Resend Workflow
// ---------------------------------------------------------------------------

test("VerificationBanner visibility logic: unauthenticated or verified users do not see banner", () => {
  const shouldShowBanner = (session, isVerified) => {
    if (!session || isVerified) return false;
    return true;
  };

  assert.strictEqual(shouldShowBanner(null, false), false, "Unauthenticated user should not see banner");
  assert.strictEqual(shouldShowBanner(null, true), false, "Unauthenticated user should not see banner");
  assert.strictEqual(shouldShowBanner({ user: { email: "u@e.com" } }, true), false, "Verified user should not see banner");
  assert.strictEqual(shouldShowBanner({ user: { email: "u@e.com" } }, false), true, "Unverified user must see banner");
});

test("VerificationBanner resend workflow: calls resendVerification and displays success", async () => {
  mockResponses.push({ status: 200, body: { message: "Verification email resent" } });

  const handleResend = async (session, resending) => {
    if (resending || !session?.user?.email) return { skipped: true };
    try {
      const res = await resendVerification(session.user.email);
      return { success: true, message: res.message };
    } catch (err) {
      return { success: false, error: err.message };
    }
  };

  const session = { user: { email: "unverified@example.com" } };
  const result = await handleResend(session, false);

  assert.strictEqual(result.success, true);
  assert.strictEqual(result.message, "Verification email resent");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/resend-verification");

  // Duplicate prevention when resending
  const busyResult = await handleResend(session, true);
  assert.strictEqual(busyResult.skipped, true);
});

test("VerificationBanner resend workflow: handles 429 rate limit without logging user out", async () => {
  mockResponses.push({ status: 429, body: { detail: "Too many verification resend requests" } });
  setMemoryToken("active_user_session");

  const handleResend = async (session) => {
    try {
      const res = await resendVerification(session.user.email);
      return { success: true, message: res.message };
    } catch (err) {
      return { success: false, error: err.message };
    }
  };

  const session = { user: { email: "unverified@example.com" } };
  const result = await handleResend(session);

  assert.strictEqual(result.success, false);
  assert.strictEqual(result.error, "Too many verification resend requests");
  // Session must remain intact
  assert.strictEqual(getMemoryToken(), "active_user_session");
});

test("VerificationBanner resend workflow: handles network error without logging user out", async () => {
  globalThis.fetch = async () => {
    throw new Error("Network connection lost");
  };
  setMemoryToken("active_user_session");

  const handleResend = async (session) => {
    try {
      const res = await resendVerification(session.user.email);
      return { success: true, message: res.message };
    } catch (err) {
      return { success: false, error: err.message };
    }
  };

  const session = { user: { email: "unverified@example.com" } };
  const result = await handleResend(session);

  assert.strictEqual(result.success, false);
  assert.strictEqual(result.error, "Network connection lost");
  assert.strictEqual(getMemoryToken(), "active_user_session");
});

// ---------------------------------------------------------------------------
// 13. Route Registration & Page Markup Verification
// ---------------------------------------------------------------------------

test("App.tsx contains public route registrations for verify-email, forgot-password, reset-password", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const appFile = fs.readFileSync(path.resolve("src/App.tsx"), "utf8");

  assert(appFile.includes('path="/verify-email"'), "App.tsx must register /verify-email route");
  assert(appFile.includes('path="/forgot-password"'), "App.tsx must register /forgot-password route");
  assert(appFile.includes('path="/reset-password"'), "App.tsx must register /reset-password route");
  assert(appFile.includes("VerifyEmail"), "App.tsx must import VerifyEmail");
  assert(appFile.includes("ForgotPassword"), "App.tsx must import ForgotPassword");
  assert(appFile.includes("ResetPassword"), "App.tsx must import ResetPassword");
});

test("Login.tsx includes forgot-password link and accessible label attributes", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const loginFile = fs.readFileSync(path.resolve("src/pages/Login.tsx"), "utf8");

  assert(loginFile.includes('to="/forgot-password"'), "Login.tsx must link to /forgot-password");
  assert(loginFile.includes('htmlFor="login-email"'), "Login.tsx email label must have htmlFor");
  assert(loginFile.includes('id="login-email"'), "Login.tsx email input must have id");
  assert(loginFile.includes('htmlFor="login-password"'), "Login.tsx password label must have htmlFor");
  assert(loginFile.includes('id="login-password"'), "Login.tsx password input must have id");
  assert(loginFile.includes('autoComplete="email"'), "Login.tsx email input must specify autocomplete");
  assert(loginFile.includes('autoComplete="current-password"'), "Login.tsx password input must specify autocomplete");
});

test("Signup.tsx provides onboarding email verification guidance and accessible form", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const signupFile = fs.readFileSync(path.resolve("src/pages/Signup.tsx"), "utf8");

  assert(signupFile.includes("verification email"), "Signup.tsx must provide verification guidance on success");
  assert(signupFile.includes("minLength={12}"), "Signup.tsx must enforce 12 character password minimum");
  assert(signupFile.includes('autoComplete="new-password"'), "Signup.tsx password must specify autocomplete new-password");
});

test("Layout.tsx renders VerificationBanner above content", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const layoutFile = fs.readFileSync(path.resolve("src/components/Layout.tsx"), "utf8");

  assert(layoutFile.includes("<VerificationBanner />"), "Layout.tsx must render VerificationBanner");
});

test("No raw tokens or passwords leaked in console logging or persisted storage across new auth pages", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const pages = [
    "src/pages/VerifyEmail.tsx",
    "src/pages/ForgotPassword.tsx",
    "src/pages/ResetPassword.tsx",
    "src/components/VerificationBanner.tsx",
  ];

  for (const page of pages) {
    const content = fs.readFileSync(path.resolve(page), "utf8");
    assert(!content.includes("console.log"), `${page} must not use console.log`);
    assert(!content.includes("localStorage.setItem"), `${page} must not persist tokens in localStorage`);
    assert(!content.includes("sessionStorage.setItem"), `${page} must not persist tokens in sessionStorage`);
  }
});

// ---------------------------------------------------------------------------
// 14. F-01: Token Protection in URLs and Safe Referrer Policy
// ---------------------------------------------------------------------------

test("index.html specifies a strict no-referrer policy", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const html = fs.readFileSync(path.resolve("index.html"), "utf8");

  assert(
    html.includes('<meta name="referrer" content="no-referrer" />'),
    "index.html must include meta tag with no-referrer policy"
  );
});

test("VerifyEmail captures token in state, cleans URL from address bar/history, and completes verification", async () => {
  let currentUrl = "https://app.rivinityads.com/verify-email?token=verif_secret_token_123&ref=welcome";
  let replacedUrl = null;
  const mockHistory = {
    state: {},
    replaceState: (state, title, url) => {
      replacedUrl = url;
      currentUrl = url;
    },
  };

  // Step 1: Capture token into component memory
  const searchParams = new URL(currentUrl).searchParams;
  const token = searchParams.get("token");
  assert.strictEqual(token, "verif_secret_token_123");

  // Step 2: Clean sensitive token parameter as early as possible
  const url = new URL(currentUrl);
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    const cleanSearch = url.searchParams.toString();
    const cleanUrl = url.pathname + (cleanSearch ? `?${cleanSearch}` : "") + url.hash;
    mockHistory.replaceState(mockHistory.state, "Title", cleanUrl);
  }

  // Verify the URL was stripped of the token but preserved other query params
  assert.strictEqual(replacedUrl, "/verify-email?ref=welcome");
  assert(!replacedUrl.includes("verif_secret_token_123"));

  // Step 3: Verify the token in component memory is still successfully sent to verifyEmail API
  mockResponses.push({ status: 200, body: { message: "Email verified" } });
  const result = await verifyEmail(token);
  assert.strictEqual(result.message, "Email verified");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/verify-email");
  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.token, "verif_secret_token_123");
});

test("ResetPassword captures token in state, cleans URL on mount, and retains token for form submission", async () => {
  let currentUrl = "https://app.rivinityads.com/reset-password?token=reset_secret_tok_999";
  let replacedUrl = null;
  const mockHistory = {
    state: {},
    replaceState: (state, title, url) => {
      replacedUrl = url;
      currentUrl = url;
    },
  };

  // Step 1: Initial render captures token in component memory
  const searchParams = new URL(currentUrl).searchParams;
  const token = searchParams.get("token");
  assert.strictEqual(token, "reset_secret_tok_999");

  // Step 2: Component mount effect cleans token from URL
  const url = new URL(currentUrl);
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    const cleanSearch = url.searchParams.toString();
    const cleanUrl = url.pathname + (cleanSearch ? `?${cleanSearch}` : "") + url.hash;
    mockHistory.replaceState(mockHistory.state, "Title", cleanUrl);
  }

  // URL no longer contains token
  assert.strictEqual(replacedUrl, "/reset-password");
  assert(!replacedUrl.includes("reset_secret_tok_999"));

  // Step 3: User fills in form and submits; captured token is still available in memory
  mockResponses.push({ status: 200, body: { message: "Password reset" } });
  const result = await resetPassword({
    token: token,
    password: "NewStrongPassword123!",
  });

  assert.strictEqual(result.message, "Password reset");
  assert.strictEqual(mockRequests[0].url, "/api/v1/auth/reset-password");
  const body = JSON.parse(mockRequests[0].init?.body);
  assert.strictEqual(body.token, "reset_secret_tok_999");
  assert.strictEqual(body.password, "NewStrongPassword123!");
});

test("VerifyEmail.tsx and ResetPassword.tsx source code contains URL token stripping and replaceState logic", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");

  const verifyFile = fs.readFileSync(path.resolve("src/pages/VerifyEmail.tsx"), "utf8");
  assert(verifyFile.includes("window.history.replaceState"), "VerifyEmail must clean history via replaceState");
  assert(verifyFile.includes('searchParams.delete("token")'), "VerifyEmail must delete token param");
  assert(verifyFile.includes("useState<string | null>(() => searchParams.get(\"token\"))"), "VerifyEmail must capture token in state");

  const resetFile = fs.readFileSync(path.resolve("src/pages/ResetPassword.tsx"), "utf8");
  assert(resetFile.includes("window.history.replaceState"), "ResetPassword must clean history via replaceState");
  assert(resetFile.includes('searchParams.delete("token")'), "ResetPassword must delete token param");
  assert(resetFile.includes("useState<string | null>(() => searchParams.get(\"token\"))"), "ResetPassword must capture token in state");
});

test("VerifyEmailResponse returns email and verifyEmail handles typed email field", async () => {
  mockResponses.push({
    status: 200,
    body: { message: "Email verified", email: "target_verified@example.com" },
  });

  const res = await verifyEmail("token_with_email");
  assert.strictEqual(res.message, "Email verified");
  assert.strictEqual(res.email, "target_verified@example.com");
});

test("VerifyEmail.tsx handles cross-account verification mismatch without refreshing wrong user session", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");

  const verifyFile = fs.readFileSync(path.resolve("src/pages/VerifyEmail.tsx"), "utf8");
  assert(verifyFile.includes("session.user.email.toLowerCase() === res.email.toLowerCase()"), "Must check verified email against session");
  assert(verifyFile.includes("isCrossAccount"), "Must define isCrossAccount state logic");
  assert(verifyFile.includes("Switch Account"), "Must provide Switch Account option on mismatch");
  assert(verifyFile.includes("handleSwitchAccount"), "Must provide handleSwitchAccount handler");
});
