"""Comprehensive regression tests for Phase 4A SSRF-safe webhook destinations."""

import asyncio
from contextlib import asynccontextmanager
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpcore
import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.webhook_dispatcher import (
    _deliver,
    _deliver_with_retries,
    test_webhook as dispatcher_test_webhook,
)
from liteads.common.ssrf import (
    ALLOWED_PORTS,
    SSRFConnectionError,
    SSRFError,
    SSRFSafeBackend,
    SSRFSafeTransport,
    SSRFValidationError,
    is_ip_allowed,
    validate_destination_url,
)
from liteads.models.webhook import Webhook


def _make_mock_client(status_code=200, headers=None, body_chunks=None):
    """Helper to mock httpx.AsyncClient with async stream support."""
    @asynccontextmanager
    async def mock_stream(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = headers or {}

        async def aiter_bytes():
            for chunk in (body_chunks or []):
                yield chunk

        resp.aiter_bytes = aiter_bytes
        yield resp

    client_mock = MagicMock()

    async def aenter(*args):
        return client_mock

    async def aexit(*args):
        pass

    client_mock.__aenter__ = aenter
    client_mock.__aexit__ = aexit
    client_mock.stream = mock_stream
    return client_mock


# ── 1. Unit Tests: validate_destination_url & is_ip_allowed ──────────────────

def test_valid_public_https_destinations():
    """Valid public HTTPS URLs should pass validation."""
    valid_urls = [
        "https://example.com/webhook",
        "https://example.com:443/webhook",
        "https://example.com:8443/events",
        "https://api.partner.org/ads/callback",
        "https://sub.domain.example.co.uk/hook",
        "https://93.184.216.34/webhook",
        "https://93.184.216.34:8443/webhook",
        "https://[2606:4700:4700::1111]/webhook",
    ]
    for url in valid_urls:
        parsed = validate_destination_url(url, resolve_dns=False)
        assert parsed.scheme == "https"


@pytest.mark.parametrize(
    "invalid_scheme_url",
    [
        "http://example.com/webhook",
        "ftp://example.com/webhook",
        "gopher://example.com/webhook",
        "file:///etc/passwd",
        "javascript://alert(1)",
        "ws://example.com/webhook",
        "wss://example.com/webhook",
        "data:text/plain;base64,SGVsbG8=",
        "//example.com/webhook",
    ],
)
def test_reject_invalid_schemes(invalid_scheme_url):
    """Only HTTPS is allowed; all other schemes must be rejected."""
    with pytest.raises(SSRFValidationError, match="HTTPS"):
        validate_destination_url(invalid_scheme_url, resolve_dns=False)


@pytest.mark.parametrize(
    "credential_url",
    [
        "https://user:pass@example.com/webhook",
        "https://user@example.com/webhook",
        "https://:pass@example.com/webhook",
        "https://admin:secret@93.184.216.34/webhook",
        "https://user%40example.com@evil.com/webhook",
    ],
)
def test_reject_credentials_in_url(credential_url):
    """URLs containing userinfo / credentials must be rejected."""
    with pytest.raises(SSRFValidationError, match="user credentials"):
        validate_destination_url(credential_url, resolve_dns=False)


@pytest.mark.parametrize(
    "unsupported_port_url",
    [
        "https://example.com:80/webhook",
        "https://example.com:8080/webhook",
        "https://example.com:22/webhook",
        "https://example.com:25/webhook",
        "https://example.com:6379/webhook",  # Redis
        "https://example.com:5432/webhook",  # PostgreSQL
        "https://example.com:2375/webhook",  # Docker
        "https://example.com:8000/webhook",
    ],
)
def test_reject_unsupported_ports(unsupported_port_url):
    """Only ports 443 and 8443 are allowed."""
    with pytest.raises(SSRFValidationError, match="Unsupported port"):
        validate_destination_url(unsupported_port_url, resolve_dns=False)


def test_reject_malformed_ports():
    """Malformed non-numeric ports must be rejected."""
    with pytest.raises(SSRFValidationError, match="Malformed port"):
        validate_destination_url("https://example.com:abc/webhook", resolve_dns=False)


@pytest.mark.parametrize(
    "prohibited_ip_url",
    [
        # IPv4 Loopback
        "https://127.0.0.1/webhook",
        "https://127.0.0.2/webhook",
        "https://127.255.255.254/webhook",
        # RFC 1918 Private
        "https://10.0.0.1/webhook",
        "https://10.254.0.1/webhook",
        "https://172.16.0.1/webhook",
        "https://172.31.255.254/webhook",
        "https://192.168.0.1/webhook",
        "https://192.168.1.1/webhook",
        # Link-local / Cloud metadata
        "https://169.254.169.254/webhook",
        "https://169.254.1.1/webhook",
        "https://100.100.100.200/webhook",  # Alibaba IMDS
        # Multicast / Broadcast / Unspecified
        "https://0.0.0.0/webhook",
        "https://224.0.0.1/webhook",
        "https://255.255.255.255/webhook",
        "https://240.0.0.1/webhook",
    ],
)
def test_reject_prohibited_ipv4_literals(prohibited_ip_url):
    """Prohibited IPv4 literals must be rejected."""
    with pytest.raises(SSRFValidationError, match="private or prohibited IP address"):
        validate_destination_url(prohibited_ip_url, resolve_dns=False)


@pytest.mark.parametrize(
    "prohibited_ipv6_url",
    [
        # IPv6 Loopback
        "https://[::1]/webhook",
        # IPv6 Unspecified
        "https://[::]/webhook",
        # IPv6 Unique Local (RFC 4193 private)
        "https://[fc00::1]/webhook",
        "https://[fd00::1]/webhook",
        "https://[fd00:ec2::254]/webhook",  # AWS IPv6 IMDS
        # IPv6 Link-local
        "https://[fe80::1]/webhook",
        "https://[fe80::169:254:169:254]/webhook",
        # IPv6 Multicast
        "https://[ff02::1]/webhook",
        # IPv4-mapped IPv6 pointing to private/loopback/metadata
        "https://[::ffff:127.0.0.1]/webhook",
        "https://[::ffff:10.0.0.1]/webhook",
        "https://[::ffff:169.254.169.254]/webhook",
        "https://[::ffff:192.168.1.1]/webhook",
        # Deprecated IPv4-compatible IPv6
        "https://[::127.0.0.1]/webhook",
        # 6to4 embedding private IPv4 (2002::/16)
        "https://[2002:7f00:1::]/webhook",  # 127.0.0.1 embedded
        "https://[2002:a00:1::]/webhook",   # 10.0.0.1 embedded
    ],
)
def test_reject_prohibited_ipv6_literals(prohibited_ipv6_url):
    """Prohibited IPv6 literals must be rejected."""
    with pytest.raises(SSRFValidationError, match="prohibited"):
        validate_destination_url(prohibited_ipv6_url, resolve_dns=False)


@pytest.mark.parametrize(
    "alternate_ip_url",
    [
        # Octal representations
        "https://0177.0.0.1/webhook",
        "https://017700000001/webhook",
        # Hex representations
        "https://0x7f000001/webhook",
        "https://0x7f.0.0.1/webhook",
        "https://127.0.0.0x1/webhook",
        "https://127.0.0x0.1/webhook",
        # Decimal / Dword integers
        "https://2130706433/webhook",
        "https://2852039166/webhook",  # 169.254.169.254
        # Shorthand dotted
        "https://127.1/webhook",
        "https://10.1/webhook",
    ],
)
def test_reject_encoded_and_alternate_ip_forms(alternate_ip_url):
    """Non-canonical or ambiguous numeric IP forms must be rejected."""
    with pytest.raises(SSRFValidationError, match="invalid or ambiguous numeric IP"):
        validate_destination_url(alternate_ip_url, resolve_dns=False)


@pytest.mark.parametrize(
    "internal_domain_url",
    [
        "https://localhost/webhook",
        "https://service.localhost/webhook",
        "https://metadata/computeMetadata/v1",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://instance-data/latest/meta-data",
        "https://cluster.local/webhook",
        "https://service.internal/webhook",
        "https://server.corp/webhook",
        "https://router.lan/webhook",
        "https://intranet.home/webhook",
        "https://singlelabel/webhook",
    ],
)
def test_reject_internal_domains_and_suffixes(internal_domain_url):
    """Internal hostnames, cloud metadata names, and single-label hosts must be rejected."""
    with pytest.raises(SSRFValidationError):
        validate_destination_url(internal_domain_url, resolve_dns=False)


def test_reject_dns_resolving_to_prohibited_ip():
    """Hostnames resolving to private or loopback addresses must be rejected."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with pytest.raises(SSRFValidationError, match="resolves to a private or prohibited IP"):
            validate_destination_url("https://spoofed.example.com/webhook", resolve_dns=True)


def test_reject_dns_resolving_to_metadata_ip():
    """Hostnames resolving to cloud metadata must be rejected."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))
        ]
        with pytest.raises(SSRFValidationError, match="resolves to a private or prohibited IP"):
            validate_destination_url("https://metadata-proxy.example.com/webhook", resolve_dns=True)


def test_reject_dns_mixed_public_and_private():
    """Split DNS returning ANY prohibited address must be rejected immediately."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]
        with pytest.raises(SSRFValidationError, match="resolves to a private or prohibited IP"):
            validate_destination_url("https://dual-homed.example.com/webhook", resolve_dns=True)


def test_reject_unresolvable_dns():
    """Hostnames that cannot be resolved via DNS must be rejected."""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(SSRFValidationError, match="Could not resolve"):
            validate_destination_url("https://nonexistent-domain-xyz.com/webhook", resolve_dns=True)


# ── 2. API Route Enforcement: Create and Update ──────────────────────────────

@pytest.mark.asyncio
async def test_api_create_webhook_ssrf_rejection(client: AsyncClient, test_db: AsyncSession):
    """POST /webhooks must reject all SSRF vectors with HTTP 422."""
    res = await client.post(
        "/api/v1/auth/register",
        json={"name": "SSRF Adv", "email": "ssrf_adv@test.com", "password": "Password123!", "role": "advertiser"},
    )
    assert res.status_code == 201
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    malicious_urls = [
        "http://example.com/webhook",
        "https://127.0.0.1/webhook",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/webhook",
        "https://[::1]/webhook",
        "https://example.com:8080/webhook",
        "https://0x7f000001/webhook",
        "https://admin:pass@example.com/webhook",
    ]
    for m_url in malicious_urls:
        resp = await client.post(
            "/api/v1/webhooks",
            json={"url": m_url, "events": ["impression"]},
            headers=headers,
        )
        assert resp.status_code == 422, f"Expected 422 for {m_url}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_api_update_webhook_ssrf_rejection(client: AsyncClient, test_db: AsyncSession):
    """PATCH /webhooks/{id} must reject SSRF destinations with HTTP 422."""
    res = await client.post(
        "/api/v1/auth/register",
        json={"name": "SSRF Patch Adv", "email": "ssrf_patch@test.com", "password": "Password123!", "role": "advertiser"},
    )
    assert res.status_code == 201
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create valid webhook
    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/webhook", "events": ["click"]},
        headers=headers,
    )
    assert create_res.status_code == 201
    wh_id = create_res.json()["id"]

    # Try updating to prohibited destinations
    for bad_url in ["http://example.com/webhook", "https://10.0.0.1/webhook", "https://[::1]/webhook"]:
        patch_res = await client.patch(
            f"/api/v1/webhooks/{wh_id}",
            json={"url": bad_url},
            headers=headers,
        )
        assert patch_res.status_code == 422, f"Expected 422 for {bad_url}, got {patch_res.status_code}"


@pytest.mark.asyncio
async def test_api_test_webhook_destination_rejected(client: AsyncClient, test_db: AsyncSession):
    """POST /webhooks/{id}/test must block internal targets and report failure safely."""
    res = await client.post(
        "/api/v1/auth/register",
        json={"name": "SSRF Test Ping Adv", "email": "ssrf_ping@test.com", "password": "Password123!", "role": "advertiser"},
    )
    assert res.status_code == 201
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/webhook", "events": ["conversion"]},
        headers=headers,
    )
    assert create_res.status_code == 201
    wh_id = create_res.json()["id"]

    # Directly mutate DB URL to a loopback address to simulate legacy or altered data
    wh = await test_db.get(Webhook, wh_id)
    wh.url = "https://127.0.0.1/test"
    await test_db.commit()

    test_res = await client.post(f"/api/v1/webhooks/{wh_id}/test", headers=headers)
    assert test_res.status_code == 200
    data = test_res.json()
    assert data["delivered"] is False
    assert "Destination rejected" in data["error"]


# ── 3. Outbound Request & Redirect Re-validation ──────────────────────────────

@pytest.mark.asyncio
async def test_deliver_blocks_private_destinations():
    """_deliver must block private IP addresses before sending requests."""
    ok, status_code, err = await _deliver("https://127.0.0.1/webhook", b"{}", "sig")
    assert ok is False
    assert status_code is None
    assert "Destination rejected" in err


@pytest.mark.asyncio
async def test_deliver_blocks_redirect_to_private_host():
    """_deliver must re-validate redirects and reject redirects to private hosts."""
    mock_client = _make_mock_client(status_code=302, headers={"Location": "https://127.0.0.1/admin"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok, status_code, err = await _deliver("https://example.com/webhook", b"{}", "sig")
        assert ok is False
        assert status_code == 302
        assert "Redirect destination rejected" in err


@pytest.mark.asyncio
async def test_deliver_blocks_redirect_to_metadata():
    """_deliver must reject redirects targeting cloud metadata."""
    mock_client = _make_mock_client(status_code=302, headers={"Location": "https://169.254.169.254/latest/meta-data"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok, status_code, err = await _deliver("https://example.com/webhook", b"{}", "sig")
        assert ok is False
        assert status_code == 302
        assert "Redirect destination rejected" in err


@pytest.mark.asyncio
async def test_deliver_blocks_redirect_downgrade_to_http():
    """_deliver must reject redirects that downgrade from HTTPS to HTTP."""
    mock_client = _make_mock_client(status_code=301, headers={"Location": "http://example.com/insecure"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok, status_code, err = await _deliver("https://example.com/webhook", b"{}", "sig")
        assert ok is False
        assert status_code == 301
        assert "HTTPS" in err


@pytest.mark.asyncio
async def test_deliver_blocks_too_many_redirects():
    """_deliver must limit redirects to prevent redirect loops."""
    mock_client = _make_mock_client(status_code=302, headers={"Location": "https://example.com/next"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok, status_code, err = await _deliver("https://example.com/webhook", b"{}", "sig")
        assert ok is False
        assert "Too many redirects" in err


@pytest.mark.asyncio
async def test_deliver_bounded_response_handling():
    """_deliver must terminate responses exceeding max allowed payload size."""
    body_chunks = [b"X" * 1024 for _ in range(70)]  # 70 KB > 64 KB limit
    mock_client = _make_mock_client(status_code=200, body_chunks=body_chunks)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok, status_code, err = await _deliver("https://example.com/webhook", b"{}", "sig")
        assert ok is False
        assert status_code == 200
        assert "exceeded maximum allowed size" in err


@pytest.mark.asyncio
async def test_deliver_with_retries_aborts_immediately_on_ssrf():
    """_deliver_with_retries must not waste retries when destination is rejected by SSRF."""
    with patch("liteads.ad_server.services.webhook_dispatcher._deliver") as mock_deliver:
        mock_deliver.return_value = (False, None, "Destination rejected: prohibited address")
        ok, status, err = await _deliver_with_retries("https://127.0.0.1/webhook", b"{}", "sig")
        assert ok is False
        # Must only call _deliver once, not retried 3 times
        assert mock_deliver.call_count == 1


# ── 4. DNS Rebinding & TOCTOU Defense via SSRFSafeBackend ─────────────────────

@pytest.mark.asyncio
async def test_ssrf_safe_backend_blocks_dns_rebinding():
    """SSRFSafeBackend.connect_tcp must intercept rebinding to private IPs at connect time."""
    backend = SSRFSafeBackend()

    # Simulate loop.getaddrinfo returning a private IP during connect_tcp
    mock_loop = AsyncMock()
    mock_loop.getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ]

    with patch("asyncio.get_running_loop", return_value=mock_loop):
        with pytest.raises(SSRFConnectionError, match="prohibited address"):
            await backend.connect_tcp("rebound-host.com", 443)


@pytest.mark.asyncio
async def test_ssrf_safe_backend_blocks_mixed_addresses_at_connect():
    """SSRFSafeBackend.connect_tcp must abort if ANY address is prohibited."""
    backend = SSRFSafeBackend()

    mock_loop = AsyncMock()
    mock_loop.getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
    ]

    with patch("asyncio.get_running_loop", return_value=mock_loop):
        with pytest.raises(SSRFConnectionError, match="prohibited address"):
            await backend.connect_tcp("mixed-host.com", 443)
