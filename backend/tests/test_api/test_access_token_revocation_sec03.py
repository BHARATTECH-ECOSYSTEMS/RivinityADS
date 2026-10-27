"""
SEC-03: Access-Token Revocation After Password Reset Regression Test Suite.

Covers all 9 mandatory requirements:
1. An access token issued before password reset is rejected after reset.
2. A newly issued access token is accepted after reset.
3. The old refresh token remains rejected.
4. The new password works and the old password fails.
5. Existing users with password_changed_at = NULL retain normal authentication behavior.
6. Tokens at the same-second timestamp boundary are handled correctly.
7. Missing or malformed iat values are handled safely.
8. A failed or rolled-back reset does not persist the timestamp or partially revoke credentials.
9. Existing verified-user, role, and tenant authorization behavior remains intact.
"""

from datetime import UTC, datetime, timedelta, timezone
import math
from unittest.mock import AsyncMock, patch
import urllib.parse

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.security import create_access_token, hash_password, token_digest
from liteads.models.ad import Advertiser
from liteads.models.auth import AuditLog, OneTimeToken, RefreshToken, User


def _forge_access_token(user_id: int, role: str, iat_val, exp_delta_minutes: int = 15) -> str:
    """Helper to forge a test access JWT with specific iat claim type and value."""
    secret = get_settings().auth.jwt_secret
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "role": role,
        "exp": (now + timedelta(minutes=exp_delta_minutes)).timestamp(),
    }
    if iat_val != "OMIT":
        payload["iat"] = iat_val
    return jwt.encode(payload, secret, algorithm="HS256")


# ── Requirement 1, 2, 3, 4: Core Flow Lifecycle ─────────────────────────────

@pytest.mark.asyncio
async def test_access_token_revocation_full_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """
    Requirements 1-4:
    - Access token issued before reset is rejected after reset (HTTP 401).
    - Newly issued access token is accepted after reset (HTTP 200).
    - Old refresh token remains rejected (HTTP 401).
    - New password works and old password fails.
    """
    # 1. Setup verified advertiser user with linked advertiser account
    adv = Advertiser(name="SEC-03 Advertiser", contact_email="sec03@example.com")
    test_db.add(adv)
    await test_db.flush()

    user = User(
        email="sec03@example.com",
        name="SEC-03 User",
        password_hash=hash_password("OldPassword123!"),
        role="advertiser",
        advertiser_id=adv.id,
        is_active=True,
        email_verified=True,
        password_changed_at=None,
    )
    test_db.add(user)
    await test_db.commit()

    # 2. Login to obtain pre-reset access token and refresh cookie
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "sec03@example.com", "password": "OldPassword123!"},
        )
        assert login_res.status_code == 200
        old_access_token = login_res.json()["access_token"]
        refresh_cookie = login_res.cookies.get("refresh_token")

    # Verify old access token works on /auth/me and operational route before reset
    pre_res = await client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_access_token}"},
    )
    assert pre_res.status_code == 200
    pre_op_res = await client.get(
        "/api/v1/campaign",
        headers={"Authorization": f"Bearer {old_access_token}"},
    )
    assert pre_op_res.status_code == 200

    # 3. Request and complete password reset
    captured_token = None

    async def mock_capture_email(to_address, subject, text_content, html_content=None):
        nonlocal captured_token
        parsed = urllib.parse.urlparse(text_content.split()[text_content.split().index([w for w in text_content.split() if 'token=' in w][0])])
        captured_token = urllib.parse.parse_qs(parsed.query)["token"][0]

    with patch("liteads.ad_server.routers.auth.send_email", side_effect=mock_capture_email), \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):
        req_res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "sec03@example.com"},
        )
        assert req_res.status_code == 200

    assert captured_token is not None

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock), \
         patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        reset_res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": captured_token, "password": "BrandNewPassword123!"},
        )
        assert reset_res.status_code == 200

    # Requirement 1: Old access token is immediately rejected on /auth/me and operational routes
    post_res = await client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_access_token}"},
    )
    assert post_res.status_code == 401
    assert post_res.json()["detail"] == "Invalid or expired token"

    post_op_res = await client.get(
        "/api/v1/campaign",
        headers={"Authorization": f"Bearer {old_access_token}"},
    )
    assert post_op_res.status_code == 401

    # Requirement 3: Old refresh token is rejected
    refresh_fail = await client.post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": refresh_cookie},
    )
    assert refresh_fail.status_code == 401

    # Requirement 4: Old password fails, new password succeeds
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        old_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "sec03@example.com", "password": "OldPassword123!"},
        )
        assert old_login.status_code == 401

        new_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "sec03@example.com", "password": "BrandNewPassword123!"},
        )
        assert new_login.status_code == 200
        new_access_token = new_login.json()["access_token"]

    # Requirement 2: Newly issued access token is accepted
    new_auth_res = await client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_access_token}"},
    )
    assert new_auth_res.status_code == 200
    assert new_auth_res.json()["email"] == "sec03@example.com"

    new_op_res = await client.get(
        "/api/v1/campaign",
        headers={"Authorization": f"Bearer {new_access_token}"},
    )
    assert new_op_res.status_code == 200


# ── Requirement 5: Existing Users with password_changed_at = NULL ────────────

@pytest.mark.asyncio
async def test_existing_users_with_null_password_changed_at(client: AsyncClient, test_db: AsyncSession):
    """Requirement 5: Existing users with password_changed_at = NULL retain normal authentication."""
    adv = Advertiser(name="Null Timestamp Adv", contact_email="null_ts@example.com")
    test_db.add(adv)
    await test_db.flush()

    user = User(
        email="null_ts@example.com",
        name="Null Timestamp User",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        advertiser_id=adv.id,
        is_active=True,
        email_verified=True,
        password_changed_at=None,
    )
    test_db.add(user)
    await test_db.commit()

    token = create_access_token(user)
    res = await client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["email"] == "null_ts@example.com"


# ── Requirement 6: Same-Second Timestamp Boundary Handling ───────────────────

@pytest.mark.asyncio
async def test_tokens_at_same_second_timestamp_boundary(client: AsyncClient, test_db: AsyncSession):
    """
    Requirement 6: Tokens at the same-second timestamp boundary are handled correctly.
    - Second-level token at pwd_changed_sec (same second) is accepted (not falsely invalidated).
    - Second-level token at pwd_changed_sec - 1 is rejected.
    - Second-level token at pwd_changed_sec + 1 is accepted.
    - Sub-second float token issued before reset is rejected.
    - Sub-second float token issued after reset is accepted.
    """
    boundary_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    boundary_ts = boundary_dt.replace(tzinfo=timezone.utc).timestamp()
    boundary_sec = int(boundary_ts)

    user = User(
        email="boundary@example.com",
        name="Boundary User",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
        password_changed_at=boundary_dt,
    )
    test_db.add(user)
    await test_db.commit()

    # 6a. Integer iat in the same second: accepted (avoids false invalidation)
    tok_same_sec = _forge_access_token(user.id, user.role, boundary_sec)
    res_same = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_same_sec}"})
    assert res_same.status_code == 200

    # 6b. Integer iat one second prior: rejected
    tok_prev_sec = _forge_access_token(user.id, user.role, boundary_sec - 1)
    res_prev = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_prev_sec}"})
    assert res_prev.status_code == 401
    assert res_prev.json()["detail"] == "Invalid or expired token"

    # 6c. Integer iat one second after: accepted
    tok_next_sec = _forge_access_token(user.id, user.role, boundary_sec + 1)
    res_next = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_next_sec}"})
    assert res_next.status_code == 200

    # 6d. Sub-second float iat before reset: rejected
    tok_sub_prev = _forge_access_token(user.id, user.role, boundary_ts - 0.05)
    res_sub_prev = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_sub_prev}"})
    assert res_sub_prev.status_code == 401

    # 6e. Sub-second float iat after reset: accepted
    tok_sub_next = _forge_access_token(user.id, user.role, boundary_ts + 0.05)
    res_sub_next = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_sub_next}"})
    assert res_sub_next.status_code == 200


# ── Requirement 7: Missing or Malformed iat Claim Handling ───────────────────

@pytest.mark.asyncio
async def test_missing_and_malformed_iat_claims(client: AsyncClient, test_db: AsyncSession):
    """Requirement 7: Missing or malformed iat values are safely rejected with HTTP 401."""
    user = User(
        email="malformed_iat@example.com",
        name="Malformed IAT User",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
        password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )
    test_db.add(user)
    await test_db.commit()

    malformed_cases = [
        ("OMIT", "missing iat claim"),
        ("not_a_number", "string iat claim"),
        (True, "boolean True iat claim"),
        (False, "boolean False iat claim"),
        (-100, "negative integer iat claim"),
        (0, "zero integer iat claim"),
        (-50.5, "negative float iat claim"),
        ([123456], "list iat claim"),
        ({"ts": 123456}, "dict iat claim"),
        (None, "explicit None iat claim"),
    ]

    for bad_iat, desc in malformed_cases:
        token = _forge_access_token(user.id, user.role, bad_iat)
        res = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401, f"Expected 401 for {desc}, got {res.status_code}"
        assert res.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
async def test_nan_and_infinity_iat_claims(client: AsyncClient, test_db: AsyncSession):
    """
    Explicit edge-case test: Verify that access tokens containing NaN and positive/negative
    Infinity iat claims are safely rejected with HTTP 401 and never cause an unexpected 500 server error.
    """
    user = User(
        email="nan_inf_user@example.com",
        name="NaN Inf User",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
        password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )
    test_db.add(user)
    await test_db.commit()

    nan_inf_cases = [
        (float("nan"), "NaN iat claim"),
        (float("inf"), "positive Infinity iat claim"),
        (float("-inf"), "negative Infinity iat claim"),
    ]

    for bad_iat, desc in nan_inf_cases:
        token = _forge_access_token(user.id, user.role, bad_iat)
        res = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401, f"Expected 401 for {desc}, got {res.status_code}: {res.text}"
        assert res.json()["detail"] == "Invalid or expired token"


# ── Requirement 8: Failed Reset Rollback Semantics ───────────────────────────


@pytest.mark.asyncio
async def test_failed_reset_does_not_persist_timestamp_or_revoke_token(client: AsyncClient, test_db: AsyncSession):
    """Requirement 8: A failed or rolled-back reset does not persist timestamp or revoke credentials."""
    user = User(
        email="failed_reset@example.com",
        name="Failed Reset User",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
        password_changed_at=None,
    )
    test_db.add(user)
    await test_db.commit()

    token = create_access_token(user)

    # Attempt reset with an invalid reset token
    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "non_existent_reset_token", "password": "NewPassword123!"},
        )
        assert res.status_code == 400

    # Verify user record is untouched
    refreshed_user = await test_db.scalar(select(User).where(User.email == "failed_reset@example.com"))
    assert refreshed_user.password_changed_at is None

    # Pre-existing access token continues to work
    auth_res = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert auth_res.status_code == 200


# ── Requirement 9: Verified-User, Role, and Tenant Scoping Intact ────────────

@pytest.mark.asyncio
async def test_verified_user_role_and_tenant_authorization_preserved(client: AsyncClient, test_db: AsyncSession):
    """Requirement 9: Existing verified-user, role, and tenant authorization behavior remains intact."""
    adv_a = Advertiser(name="Adv A", contact_email="adv_a@example.com")
    adv_b = Advertiser(name="Adv B", contact_email="adv_b@example.com")
    test_db.add_all([adv_a, adv_b])
    await test_db.flush()

    user_a = User(
        email="tenant_a@example.com",
        name="Tenant A",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        advertiser_id=adv_a.id,
        is_active=True,
        email_verified=True,
        password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )
    user_unverified = User(
        email="unverified_tenant@example.com",
        name="Unverified Tenant",
        password_hash=hash_password("Password123!"),
        role="advertiser",
        advertiser_id=adv_b.id,
        is_active=True,
        email_verified=False,
        password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )
    test_db.add_all([user_a, user_unverified])
    await test_db.commit()

    # Newly issued tokens for both (issued after password_changed_at)
    token_a = create_access_token(user_a)
    token_unverified = create_access_token(user_unverified)

    # 1. Verified user A can access operational routes
    res_a = await client.get("/api/v1/campaign", headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.status_code == 200

    # 2. Verified user A is blocked by require_roles from admin routes (HTTP 403)
    res_admin = await client.get("/api/v1/moderation/creatives/pending", headers={"Authorization": f"Bearer {token_a}"})
    assert res_admin.status_code == 403

    # 3. Unverified user can access /auth/me
    res_unv_me = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unverified}"})
    assert res_unv_me.status_code == 200
    assert res_unv_me.json()["email_verified"] is False

    # 4. Unverified user is blocked by verified_user from operational routes (HTTP 403)
    res_unv_op = await client.get("/api/v1/campaign", headers={"Authorization": f"Bearer {token_unverified}"})
    assert res_unv_op.status_code == 403
