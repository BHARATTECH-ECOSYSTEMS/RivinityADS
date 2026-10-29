"""Unit and integration tests for Phase 6.1B: Listmonk Client and Email Provider Adapter.

Tests cover:
- Typed Listmonk settings validation, fail-closed semantics, and credential redaction.
- ListmonkClient HTTP Basic Auth, external-recipient mode, bounded timeouts, and response parsing.
- Precise error classification: ConnectTimeout (pre-send retryable) vs AmbiguousTimeout (post-send non-retryable).
- EmailProvider interface and provider selection (SmtpEmailProvider vs ListmonkEmailProvider).
- Template ID resolution and missing template ID failure handling.
- Notification worker error classification and prevention of blind duplicate retries.
- Zero-regression compatibility with existing SMTP paths.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from liteads.ad_server.services.notification_worker import (
    NotificationDeliveryWorker,
    is_permanent_email_error,
)
from liteads.common.config import EmailSettings, ListmonkSettings, Settings
from liteads.common.email import (
    EmailDeliveryError,
    EmailProvider,
    ListmonkEmailProvider,
    SmtpEmailProvider,
    get_email_provider,
    send_email,
)
from liteads.common.listmonk_client import (
    ListmonkAmbiguousTimeoutError,
    ListmonkAuthError,
    ListmonkClient,
    ListmonkConfigError,
    ListmonkConnectTimeoutError,
    ListmonkError,
    ListmonkNotFoundError,
    ListmonkRateLimitError,
    ListmonkResponseError,
    ListmonkServerError,
    mask_email,
    sanitize_message,
)
from liteads.models.notification import NotificationDelivery


# ── 1. Settings & Configuration Tests ─────────────────────────────────────────

def test_listmonk_settings_disabled_by_default():
    settings = ListmonkSettings()
    assert settings.enabled is False
    assert settings.request_timeout == 10.0
    assert settings.connect_timeout == 3.0
    # Disabled provider does not raise on empty credentials
    settings.validate_if_enabled()


def test_listmonk_settings_fail_closed_when_enabled():
    # 1. Missing token
    with pytest.raises(ValueError, match="LISTMONK_TOKEN must be non-empty"):
        ListmonkSettings(enabled=True, url="http://localhost:9000", user="admin", token="").validate_if_enabled()

    # 2. Missing user
    with pytest.raises(ValueError, match="LISTMONK_USER must be non-empty"):
        ListmonkSettings(enabled=True, url="http://localhost:9000", user="", token="secret").validate_if_enabled()

    # 3. Invalid URL scheme
    with pytest.raises(ValueError, match="LISTMONK_URL must be a valid http/https URL"):
        ListmonkSettings(enabled=True, url="ftp://invalid", user="admin", token="secret").validate_if_enabled()


def test_listmonk_settings_repr_token_strictly_redacted():
    settings = ListmonkSettings(
        enabled=True,
        url="http://listmonk.internal:9000",
        user="admin_user",
        token="super_secret_production_token_12345",
    )
    repr_str = repr(settings)
    assert "[REDACTED]" in repr_str
    assert "super_secret_production_token_12345" not in repr_str


# ── 2. Listmonk Client Core Functionality ─────────────────────────────────────

@pytest.mark.asyncio
async def test_listmonk_client_send_transactional_success():
    """Verify standard transactional send using mocked transport."""
    captured_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"data": True})

    transport = httpx.MockTransport(handler)
    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="api_user",
        token="api_token_abc",
        transport=transport,
    )

    success = await client.send_transactional(
        recipient_email="advertiser@example.com",
        template_id=42,
        data={"name": "Alice", "code": "123456"},
    )

    assert success is True
    assert captured_request is not None
    assert captured_request.method == "POST"
    assert captured_request.url.path == "/api/tx"

    # Verify Basic Auth header
    auth_header = captured_request.headers.get("Authorization")
    assert auth_header is not None
    assert auth_header.startswith("Basic ")
    encoded_creds = auth_header.split(" ", 1)[1]
    decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
    assert decoded_creds == "api_user:api_token_abc"

    # Verify external recipient mode payload
    import json
    body = json.loads(captured_request.content)
    assert body["subscriber_email"] == "advertiser@example.com"
    assert body["subscriber_mode"] == "external"  # Crucial: never enroll into marketing
    assert body["template_id"] == 42
    assert body["data"]["name"] == "Alice"
    assert body["data"]["code"] == "123456"


@pytest.mark.asyncio
async def test_listmonk_client_external_recipient_mode_enforced():
    """Verify that default subscriber_mode is 'external' to protect privacy."""
    captured_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        import json
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json={"data": True})

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    await client.send_transactional(
        recipient_email="user@test.org",
        template_id=1,
    )

    assert captured_payload is not None
    assert captured_payload["subscriber_mode"] == "external"


@pytest.mark.asyncio
async def test_listmonk_client_auth_error_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="bad_user",
        token="bad_token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkAuthError) as exc_info:
        await client.send_transactional(recipient_email="user@test.org", template_id=1)

    assert "HTTP 401" in str(exc_info.value)
    assert "bad_token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_listmonk_client_template_not_found_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "template not found"})

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkNotFoundError) as exc_info:
        await client.send_transactional(recipient_email="user@test.org", template_id=999)

    assert "HTTP 404" in str(exc_info.value)


@pytest.mark.asyncio
async def test_listmonk_client_rate_limit_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "too many requests"})

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkRateLimitError):
        await client.send_transactional(recipient_email="user@test.org", template_id=1)


@pytest.mark.asyncio
async def test_listmonk_client_server_error_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal server error"})

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkServerError) as exc_info:
        await client.send_transactional(recipient_email="user@test.org", template_id=1)

    assert "HTTP 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_listmonk_client_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Not a JSON response")

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkResponseError):
        await client.send_transactional(recipient_email="user@test.org", template_id=1)


@pytest.mark.asyncio
async def test_listmonk_client_health():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"data": "ok"})
        return httpx.Response(404)

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    is_healthy = await client.health()
    assert is_healthy is True


# ── 3. Timeout Classification: Pre-send vs Ambiguous ─────────────────────────

@pytest.mark.asyncio
async def test_listmonk_client_connect_timeout_is_classified_retryable():
    """ConnectTimeout occurs before dispatch: remote server never received the request."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("TCP handshake timed out", request=request)

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkConnectTimeoutError):
        await client.send_transactional(recipient_email="user@test.org", template_id=1)


@pytest.mark.asyncio
async def test_listmonk_client_read_timeout_is_classified_ambiguous():
    """ReadTimeout occurs after dispatch: delivery outcome is uncertain."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Server took too long to reply", request=request)

    client = ListmonkClient(
        base_url="http://listmonk.test:9000",
        username="user",
        token="token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ListmonkAmbiguousTimeoutError):
        await client.send_transactional(recipient_email="user@test.org", template_id=1)


# ── 4. Logging & Redaction Safety ─────────────────────────────────────────────

def test_mask_email():
    assert mask_email("user@example.com") == "u***@example.com"
    assert mask_email("a@b.com") == "*@b.com"
    assert mask_email("invalid") == "[REDACTED_EMAIL]"


def test_sanitize_message():
    raw = "Failed with token='secret123' and password=\"pwd456\" on https://api.io?token=abc&v=1"
    sanitized = sanitize_message(raw)
    assert "secret123" not in sanitized
    assert "pwd456" not in sanitized
    assert "abc" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "[PARAMS_REDACTED]" in sanitized


# ── 5. Provider Factory and Template Resolution ──────────────────────────────

def test_get_email_provider_default_smtp():
    settings = Settings(
        listmonk=ListmonkSettings(enabled=False),
        email=EmailSettings(host="smtp.test", mock_delivery=True),
    )
    provider = get_email_provider(settings)
    assert isinstance(provider, SmtpEmailProvider)


def test_get_email_provider_listmonk_when_enabled():
    settings = Settings(
        listmonk=ListmonkSettings(
            enabled=True,
            url="http://localhost:9000",
            user="admin",
            token="secret_token",
        ),
    )
    provider = get_email_provider(settings)
    assert isinstance(provider, ListmonkEmailProvider)


def test_listmonk_provider_template_resolution():
    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="secret",
        template_verification_id=10,
        template_password_reset_id=20,
        template_security_notice_id=30,
        template_digest_id=40,
        template_generic_id=99,
    )
    provider = ListmonkEmailProvider(settings=settings)

    assert provider._resolve_template_id("verification", settings) == 10
    assert provider._resolve_template_id("password_reset", settings) == 20
    assert provider._resolve_template_id("security_notice", settings) == 30
    assert provider._resolve_template_id("digest", settings) == 40
    # Fallback to generic when unknown or None
    assert provider._resolve_template_id(None, settings) == 99
    assert provider._resolve_template_id("unknown_flow", settings) == 99


def test_listmonk_provider_missing_template_raises_config_error():
    # No generic template configured
    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="secret",
        template_verification_id=None,
        template_generic_id=None,
    )
    provider = ListmonkEmailProvider(settings=settings)

    with pytest.raises(ListmonkConfigError, match="No Listmonk template ID configured"):
        provider._resolve_template_id("verification", settings)


@pytest.mark.asyncio
async def test_listmonk_provider_send_email_delegation():
    mock_client = AsyncMock()
    mock_client.send_transactional.return_value = True

    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="secret",
        template_verification_id=15,
    )
    provider = ListmonkEmailProvider(settings=settings, client=mock_client)

    await provider.send_email(
        to_address="recipient@example.com",
        subject="Verify Account",
        text_content="Verify link: abc",
        html_content="<p>Verify link: abc</p>",
        template_purpose="verification",
        template_data={"token": "abc_tok"},
    )

    mock_client.send_transactional.assert_awaited_once_with(
        recipient_email="recipient@example.com",
        template_id=15,
        data={
            "subject": "Verify Account",
            "body_text": "Verify link: abc",
            "body_html": "<p>Verify link: abc</p>",
            "token": "abc_tok",
        },
        subscriber_mode="external",
    )


# ── 6. Notification Worker Integration & Error Classification ────────────────

def test_is_permanent_email_error_classification():
    # Permanent Listmonk errors
    assert is_permanent_email_error(ListmonkAuthError("Unauthorized")) is True
    assert is_permanent_email_error(ListmonkNotFoundError("Template 404")) is True
    assert is_permanent_email_error(ListmonkConfigError("Template unconfigured")) is True
    assert is_permanent_email_error(ListmonkAmbiguousTimeoutError("Timeout")) is True

    # Wrapped in EmailDeliveryError (chained cause)
    wrapped_auth = EmailDeliveryError("Delivery failed")
    wrapped_auth.__cause__ = ListmonkAuthError("401")
    assert is_permanent_email_error(wrapped_auth) is True

    wrapped_ambiguous = EmailDeliveryError("Delivery failed")
    wrapped_ambiguous.__cause__ = ListmonkAmbiguousTimeoutError("Read timeout")
    assert is_permanent_email_error(wrapped_ambiguous) is True

    # Retryable transient errors
    assert is_permanent_email_error(ListmonkConnectTimeoutError("TCP timeout")) is False
    assert is_permanent_email_error(ListmonkServerError("500 Internal Error")) is False
    assert is_permanent_email_error(ListmonkRateLimitError("429 Too Many Requests")) is False


@pytest.mark.asyncio
async def test_notification_worker_ambiguous_timeout_prevents_blind_duplicate_retries():
    """Ensure worker does NOT blindly retry on ambiguous timeouts, halting duplicate sends."""
    worker = NotificationDeliveryWorker()

    job = MagicMock(spec=NotificationDelivery)
    job.id = 101
    job.recipient_email = "target@example.com"
    job.subject = "Security Notice"
    job.body_text = "Your password was reset"
    job.body_html = "<p>Your password was reset</p>"
    job.attempts = 1
    job.max_attempts = 5

    mock_persist = AsyncMock(return_value=True)
    worker._persist_delivery_outcome = mock_persist

    with patch(
        "liteads.ad_server.services.notification_worker.send_email",
        side_effect=ListmonkAmbiguousTimeoutError("Read timeout on POST /api/tx"),
    ):
        result = await worker.process_job(job, claim_token="token_abc")

    assert result is False
    # Verify outcome persisted with is_permanent_err=True and [AMBIGUOUS_TIMEOUT] tag
    mock_persist.assert_awaited_once()
    kwargs = mock_persist.call_args.kwargs
    assert kwargs["delivery_id"] == 101
    assert kwargs["success"] is False
    assert kwargs["is_permanent_err"] is True  # Halts blind retries!
    assert "[AMBIGUOUS_TIMEOUT]" in kwargs["error_msg"]

