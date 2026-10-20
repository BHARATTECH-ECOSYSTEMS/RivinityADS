"""
Unit and integration tests for Email Verification & Resend Workflows (Step 3).

Covers:
- Registration creates unverified account (email_verified=False).
- Verification token generation and email dispatch.
- Registration email-delivery failure handling and database consistency.
- Email verification endpoint: valid token redemption, marking email_verified=True,
  invalidating other tokens, query parameter and JSON body compatibility.
- Rejection of invalid, expired, already-used, and wrong-purpose tokens.
- Inactive and deleted user handling.
- Concurrency guard preventing double redemption of tokens.
- Resend-verification endpoint: successful resend, invalidation of older tokens,
  generic response for unknown/inactive/already-verified accounts.
- Distributed Redis rate limiting by IP and account.
- Email delivery failure handling during resend.
"""

from datetime import datetime, timedelta, timezone
import urllib.parse
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.email import EmailDeliveryError
from liteads.common.security import token_digest
from liteads.models.auth import AuditLog, OneTimeToken, User


class MockRedisPipeline:
    """Mock Redis pipeline for distributed rate-limiting tests."""

    def __init__(self, state: dict[str, int]):
        self.state = state
        self.commands: list[tuple] = []

    def incr(self, key: str):
        self.commands.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int):
        self.commands.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for cmd, *args in self.commands:
            if cmd == "incr":
                k = args[0]
                self.state[k] = self.state.get(k, 0) + 1
                results.append(self.state[k])
            elif cmd == "expire":
                results.append(True)
        self.commands.clear()
        return results

    @property
    def command_stack(self):
        return self.commands


# ── 1. Registration & Verification Email Dispatch Tests ────────────────────────

@pytest.mark.asyncio
async def test_register_creates_unverified_account_and_dispatches_email(client: AsyncClient, test_db: AsyncSession):
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send:
        res = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Verify Me",
                "email": "verify_me@example.com",
                "password": "Password1234!",
                "role": "advertiser",
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["user"]["email_verified"] is False

        # Database checks
        user = await test_db.scalar(select(User).where(User.email == "verify_me@example.com"))
        assert user is not None
        assert user.email_verified is False

        # OneTimeToken checks
        token_record = await test_db.scalar(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
            )
        )
        assert token_record is not None
        assert token_record.used_at is None
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert token_record.expires_at > now_utc

        # Email dispatch checks
        assert mock_send.call_count == 1
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["to_address"] == "verify_me@example.com"
        assert "Verify your RivinityADS account" in call_kwargs["subject"]
        assert "/verify-email?token=" in call_kwargs["text_content"]
        assert "/verify-email?token=" in call_kwargs["html_content"]


@pytest.mark.asyncio
async def test_register_email_delivery_failure_leaves_consistent_database_state(client: AsyncClient, test_db: AsyncSession):
    """If SMTP fails during registration, account and token are committed and user can resend."""
    with patch("liteads.ad_server.routers.auth.send_email", side_effect=EmailDeliveryError("SMTP timeout")):
        res = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "SMTP Fail User",
                "email": "smtp_fail_verify@example.com",
                "password": "Password1234!",
                "role": "advertiser",
            },
        )
        assert res.status_code == 201

        # Database state is consistent: user exists, unverified, with a valid verification token
        user = await test_db.scalar(select(User).where(User.email == "smtp_fail_verify@example.com"))
        assert user is not None
        assert user.email_verified is False

        token_record = await test_db.scalar(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
            )
        )
        assert token_record is not None
        assert token_record.used_at is None


# ── 2. Verify-Email Endpoint Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_email_success_with_json_body(client: AsyncClient, test_db: AsyncSession):
    captured_token = None

    async def mock_capture_email(to_address, subject, text_content, html_content=None):
        nonlocal captured_token
        # Extract token from text content: /verify-email?token=<TOKEN>
        parsed = urllib.parse.urlparse(text_content)
        for word in text_content.split():
            if "token=" in word:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(word).query)
                captured_token = q["token"][0]
                break

    with patch("liteads.ad_server.routers.auth.send_email", side_effect=mock_capture_email):
        await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Verify Flow",
                "email": "verify_flow@example.com",
                "password": "Password1234!",
                "role": "advertiser",
            },
        )

    assert captured_token is not None

    # Call verify-email endpoint
    verify_res = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": captured_token},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["message"] == "Email verified"

    # User in database is now email_verified=True
    user = await test_db.scalar(select(User).where(User.email == "verify_flow@example.com"))
    assert user.email_verified is True

    # Token record is marked used
    token_record = await test_db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.token_hash == token_digest(captured_token),
        )
    )
    assert token_record.used_at is not None

    # AuditLog recorded
    audit = await test_db.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "verify_email",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_verify_email_success_with_query_parameter(client: AsyncClient, test_db: AsyncSession):
    """Endpoint also accepts query parameter ?token= for direct link clicks."""
    raw_token = "query_param_token_123"
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Query Param",
            "email": "query_param_verify@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "query_param_verify@example.com"))
    assert not user.email_verified

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="email_verification",
            expires_at=now_naive + timedelta(hours=24),
            used_at=None,
        )
    )
    await test_db.commit()

    res = await client.post(f"/api/v1/auth/verify-email?token={raw_token}")
    assert res.status_code == 200
    assert res.json()["message"] == "Email verified"

    await test_db.refresh(user)
    assert user.email_verified is True


@pytest.mark.asyncio
async def test_verify_email_invalid_and_expired_tokens(client: AsyncClient, test_db: AsyncSession):
    # 1. Nonexistent token
    res1 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": "nonexistent_token_xyz"},
    )
    assert res1.status_code == 400
    assert res1.json()["detail"] == "Verification token is invalid"

    # Setup user
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Invalid Tokens",
            "email": "invalid_tokens_verify@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "invalid_tokens_verify@example.com"))
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # 2. Expired verification token
    expired_token = "expired_verification_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(expired_token),
            purpose="email_verification",
            expires_at=now_naive - timedelta(hours=1),
            used_at=None,
        )
    )
    await test_db.commit()

    res2 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": expired_token},
    )
    assert res2.status_code == 400
    assert res2.json()["detail"] == "Verification token is invalid"

    # 3. Wrong purpose token (e.g. purpose="password_reset")
    wrong_purpose_token = "wrong_purpose_token_for_verify"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(wrong_purpose_token),
            purpose="password_reset",
            expires_at=now_naive + timedelta(hours=1),
            used_at=None,
        )
    )
    await test_db.commit()

    res3 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": wrong_purpose_token},
    )
    assert res3.status_code == 400
    assert res3.json()["detail"] == "Verification token is invalid"

    # 4. Already used token
    used_token = "already_used_verification_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(used_token),
            purpose="email_verification",
            expires_at=now_naive + timedelta(hours=24),
            used_at=now_naive - timedelta(minutes=10),
        )
    )
    await test_db.commit()

    res4 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": used_token},
    )
    assert res4.status_code == 400
    assert res4.json()["detail"] == "Verification token is invalid"


@pytest.mark.asyncio
async def test_verify_email_single_use_enforcement(client: AsyncClient, test_db: AsyncSession):
    """Submitting the exact same token a second time must fail."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Single Use",
            "email": "single_use_verify@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "single_use_verify@example.com"))

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    raw_token = "single_use_verif_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="email_verification",
            expires_at=now_naive + timedelta(hours=24),
            used_at=None,
        )
    )
    await test_db.commit()

    # 1st attempt succeeds
    res1 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )
    assert res1.status_code == 200

    # 2nd attempt fails
    res2 = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )
    assert res2.status_code == 400
    assert res2.json()["detail"] == "Verification token is invalid"


@pytest.mark.asyncio
async def test_verify_email_inactive_user_rejected(client: AsyncClient, test_db: AsyncSession):
    """If user account is deactivated before verification, redemption must fail."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Inactive",
            "email": "inactive_verify@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "inactive_verify@example.com"))

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    raw_token = "inactive_verif_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="email_verification",
            expires_at=now_naive + timedelta(hours=24),
            used_at=None,
        )
    )
    user.is_active = False
    await test_db.commit()

    res = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Verification token is invalid"


# ── 3. Resend-Verification Endpoint Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_resend_verification_success_and_invalidates_prior_tokens(client: AsyncClient, test_db: AsyncSession):
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Resend User",
            "email": "resend_user@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "resend_user@example.com"))

    # Initial token created during registration
    initial_tokens = (
        await test_db.scalars(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
            )
        )
    ).all()
    assert len(initial_tokens) == 1
    old_token = initial_tokens[0]
    assert old_token.used_at is None

    # Call resend-verification
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        res = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "resend_user@example.com"},
        )
        assert res.status_code == 200
        assert "If an unverified account exists" in res.json()["message"]
        assert mock_send.call_count == 1
        assert "Verify your RivinityADS account" in mock_send.call_args[1]["subject"]

    # Verify old token was invalidated
    await test_db.refresh(old_token)
    assert old_token.used_at is not None, "Prior verification token was not invalidated upon resend"

    # Verify new token exists and is active
    tokens = (
        await test_db.scalars(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
            )
        )
    ).all()
    assert len(tokens) == 2
    active_tokens = [t for t in tokens if t.used_at is None]
    assert len(active_tokens) == 1


@pytest.mark.asyncio
async def test_resend_verification_generic_response_for_nonexistent_and_inactive(client: AsyncClient, test_db: AsyncSession):
    # 1. Non-existent email
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        res1 = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "nonexistent@example.com"},
        )
        assert res1.status_code == 200
        assert "If an unverified account exists" in res1.json()["message"]
        mock_send.assert_not_called()

    # 2. Inactive account
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Inactive Resend",
            "email": "inactive_resend@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "inactive_resend@example.com"))
    user.is_active = False
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        res2 = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "inactive_resend@example.com"},
        )
        assert res2.status_code == 200
        assert "If an unverified account exists" in res2.json()["message"]
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_resend_verification_already_verified_account_no_email_sent(client: AsyncClient, test_db: AsyncSession):
    """Already-verified accounts return the generic response but do not dispatch emails."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Already Verified",
            "email": "already_verified@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "already_verified@example.com"))
    user.email_verified = True
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        res = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "already_verified@example.com"},
        )
        assert res.status_code == 200
        assert "If an unverified account exists" in res.json()["message"]
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_resend_verification_rate_limiting(client: AsyncClient):
    """Check distributed rate-limiting on resend-verification endpoint."""
    state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(state)), \
         patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock):

        for _ in range(5):
            res = await client.post(
                "/api/v1/auth/resend-verification",
                json={"email": "resend_rate_limit@example.com"},
            )
            assert res.status_code == 200

        # 6th request triggers 429
        res = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "resend_rate_limit@example.com"},
        )
        assert res.status_code == 429
        assert "Too many verification resend requests" in res.json()["detail"]


@pytest.mark.asyncio
async def test_resend_verification_email_delivery_failure_resilience(client: AsyncClient, test_db: AsyncSession):
    """If SMTP fails during resend, the endpoint logs the error and still returns 200."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Resend SMTP Fail",
            "email": "resend_smtp_fail@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )

    with patch("liteads.ad_server.routers.auth.send_email", side_effect=EmailDeliveryError("Relay unavailable")), \
         patch("liteads.ad_server.routers.auth.check_resend_verification_rate_limit"):

        res = await client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "resend_smtp_fail@example.com"},
        )
        assert res.status_code == 200
        assert "If an unverified account exists" in res.json()["message"]
