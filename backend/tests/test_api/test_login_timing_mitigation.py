"""
Unit and integration tests for F-03: Mitigate login timing-based account enumeration.

Covers:
- Dummy password hash validity, algorithm, and parameters matching production scrypt format.
- Nonexistent accounts trigger password verification with DUMMY_PASSWORD_HASH.
- Existing accounts with incorrect passwords verify against their stored hash.
- Identical generic HTTP 401 response for nonexistent accounts and wrong passwords.
- Valid credentials log in successfully.
- Inactive accounts remain rejected with generic 401.
- Malformed or unsupported stored hashes fail safely without bypass or 500 errors.
- Login rate limiting and F-02 trusted proxy client-IP resolution continue to function.
"""

import base64
from unittest.mock import patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import liteads.ad_server.routers.auth as auth_router
from liteads.common.config import get_settings
from liteads.common.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from liteads.models.auth import AuditLog, User


# ── 1. Dummy Password Hash Unit Tests ─────────────────────────────────────────

def test_dummy_password_hash_format_and_parameters():
    """Verify DUMMY_PASSWORD_HASH matches production scrypt algorithm and parameters."""
    parts = DUMMY_PASSWORD_HASH.split("$")
    assert len(parts) == 6, "DUMMY_PASSWORD_HASH must have exactly 6 $-delimited parts"
    assert parts[0] == "scrypt", "Algorithm must be scrypt"
    assert parts[1] == "16384", "N must be 16384 (2^14)"
    assert parts[2] == "8", "r must be 8"
    assert parts[3] == "1", "p must be 1"

    salt = base64.urlsafe_b64decode(parts[4].encode())
    digest = base64.urlsafe_b64decode(parts[5].encode())
    assert len(salt) == 16, "Salt must be 16 bytes"
    assert len(digest) == 64, "Digest must be 64 bytes"

    # Must verify cleanly as False for wrong passwords
    assert verify_password("arbitrary_wrong_password", DUMMY_PASSWORD_HASH) is False


def test_verify_password_safe_handling_of_malformed_hashes():
    """verify_password must return False on None, empty, or malformed hashes without crashing."""
    assert verify_password("password", None) is False
    assert verify_password("password", "") is False
    assert verify_password("password", "invalid_plain_text") is False
    assert verify_password("password", "argon2$16384$8$1$salt$digest") is False
    assert verify_password("password", "scrypt$invalid_n$8$1$salt$digest") is False
    assert verify_password("password", "scrypt$16384$8$1$too_short") is False
    assert verify_password("", DUMMY_PASSWORD_HASH) is False


# ── 2. Timing Mitigation & Account Enumeration Protection ─────────────────────

@pytest.mark.asyncio
async def test_login_nonexistent_account_executes_verify_password_with_dummy_hash(
    client: AsyncClient,
):
    """When account does not exist, verify_password is executed against DUMMY_PASSWORD_HASH."""
    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"), \
         patch.object(auth_router, "verify_password", wraps=auth_router.verify_password) as spy_verify:

        res = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent_enum_target@example.com",
                "password": "AttemptPassword123!",
            },
        )

        assert res.status_code == 401
        assert res.json() == {"detail": "Invalid email or password"}
        assert spy_verify.called
        call_args = spy_verify.call_args[0]
        assert call_args[0] == "AttemptPassword123!"
        assert call_args[1] == DUMMY_PASSWORD_HASH


@pytest.mark.asyncio
async def test_login_existing_account_wrong_password_executes_verify_with_stored_hash(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """When account exists, verify_password is executed against user's actual stored hash."""
    user = User(
        name="F03 Existing User",
        email="f03_existing@example.com",
        password_hash=hash_password("CorrectSecret123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"), \
         patch.object(auth_router, "verify_password", wraps=auth_router.verify_password) as spy_verify:

        res = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "f03_existing@example.com",
                "password": "WrongSecret999!",
            },
        )

        assert res.status_code == 401
        assert res.json() == {"detail": "Invalid email or password"}
        assert spy_verify.called
        call_args = spy_verify.call_args[0]
        assert call_args[0] == "WrongSecret999!"
        assert call_args[1] == user.password_hash


@pytest.mark.asyncio
async def test_login_response_identical_for_nonexistent_and_wrong_password(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Response code, headers, and body must be identical whether account exists or not."""
    user = User(
        name="F03 Probe User",
        email="f03_probe@example.com",
        password_hash=hash_password("CorrectSecret123!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        # Nonexistent account
        res_nonexistent = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "definitely_does_not_exist@example.com",
                "password": "Password1234!",
            },
        )

        # Existing account with wrong password
        res_wrong_pw = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "f03_probe@example.com",
                "password": "WrongPassword1234!",
            },
        )

        assert res_nonexistent.status_code == res_wrong_pw.status_code == 401
        assert res_nonexistent.json() == res_wrong_pw.json() == {"detail": "Invalid email or password"}
        assert res_nonexistent.headers.get("content-type") == res_wrong_pw.headers.get("content-type")


# ── 3. Preserved Authentication Functionality ─────────────────────────────────

@pytest.mark.asyncio
async def test_login_valid_credentials_succeeds_and_records_audit_log(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Valid credentials still log in successfully, issue session tokens, and record audit log."""
    user = User(
        name="F03 Valid User",
        email="f03_valid@example.com",
        password_hash=hash_password("SuperSecret1234!"),
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        res = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "f03_valid@example.com",
                "password": "SuperSecret1234!",
            },
        )

        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in res.cookies

        # Verify AuditLog recorded
        audit = await test_db.scalar(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.action == "login",
            )
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_login_inactive_user_rejected_with_generic_response(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Inactive users are rejected with the same generic 401 response and no audit log."""
    user = User(
        name="F03 Inactive User",
        email="f03_inactive@example.com",
        password_hash=hash_password("SuperSecret1234!"),
        role="advertiser",
        is_active=False,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        res = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "f03_inactive@example.com",
                "password": "SuperSecret1234!",
            },
        )

        assert res.status_code == 401
        assert res.json() == {"detail": "Invalid email or password"}

        # AuditLog must not be recorded for failed login
        audit = await test_db.scalar(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.action == "login",
            )
        )
        assert audit is None


@pytest.mark.asyncio
async def test_login_corrupted_stored_hash_rejected_safely(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """User with corrupted stored hash fails authentication safely without crashing."""
    user = User(
        name="F03 Corrupted Hash User",
        email="f03_corrupted@example.com",
        password_hash="corrupted_hash_format",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()

    with patch("liteads.ad_server.routers.auth.check_login_rate_limit"):
        res = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "f03_corrupted@example.com",
                "password": "AnyPassword123!",
            },
        )

        assert res.status_code == 401
        assert res.json() == {"detail": "Invalid email or password"}


class MockRedisPipeline:
    """In-memory mock for Redis pipeline in rate-limit tests."""

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


@pytest.mark.asyncio
async def test_login_rate_limiting_and_f02_client_ip_preserved(
    client: AsyncClient,
):
    """Login rate limiting and F-02 trusted proxy client-IP resolution continue to function."""
    redis_state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(redis_state)), \
         patch.object(get_settings().server, "trusted_proxies", ["127.0.0.1"]):

        # Send 5 requests from 203.0.113.77 via trusted proxy
        for _ in range(5):
            res = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "rate_limit_probe@example.com",
                    "password": "WrongPassword1!",
                },
                headers={"X-Forwarded-For": "203.0.113.77"},
            )
            assert res.status_code == 401

        # 6th attempt is blocked by rate limit
        res_6 = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "rate_limit_probe@example.com",
                "password": "WrongPassword1!",
            },
            headers={"X-Forwarded-For": "203.0.113.77"},
        )
        assert res_6.status_code == 429
        assert "Too many login attempts" in res_6.json()["detail"]
        assert "rate_limit:login:ip:203.0.113.77" in redis_state
