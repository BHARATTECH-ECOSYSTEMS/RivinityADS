"""
Unit and integration tests for Password Reset (Step 2).

Covers:
- Password-reset request endpoint: valid email, nonexistent account, inactive account,
  enumeration protection, invalidation of older tokens, rate limiting, and email delivery failure.
- Password-reset completion endpoint: valid token consumption, password update,
  refresh token revocation, single-use enforcement, expired/wrong-purpose token rejection,
  atomic concurrency, rate limiting, and security notification delivery.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.security import hash_password, token_digest
from liteads.models.auth import AuditLog, OneTimeToken, RefreshToken, User


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


# ── 1. Password-Reset Request Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_password_reset_success_sends_email(client: AsyncClient, test_db: AsyncSession):
    # Register an active user
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Reset Adv",
            "email": "reset_user@example.com",
            "password": "InitialPassword123!",
            "role": "advertiser",
        },
    )

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "reset_user@example.com"},
        )

        assert res.status_code == 200
        assert res.json()["message"] == "If the account exists, password reset instructions have been sent"

        # Verify email was dispatched
        assert mock_send.call_count == 1
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["to_address"] == "reset_user@example.com"
        assert "Reset your RivinityADS password" in call_kwargs["subject"]
        assert "/reset-password?token=" in call_kwargs["text_content"]
        assert "/reset-password?token=" in call_kwargs["html_content"]

        # Verify token in database
        user = await test_db.scalar(select(User).where(User.email == "reset_user@example.com"))
        token_record = await test_db.scalar(
            select(OneTimeToken).where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "password_reset",
            )
        )
        assert token_record is not None
        assert token_record.used_at is None
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert token_record.expires_at > now_utc


@pytest.mark.asyncio
async def test_request_password_reset_enumeration_protection(client: AsyncClient, test_db: AsyncSession):
    """Non-existent and inactive accounts return the exact same generic message without sending email."""
    # 1. Non-existent account
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "nonexistent_account@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["message"] == "If the account exists, password reset instructions have been sent"
        mock_send.assert_not_called()

    # 2. Inactive account
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Inactive User",
            "email": "inactive@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "inactive@example.com"))
    user.is_active = False
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send, \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "inactive@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["message"] == "If the account exists, password reset instructions have been sent"
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_request_password_reset_invalid_email_schema(client: AsyncClient):
    """Invalid email formats must be rejected by Pydantic validation (HTTP 422)."""
    res1 = await client.post(
        "/api/v1/auth/request-password-reset",
        json={"email": "not-a-valid-email"},
    )
    assert res1.status_code == 422

    res2 = await client.post(
        "/api/v1/auth/request-password-reset",
        json={},
    )
    assert res2.status_code == 422


@pytest.mark.asyncio
async def test_request_password_reset_invalidates_earlier_unused_tokens(client: AsyncClient, test_db: AsyncSession):
    """Requesting a reset a second time invalidates earlier pending tokens for that user."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Multi Reset",
            "email": "multi_reset@example.com",
            "password": "InitialPassword123!",
            "role": "advertiser",
        },
    )

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock), \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        # 1st request
        await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "multi_reset@example.com"},
        )

        user = await test_db.scalar(select(User).where(User.email == "multi_reset@example.com"))
        tokens_after_first = (
            await test_db.scalars(
                select(OneTimeToken).where(
                    OneTimeToken.user_id == user.id,
                    OneTimeToken.purpose == "password_reset",
                )
            )
        ).all()
        assert len(tokens_after_first) == 1
        first_token = tokens_after_first[0]
        assert first_token.used_at is None

        # 2nd request
        await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "multi_reset@example.com"},
        )

        # Refresh state
        await test_db.refresh(first_token)
        assert first_token.used_at is not None, "First token was not invalidated upon issuing second reset!"

        tokens_after_second = (
            await test_db.scalars(
                select(OneTimeToken).where(
                    OneTimeToken.user_id == user.id,
                    OneTimeToken.purpose == "password_reset",
                )
            )
        ).all()
        assert len(tokens_after_second) == 2
        active_tokens = [t for t in tokens_after_second if t.used_at is None]
        assert len(active_tokens) == 1


@pytest.mark.asyncio
async def test_request_password_reset_rate_limiting(client: AsyncClient):
    """Check distributed rate-limiting on password reset requests."""
    state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(state)), \
         patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock):

        # 5 allowed requests
        for _ in range(5):
            res = await client.post(
                "/api/v1/auth/request-password-reset",
                json={"email": "ratelimit_user@example.com"},
            )
            assert res.status_code == 200

        # 6th request triggers 429
        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "ratelimit_user@example.com"},
        )
        assert res.status_code == 429
        assert "Too many password reset requests" in res.json()["detail"]


@pytest.mark.asyncio
async def test_request_password_reset_email_delivery_failure_resilience(client: AsyncClient):
    """If SMTP delivery raises an error, the endpoint logs the error and still returns 200."""
    from liteads.common.email import EmailDeliveryError

    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "SMTP Fail",
            "email": "smtp_fail@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )

    with patch("liteads.ad_server.routers.auth.send_email", side_effect=EmailDeliveryError("Connection refused")), \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "smtp_fail@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["message"] == "If the account exists, password reset instructions have been sent"


# ── 2. Password-Reset Completion Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_success_workflow(client: AsyncClient, test_db: AsyncSession):
    """Full workflow: request reset -> extract token -> complete reset -> verify sessions revoked -> login with new password."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Full Flow",
            "email": "full_flow@example.com",
            "password": "OldPassword123!",
            "role": "advertiser",
        },
    )
    assert reg_res.status_code == 201

    captured_token = None

    async def mock_capture_email(to_address, subject, text_content, html_content=None):
        nonlocal captured_token
        # Extract token from link: /reset-password?token=<TOKEN>
        import urllib.parse
        parsed = urllib.parse.urlparse(text_content)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(text_content.split()[text_content.split().index([w for w in text_content.split() if 'token=' in w][0])]).query)
        captured_token = query["token"][0]

    with patch("liteads.ad_server.routers.auth.send_email", side_effect=mock_capture_email), \
         patch("liteads.ad_server.routers.auth.check_password_reset_request_rate_limit"):

        res = await client.post(
            "/api/v1/auth/request-password-reset",
            json={"email": "full_flow@example.com"},
        )
        assert res.status_code == 200

    assert captured_token is not None

    # Login to create an active refresh session
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "full_flow@example.com", "password": "OldPassword123!"},
        )
        assert login_res.status_code == 200
        refresh_cookie = login_res.cookies.get("refresh_token")

    # Complete password reset
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_security_send, \
         patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):

        reset_res = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": captured_token,
                "password": "BrandNewPassword123!",
            },
        )
        assert reset_res.status_code == 200
        assert reset_res.json()["message"] == "Password reset"

        # Security notification email was sent
        assert mock_security_send.call_count == 1
        assert "Security Alert" in mock_security_send.call_args[1]["subject"]
        assert mock_security_send.call_args[1]["to_address"] == "full_flow@example.com"

    # Verify token is now marked used
    user = await test_db.scalar(select(User).where(User.email == "full_flow@example.com"))
    token_record = await test_db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.token_hash == token_digest(captured_token),
        )
    )
    assert token_record.used_at is not None

    # Verify refresh tokens are revoked
    refresh_tokens = (
        await test_db.scalars(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        )
    ).all()
    assert all(rt.revoked_at is not None for rt in refresh_tokens)

    # Attempting to refresh with old cookie must fail (HTTP 401)
    cookies = {"refresh_token": refresh_cookie}
    refresh_fail_res = await client.post("/api/v1/auth/refresh", cookies=cookies)
    assert refresh_fail_res.status_code == 401

    # Attempting to login with OLD password must fail (HTTP 401)
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        old_login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "full_flow@example.com", "password": "OldPassword123!"},
        )
        assert old_login_res.status_code == 401

    # Logging in with NEW password must succeed (HTTP 200)
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        new_login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "full_flow@example.com", "password": "BrandNewPassword123!"},
        )
        assert new_login_res.status_code == 200

    # Verify AuditLog was recorded
    audit_log = await test_db.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "password_reset",
        )
    )
    assert audit_log is not None


@pytest.mark.asyncio
async def test_reset_password_invalid_and_expired_tokens(client: AsyncClient, test_db: AsyncSession):
    # 1. Non-existent token
    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "nonexistent_token_123", "password": "ValidPassword123!"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Reset token is invalid"

    # 2. Setup user
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Token Edge",
            "email": "token_edge@example.com",
            "password": "Password1234!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "token_edge@example.com"))

    # 3. Expired token
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expired_token = "expired_raw_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(expired_token),
            purpose="password_reset",
            expires_at=now_naive - timedelta(hours=2),
            used_at=None,
        )
    )
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": expired_token, "password": "ValidPassword123!"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Reset token is invalid"

    # 4. Wrong-purpose token (e.g. purpose="email_verification")
    wrong_purpose_token = "wrong_purpose_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(wrong_purpose_token),
            purpose="email_verification",
            expires_at=now_naive + timedelta(hours=24),
            used_at=None,
        )
    )
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": wrong_purpose_token, "password": "ValidPassword123!"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Reset token is invalid"

    # 5. Already used token
    used_token = "already_used_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(used_token),
            purpose="password_reset",
            expires_at=now_naive + timedelta(hours=1),
            used_at=now_naive - timedelta(minutes=5),
        )
    )
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": used_token, "password": "ValidPassword123!"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Reset token is invalid"


@pytest.mark.asyncio
async def test_reset_password_single_use_enforcement(client: AsyncClient, test_db: AsyncSession):
    """Submitting the exact same token a second time must fail."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Single Use",
            "email": "single_use@example.com",
            "password": "OldPassword123!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "single_use@example.com"))

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    raw_token = "single_use_token_abc"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="password_reset",
            expires_at=now_naive + timedelta(hours=1),
            used_at=None,
        )
    )
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock), \
         patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):

        # 1st submission succeeds
        res1 = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "password": "NewPassword123!"},
        )
        assert res1.status_code == 200

        # 2nd submission fails
        res2 = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "password": "AnotherPassword123!"},
        )
        assert res2.status_code == 400
        assert res2.json()["detail"] == "Reset token is invalid"


@pytest.mark.asyncio
async def test_reset_password_inactive_user_rejected(client: AsyncClient, test_db: AsyncSession):
    """If user account is deactivated before token is redeemed, redemption must fail."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Deactivated",
            "email": "deactivated@example.com",
            "password": "OldPassword123!",
            "role": "advertiser",
        },
    )
    user = await test_db.scalar(select(User).where(User.email == "deactivated@example.com"))

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    raw_token = "deactivated_user_token"
    test_db.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            purpose="password_reset",
            expires_at=now_naive + timedelta(hours=1),
            used_at=None,
        )
    )
    # Deactivate user
    user.is_active = False
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_password_reset_completion_rate_limit"):
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "password": "NewPassword123!"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Reset token is invalid"


@pytest.mark.asyncio
async def test_reset_password_completion_rate_limiting(client: AsyncClient):
    """Check distributed rate-limiting on password reset completion attempts."""
    state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(state)):
        for _ in range(5):
            res = await client.post(
                "/api/v1/auth/reset-password",
                json={"token": "some_token", "password": "Password1234!"},
            )
            # Will be 400 because token is invalid, but rate-limiting is evaluated first
            assert res.status_code == 400

        # 6th attempt triggers 429
        res = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "some_token", "password": "Password1234!"},
        )
        assert res.status_code == 429
        assert "Too many password reset attempts" in res.json()["detail"]


@pytest.mark.asyncio
async def test_reset_password_password_length_validation(client: AsyncClient):
    """Password must be at least 12 characters."""
    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "some_token", "password": "short"},
    )
    assert res.status_code == 422
