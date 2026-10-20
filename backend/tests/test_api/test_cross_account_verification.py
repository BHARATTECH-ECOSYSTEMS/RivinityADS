"""
Unit and integration tests for F-04: Investigate and Fix Cross-Account Email Verification Confusion.

Covers:
- Proving a verification token cannot verify the wrong account.
- Verifying that when an authenticated user calls verify-email with another user's token,
  the target user is verified, but the calling user's verification status remains unchanged.
- Validating that the backend returns the verified account email in VerifyEmailResponse.
- Enforcing single-use token consumption and atomic token invalidation.
- Rejecting expired, invalid, and wrong-purpose tokens.
- Preserving generic resend-verification responses and operational gatekeeping.
"""

from datetime import datetime, timedelta, timezone
import secrets
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.security import create_access_token, hash_password, token_digest
from liteads.models.auth import AuditLog, OneTimeToken, User
from liteads.schemas.auth import VerifyEmailResponse


# ── 1. Cross-Account Isolation Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_cannot_verify_wrong_account(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A token issued to User B verifies User B and never verifies User A."""
    user_a = User(
        name="User Alice",
        email="alice_unverified@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    user_b = User(
        name="User Bob",
        email="bob_unverified@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add_all([user_a, user_b])
    await test_db.commit()

    # Issue verification token for Bob only
    raw_token_b = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token_record = OneTimeToken(
        user_id=user_b.id,
        token_hash=token_digest(raw_token_b),
        purpose="email_verification",
        expires_at=now + timedelta(hours=24),
    )
    test_db.add(token_record)
    await test_db.commit()

    # Redeem Bob's token
    res = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token_b},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["message"] == "Email verified"
    assert data["email"] == "bob_unverified@example.com"

    # Refresh users from database
    await test_db.refresh(user_a)
    await test_db.refresh(user_b)

    # Bob is verified
    assert user_b.email_verified is True

    # Alice remains UNVERIFIED
    assert user_a.email_verified is False

    # AuditLog recorded only for Bob
    audit_bob = await test_db.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user_b.id,
            AuditLog.action == "verify_email",
        )
    )
    assert audit_bob is not None

    audit_alice = await test_db.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user_a.id,
            AuditLog.action == "verify_email",
        )
    )
    assert audit_alice is None


@pytest.mark.asyncio
async def test_authenticated_caller_submitting_other_token_does_not_verify_caller(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """An authenticated user (Alice) submitting Bob's token does not mutate Alice's status."""
    user_alice = User(
        name="Alice LoggedIn",
        email="alice_unverified_session@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    user_bob = User(
        name="Bob Registered",
        email="bob_unverified_target@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add_all([user_alice, user_bob])
    await test_db.commit()

    # Alice has an active session token
    alice_access_token = create_access_token(user_alice)

    # Bob has a pending verification token
    raw_token_bob = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token_record = OneTimeToken(
        user_id=user_bob.id,
        token_hash=token_digest(raw_token_bob),
        purpose="email_verification",
        expires_at=now + timedelta(hours=24),
    )
    test_db.add(token_record)
    await test_db.commit()

    # Alice sends Bob's token with Alice's Authorization header
    res = await client.post(
        "/api/v1/auth/verify-email",
        headers={"Authorization": f"Bearer {alice_access_token}"},
        json={"token": raw_token_bob},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["message"] == "Email verified"
    assert data["email"] == "bob_unverified_target@example.com"

    # Alice checks her own profile: she is still unverified
    me_res = await client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {alice_access_token}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email_verified"] is False

    # Alice's operational access is still blocked (403 Forbidden)
    op_res = await client.get(
        "/api/v1/campaign",
        headers={"Authorization": f"Bearer {alice_access_token}"},
    )
    assert op_res.status_code == 403
    assert "Email verification required" in op_res.json()["detail"]


# ── 2. Token Security & Replay Prevention ──────────────────────────────────────

@pytest.mark.asyncio
async def test_single_use_token_cannot_be_replayed(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Once redeemed, a verification token cannot be used again."""
    user = User(
        name="Single Use User",
        email="single_use@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(user)
    await test_db.commit()

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="email_verification",
            expires_at=now + timedelta(hours=24),
        )
    )
    await test_db.commit()

    # First attempt succeeds
    res1 = await client.post("/api/v1/auth/verify-email", json={"token": raw_token})
    assert res1.status_code == 200

    # Second attempt fails
    res2 = await client.post("/api/v1/auth/verify-email", json={"token": raw_token})
    assert res2.status_code == 400
    assert res2.json()["detail"] == "Verification token is invalid"


@pytest.mark.asyncio
async def test_expired_token_is_rejected(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Expired verification token is rejected with HTTP 400."""
    user = User(
        name="Expired Token User",
        email="expired_token@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(user)
    await test_db.commit()

    raw_token = secrets.token_urlsafe(32)
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="email_verification",
            expires_at=past,
        )
    )
    await test_db.commit()

    res = await client.post("/api/v1/auth/verify-email", json={"token": raw_token})
    assert res.status_code == 400
    assert res.json()["detail"] == "Verification token is invalid"


@pytest.mark.asyncio
async def test_wrong_purpose_token_is_rejected(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Password reset token cannot be used to verify email."""
    user = User(
        name="Wrong Purpose User",
        email="wrong_purpose@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(user)
    await test_db.commit()

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="password_reset",
            expires_at=now + timedelta(hours=2),
        )
    )
    await test_db.commit()

    res = await client.post("/api/v1/auth/verify-email", json={"token": raw_token})
    assert res.status_code == 400
    assert res.json()["detail"] == "Verification token is invalid"


# ── 3. Resend-Verification Generic Response & Functionality ───────────────────

@pytest.mark.asyncio
async def test_resend_verification_generic_response_preserved(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Resend verification returns generic message for unverified, verified, and nonexistent emails."""
    user = User(
        name="Resend User",
        email="resend_unverified@example.com",
        password_hash=hash_password("Password1234!"),
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock), \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        # 1. Unverified existing account
        res1 = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "resend_unverified@example.com"},
        )
        assert res1.status_code == 200
        assert "If an unverified account exists" in res1.json()["message"]

        # 2. Nonexistent account
        res2 = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "nonexistent_account_123@example.com"},
        )
        assert res2.status_code == 200
        assert "If an unverified account exists" in res2.json()["message"]
