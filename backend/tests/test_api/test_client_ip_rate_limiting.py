"""
Unit and integration tests for F-02: Client-IP identification and rate limiting behind reverse proxies.

Covers:
- Direct requests from untrusted clients with spoofed forwarding headers (X-Forwarded-For, X-Real-IP).
- Requests from configured trusted proxies with valid forwarded client IPs.
- Multiple forwarded addresses and right-to-left trust-chain walking.
- Missing, empty, or malformed forwarding headers.
- Rate-limit key isolation for different client IPs behind the same trusted proxy.
- Protection against IP-spoofing rate limit bypass attempts by untrusted direct clients.
- Preservation of per-account rate limits and Redis fail-closed behavior (HTTP 503).
- Application configuration and startup with trusted proxy settings.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient
import pytest
from starlette.datastructures import Address, Headers

from liteads.ad_server.main import create_app
from liteads.common.config import ServerSettings, get_settings
from liteads.common.security import (
    check_login_rate_limit,
    check_password_reset_completion_rate_limit,
    check_password_reset_request_rate_limit,
    check_resend_verification_rate_limit,
    is_trusted_proxy,
    resolve_client_ip,
)


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


def _create_mock_request(
    client_host: str | None,
    headers: dict[str, str] | list[tuple[bytes, bytes]] | None = None,
) -> Request:
    """Helper to build a Starlette/FastAPI Request with specific client host and headers."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": [],
    }
    if client_host is not None:
        scope["client"] = (client_host, 12345)
    else:
        scope["client"] = None

    if headers:
        if isinstance(headers, dict):
            raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
        else:
            raw_headers = headers
        scope["headers"] = raw_headers

    return Request(scope)


# ── 1. Unit Tests for is_trusted_proxy ────────────────────────────────────────

def test_is_trusted_proxy_matching():
    trusted = ["127.0.0.1", "::1", "10.0.0.0/8", "172.16.0.0/12"]

    # Exact IP matches
    assert is_trusted_proxy("127.0.0.1", trusted) is True
    assert is_trusted_proxy("::1", trusted) is True

    # CIDR subnet matches
    assert is_trusted_proxy("10.0.1.25", trusted) is True
    assert is_trusted_proxy("10.254.0.1", trusted) is True
    assert is_trusted_proxy("172.16.5.10", trusted) is True
    assert is_trusted_proxy("172.31.255.254", trusted) is True

    # Untrusted IPs
    assert is_trusted_proxy("203.0.113.1", trusted) is False
    assert is_trusted_proxy("192.168.1.1", trusted) is False
    assert is_trusted_proxy("172.32.0.1", trusted) is False

    # Edge cases
    assert is_trusted_proxy(None, trusted) is False
    assert is_trusted_proxy("", trusted) is False
    assert is_trusted_proxy("invalid_ip", trusted) is False
    assert is_trusted_proxy("127.0.0.1", []) is False


# ── 2. Unit Tests for resolve_client_ip ───────────────────────────────────────

def test_resolve_client_ip_direct_untrusted_client_ignores_spoofed_headers():
    """Direct untrusted client cannot spoof client IP using X-Forwarded-For or X-Real-IP."""
    trusted = ["127.0.0.1", "::1"]

    req = _create_mock_request(
        client_host="203.0.113.50",
        headers={
            "X-Forwarded-For": "1.1.1.1, 8.8.8.8",
            "X-Real-IP": "9.9.9.9",
        },
    )

    resolved = resolve_client_ip(req, trusted_proxies=trusted)
    assert resolved == "203.0.113.50"


def test_resolve_client_ip_trusted_proxy_single_forwarded_ip():
    """When peer is a trusted proxy, X-Forwarded-For is correctly parsed."""
    trusted = ["127.0.0.1", "::1"]

    req = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.195"},
    )
    assert resolve_client_ip(req, trusted_proxies=trusted) == "203.0.113.195"

    # IPv6 proxy and IPv6 client
    req_v6 = _create_mock_request(
        client_host="::1",
        headers={"X-Forwarded-For": "2001:db8::cafe"},
    )
    assert resolve_client_ip(req_v6, trusted_proxies=trusted) == "2001:db8::cafe"


def test_resolve_client_ip_trusted_proxy_ignores_attacker_prepended_spoof():
    """Attacker pre-pending spoofed IPs in X-Forwarded-For is ignored by right-to-left walk."""
    trusted = ["127.0.0.1"]

    # Attacker 203.0.113.50 sent X-Forwarded-For: 8.8.8.8 to Nginx.
    # Nginx appended the real peer: "8.8.8.8, 203.0.113.50"
    req = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "8.8.8.8, 203.0.113.50"},
    )
    assert resolve_client_ip(req, trusted_proxies=trusted) == "203.0.113.50"


def test_resolve_client_ip_multi_hop_proxy_chain():
    """Multi-hop proxy chain correctly traverses intermediate trusted proxies."""
    trusted = ["127.0.0.1", "10.0.0.0/8"]

    # Chain: Spoofed (1.2.3.4) -> Real Client (198.51.100.77) -> Internal Proxy (10.0.0.5) -> Nginx (127.0.0.1)
    req = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.77, 10.0.0.5"},
    )
    assert resolve_client_ip(req, trusted_proxies=trusted) == "198.51.100.77"


def test_resolve_client_ip_all_proxies_trusted():
    """If all forwarded IPs are trusted proxies, returns leftmost valid originator."""
    trusted = ["127.0.0.1", "10.0.0.0/8"]

    req = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"},
    )
    assert resolve_client_ip(req, trusted_proxies=trusted) == "10.0.0.1"


def test_resolve_client_ip_missing_or_malformed_headers():
    """Graceful fallbacks for missing, empty, or malformed headers."""
    trusted = ["127.0.0.1"]

    # Missing X-Forwarded-For with valid X-Real-IP
    req_real = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Real-IP": "203.0.113.195"},
    )
    assert resolve_client_ip(req_real, trusted_proxies=trusted) == "203.0.113.195"

    # Missing both headers falls back to peer host
    req_empty = _create_mock_request(client_host="127.0.0.1")
    assert resolve_client_ip(req_empty, trusted_proxies=trusted) == "127.0.0.1"

    # Malformed X-Forwarded-For falls back to peer host
    req_malformed = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "malicious_string, 999.999.999.999"},
    )
    assert resolve_client_ip(req_malformed, trusted_proxies=trusted) == "127.0.0.1"

    # Malformed X-Forwarded-For with valid X-Real-IP falls back to X-Real-IP
    req_malformed_with_real = _create_mock_request(
        client_host="127.0.0.1",
        headers={
            "X-Forwarded-For": "bad_ip",
            "X-Real-IP": "203.0.113.200",
        },
    )
    assert resolve_client_ip(req_malformed_with_real, trusted_proxies=trusted) == "203.0.113.200"

    # Missing client connection
    req_no_client = _create_mock_request(client_host=None)
    assert resolve_client_ip(req_no_client, trusted_proxies=trusted) == "unknown"

    # IP with port string
    req_port = _create_mock_request(
        client_host="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.10:8080"},
    )
    assert resolve_client_ip(req_port, trusted_proxies=trusted) == "203.0.113.10"


# ── 3. Rate-Limiting Key Isolation & Enforcement Tests ────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_keys_isolated_per_client_ip_behind_proxy():
    """Different client IPs behind the same trusted proxy do not interfere with each other's limits."""
    redis_state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(redis_state)), \
         patch.object(get_settings().server, "trusted_proxies", ["127.0.0.1"]):

        # Client A makes 5 allowed login attempts
        req_a = _create_mock_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        for i in range(5):
            await check_login_rate_limit(req_a, f"user{i}@example.com")

        # 6th attempt from Client A is blocked by IP rate limit
        with pytest.raises(HTTPException) as exc_info:
            await check_login_rate_limit(req_a, "another_user@example.com")
        assert exc_info.value.status_code == 429
        assert "Too many login attempts" in exc_info.value.detail

        # Client B behind the SAME proxy is NOT blocked
        req_b = _create_mock_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.20"},
        )
        # Should not raise
        await check_login_rate_limit(req_b, "client_b_user@example.com")

        # Verify Redis keys
        assert "rate_limit:login:ip:203.0.113.10" in redis_state
        assert redis_state["rate_limit:login:ip:203.0.113.10"] == 6
        assert "rate_limit:login:ip:203.0.113.20" in redis_state
        assert redis_state["rate_limit:login:ip:203.0.113.20"] == 1


@pytest.mark.asyncio
async def test_untrusted_direct_client_cannot_bypass_rate_limit_with_spoofed_ip():
    """An untrusted direct client changing X-Forwarded-For is still tracked by its real peer IP."""
    redis_state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(redis_state)), \
         patch.object(get_settings().server, "trusted_proxies", ["127.0.0.1"]):

        # Attacker connects from 198.51.100.99 with spoofed headers
        for i in range(5):
            req = _create_mock_request(
                client_host="198.51.100.99",
                headers={"X-Forwarded-For": f"1.1.1.{i}"},
            )
            await check_login_rate_limit(req, f"attacker{i}@example.com")

        # 6th attempt with yet another spoofed IP header
        req_bypass = _create_mock_request(
            client_host="198.51.100.99",
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        with pytest.raises(HTTPException) as exc_info:
            await check_login_rate_limit(req_bypass, "attacker_final@example.com")

        assert exc_info.value.status_code == 429
        assert "rate_limit:login:ip:198.51.100.99" in redis_state
        assert redis_state["rate_limit:login:ip:198.51.100.99"] == 6


@pytest.mark.asyncio
async def test_account_rate_limit_persists_across_multiple_ips():
    """Per-account limits trigger after max attempts regardless of originating IP."""
    redis_state: dict[str, int] = {}

    with patch("liteads.common.security.redis_client.pipeline", side_effect=lambda: MockRedisPipeline(redis_state)), \
         patch.object(get_settings().server, "trusted_proxies", ["127.0.0.1"]):

        target_account = "victim@example.com"

        # 5 attempts from 5 distinct client IPs behind trusted proxy
        for i in range(5):
            req = _create_mock_request(
                client_host="127.0.0.1",
                headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
            )
            await check_login_rate_limit(req, target_account)

        # 6th attempt from a brand new IP for the same account is blocked
        req_6 = _create_mock_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        with pytest.raises(HTTPException) as exc_info:
            await check_login_rate_limit(req_6, target_account)

        assert exc_info.value.status_code == 429
        assert redis_state[f"rate_limit:login:account:{target_account}"] == 6


@pytest.mark.asyncio
async def test_redis_fail_closed_behavior_across_all_rate_limiters():
    """All authentication rate limiters fail closed (HTTP 503) during Redis outages."""
    req = _create_mock_request(client_host="127.0.0.1")

    with patch("liteads.common.security.redis_client.pipeline", side_effect=Exception("Redis connection refused")):
        # 1. Login rate limit
        with pytest.raises(HTTPException) as exc1:
            await check_login_rate_limit(req, "user@example.com")
        assert exc1.value.status_code == 503
        assert exc1.value.detail == "Service unavailable"

        # 2. Password reset request rate limit
        with pytest.raises(HTTPException) as exc2:
            await check_password_reset_request_rate_limit(req, "user@example.com")
        assert exc2.value.status_code == 503
        assert exc2.value.detail == "Service unavailable"

        # 3. Password reset completion rate limit
        with pytest.raises(HTTPException) as exc3:
            await check_password_reset_completion_rate_limit(req)
        assert exc3.value.status_code == 503
        assert exc3.value.detail == "Service unavailable"

        # 4. Resend verification rate limit
        with pytest.raises(HTTPException) as exc4:
            await check_resend_verification_rate_limit(req, "user@example.com")
        assert exc4.value.status_code == 503
        assert exc4.value.detail == "Service unavailable"


# ── 4. Server Settings & Startup Tests ────────────────────────────────────────

def test_server_settings_trusted_proxies_parsing():
    """ServerSettings correctly normalizes various trusted proxy formats."""
    # Default secure value
    s_default = ServerSettings()
    assert s_default.trusted_proxies == ["127.0.0.1", "::1"]
    assert s_default.proxy_headers is True

    # Comma-separated string
    s_csv = ServerSettings(trusted_proxies="127.0.0.1, 10.0.0.0/8, 172.16.0.0/12")
    assert s_csv.trusted_proxies == ["127.0.0.1", "10.0.0.0/8", "172.16.0.0/12"]

    # Explicit list
    s_list = ServerSettings(trusted_proxies=["192.168.1.1", "10.0.0.1"])
    assert s_list.trusted_proxies == ["192.168.1.1", "10.0.0.1"]

    # JSON formatted string
    s_json = ServerSettings(trusted_proxies='["127.0.0.1", "10.0.0.0/8"]')
    assert s_json.trusted_proxies == ["127.0.0.1", "10.0.0.0/8"]


def test_main_startup_uvicorn_parameters():
    """Verify main() passes proxy_headers and forwarded_allow_ips to uvicorn.run."""
    from liteads.ad_server.main import main

    with patch("uvicorn.run") as mock_run:
        main()

        assert mock_run.called
        kwargs = mock_run.call_args[1]
        assert "proxy_headers" in kwargs
        assert kwargs["proxy_headers"] is True
        assert "forwarded_allow_ips" in kwargs
        assert "127.0.0.1" in kwargs["forwarded_allow_ips"]


def test_docker_bridge_trusted_proxy_forwarded_client_ip():
    """Verify Docker bridge network (172.16.0.0/12) is trusted and untrusted peers cannot spoof."""
    docker_trusted = ["127.0.0.1", "::1", "172.16.0.0/12"]

    # 1. Nginx container on Docker bridge network (172.18.0.5) forwards real client IP
    req_nginx = _create_mock_request(
        client_host="172.18.0.5",
        headers={"X-Forwarded-For": "203.0.113.195"},
    )
    assert resolve_client_ip(req_nginx, trusted_proxies=docker_trusted) == "203.0.113.195"

    # 2. Client prepends spoofed IP; Nginx appends authentic client IP
    req_spoof_via_nginx = _create_mock_request(
        client_host="172.18.0.5",
        headers={"X-Forwarded-For": "198.51.100.99, 203.0.113.195"},
    )
    assert resolve_client_ip(req_spoof_via_nginx, trusted_proxies=docker_trusted) == "203.0.113.195"

    # 3. Direct connection from an untrusted peer (e.g. 10.0.0.5, since 10.0.0.0/8 is NOT in Docker config)
    req_untrusted_private = _create_mock_request(
        client_host="10.0.0.5",
        headers={"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "8.8.8.8"},
    )
    assert resolve_client_ip(req_untrusted_private, trusted_proxies=docker_trusted) == "10.0.0.5"

    # 4. Direct untrusted public client trying to claim it is Nginx
    req_direct_public = _create_mock_request(
        client_host="203.0.113.50",
        headers={"X-Forwarded-For": "172.18.0.5", "X-Real-IP": "172.18.0.5"},
    )
    assert resolve_client_ip(req_direct_public, trusted_proxies=docker_trusted) == "203.0.113.50"


def test_env_var_trusted_proxies_docker_compose_configuration():
    """Verify LITEADS_SERVER__TRUSTED_PROXIES env var format used in docker-compose.yml parses correctly."""
    import os
    from liteads.ad_server.main import main

    env_val = "127.0.0.1,::1,172.16.0.0/12"
    with patch.dict(os.environ, {"LITEADS_SERVER__TRUSTED_PROXIES": env_val}):
        s = ServerSettings()
        assert s.trusted_proxies == ["127.0.0.1", "::1", "172.16.0.0/12"]

        with patch("uvicorn.run") as mock_run:
            with patch("liteads.ad_server.main.get_settings") as mock_get_settings:
                mock_get_settings.return_value.server = s
                mock_get_settings.return_value.debug = False
                main()
                assert mock_run.called
                kwargs = mock_run.call_args[1]
                assert kwargs["forwarded_allow_ips"] == "127.0.0.1,::1,172.16.0.0/12"


@pytest.mark.asyncio
async def test_referrer_policy_header_present_on_fastapi_responses():
    """Verify Referrer-Policy: no-referrer is present on both success and error responses."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_ok = await client.get("/ping")
        assert resp_ok.status_code == 200
        assert resp_ok.headers.get("referrer-policy") == "no-referrer"

        resp_err = await client.get("/nonexistent-path-for-testing-404")
        assert resp_err.status_code == 404
        assert resp_err.headers.get("referrer-policy") == "no-referrer"
