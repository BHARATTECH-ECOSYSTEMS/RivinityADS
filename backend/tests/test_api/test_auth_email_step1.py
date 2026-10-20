"""
Unit and integration tests for Step 1: Request schemas, non-blocking email delivery, and reusable email templates.
"""

import asyncio
from email.message import EmailMessage
import html
import threading
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
import pytest

from liteads.common.config import EmailSettings
from liteads.common.email import (
    EmailContent,
    EmailDeliveryError,
    build_password_changed_email,
    build_password_reset_email,
    build_verification_email,
    send_email,
)
from liteads.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    RequestPasswordResetRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)


# ── 1. Request Schema Validation Tests ──────────────────────────────────────────

def test_request_password_reset_schema_valid():
    req = RequestPasswordResetRequest(email="valid.user@example.com")
    assert req.email == "valid.user@example.com"

    # Alias ForgotPasswordRequest
    req2 = ForgotPasswordRequest(email="user@test.org")
    assert req2.email == "user@test.org"


def test_request_password_reset_schema_invalid():
    with pytest.raises(ValidationError):
        RequestPasswordResetRequest(email="not-an-email")

    with pytest.raises(ValidationError):
        RequestPasswordResetRequest(email="")


def test_resend_verification_schema_valid():
    req = ResendVerificationRequest(email="advertiser@brand.in")
    assert req.email == "advertiser@brand.in"


def test_resend_verification_schema_invalid():
    with pytest.raises(ValidationError):
        ResendVerificationRequest(email="invalid_address")


def test_verify_email_schema_valid():
    req = VerifyEmailRequest(token="secure_token_12345")
    assert req.token == "secure_token_12345"


def test_verify_email_schema_invalid():
    with pytest.raises(ValidationError):
        VerifyEmailRequest(token="")


def test_existing_auth_schemas_remain_intact():
    reg = RegisterRequest(
        name="Test Advertiser",
        email="adv@example.com",
        password="ValidPassword123!",
        role="advertiser",
    )
    assert reg.role == "advertiser"

    login = LoginRequest(email="adv@example.com", password="password")
    assert login.email == "adv@example.com"

    reset = ResetPasswordRequest(token="tok", password="ValidPassword123!")
    assert reset.token == "tok"


# ── 2. Reusable Email Template Tests ───────────────────────────────────────────

def test_build_verification_email_link_and_content():
    content = build_verification_email(
        recipient_email="test@example.com",
        token="token_abc_123",
        frontend_url="https://ads.example.com",
        expiry_hours=24,
    )

    assert isinstance(content, EmailContent)
    assert content.subject == "Verify your RivinityADS account"
    assert "https://ads.example.com/verify-email?token=token_abc_123" in content.text_content
    assert "24 hours" in content.text_content
    assert "https://ads.example.com/verify-email?token=token_abc_123" in content.html_content

    # Check unpacking support
    subject, text_body, html_body = content
    assert subject == content.subject
    assert text_body == content.text_content
    assert html_body == content.html_content


def test_build_verification_email_html_escaping():
    malicious_email = 'user<script>alert("xss")</script>@example.com'
    special_token = 'token&val="123"<script>'

    content = build_verification_email(
        recipient_email=malicious_email,
        token=special_token,
        frontend_url="http://localhost:5173",
    )

    # HTML content must escape dangerous characters
    assert "<script>" not in content.html_content
    assert "&lt;script&gt;" in content.html_content
    assert html.escape(malicious_email, quote=True) in content.html_content

    # Plain text content retains raw values
    assert malicious_email in content.text_content


def test_build_password_reset_email_link_and_content():
    content = build_password_reset_email(
        recipient_email="reset@example.com",
        token="reset_token_xyz",
        frontend_url="https://ads.example.com",
        expiry_hours=1,
    )

    assert content.subject == "Reset your RivinityADS password"
    assert "https://ads.example.com/reset-password?token=reset_token_xyz" in content.text_content
    assert "1 hour(s)" in content.text_content
    assert "https://ads.example.com/reset-password?token=reset_token_xyz" in content.html_content
    assert "Reset Password" in content.html_content


def test_build_password_reset_email_escaping():
    malicious_email = 'target<img src=x onerror=alert(1)>@test.com'
    content = build_password_reset_email(
        recipient_email=malicious_email,
        token="safe-token",
        frontend_url="http://localhost:5173",
    )
    assert "<img" not in content.html_content
    assert "&lt;img" in content.html_content


def test_build_password_changed_email():
    content = build_password_changed_email(
        recipient_email="user@domain.com",
        frontend_url="https://ads.example.com",
    )

    assert content.subject == "Security Alert: Your RivinityADS password has been changed"
    assert "https://ads.example.com/login" in content.text_content
    assert "https://ads.example.com/login" in content.html_content
    assert "user@domain.com" in content.text_content
    assert "Was this you?" in content.html_content


# ── 3. Non-Blocking SMTP Execution Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_runs_in_worker_thread():
    """Ensure SMTP network call is offloaded from the main asyncio thread to a worker thread."""
    main_thread_id = threading.get_ident()
    captured_worker_thread_id = None

    def fake_send_smtp_sync(settings, msg, to_address):
        nonlocal captured_worker_thread_id
        captured_worker_thread_id = threading.get_ident()

    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="smtp.example.com",
        port=587,
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("liteads.common.email._send_smtp_sync", side_effect=fake_send_smtp_sync):
        await send_email(
            to_address="test@example.com",
            subject="Test Subject",
            text_content="Plain text content",
        )

    assert captured_worker_thread_id is not None
    assert captured_worker_thread_id != main_thread_id, "SMTP operation was not executed in a separate worker thread!"


@pytest.mark.asyncio
async def test_send_email_smtp_standard_flow():
    """Verify that SMTP client is configured, sends message, and quits cleanly."""
    mock_smtp_instance = MagicMock()
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="mailpit",
        port=1025,
        user="testuser",
        password="testpassword",
        from_address="noreply@example.com",
        use_tls=True,
        timeout_seconds=5,
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_class:

        await send_email(
            to_address="recipient@example.com",
            subject="Test Welcome",
            text_content="Welcome text",
            html_content="<p>Welcome html</p>",
        )

        mock_smtp_class.assert_called_once_with("mailpit", 1025, timeout=5)
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("testuser", "testpassword")
        assert mock_smtp_instance.send_message.call_count == 1

        sent_msg: EmailMessage = mock_smtp_instance.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "Test Welcome"
        assert sent_msg["From"] == "noreply@example.com"
        assert sent_msg["To"] == "recipient@example.com"
        mock_smtp_instance.quit.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_ssl_port_465():
    """Verify that port 465 uses SMTP_SSL."""
    mock_smtp_ssl_instance = MagicMock()
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="smtp.gmail.com",
        port=465,
        from_address="noreply@example.com",
        use_tls=False,
        timeout_seconds=5,
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("smtplib.SMTP_SSL", return_value=mock_smtp_ssl_instance) as mock_smtp_ssl_class:

        await send_email(
            to_address="recipient@example.com",
            subject="SSL Test",
            text_content="Body",
        )

        assert mock_smtp_ssl_class.call_count == 1
        mock_smtp_ssl_instance.send_message.assert_called_once()
        mock_smtp_ssl_instance.quit.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_no_auth_when_empty_credentials():
    """Verify login is skipped when credentials are not configured."""
    mock_smtp_instance = MagicMock()
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="mailpit",
        port=1025,
        user="",
        password="",
        from_address="noreply@example.com",
        use_tls=False,
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("smtplib.SMTP", return_value=mock_smtp_instance):

        await send_email(
            to_address="recipient@example.com",
            subject="No Auth Test",
            text_content="Body",
        )

        mock_smtp_instance.login.assert_not_called()
        mock_smtp_instance.send_message.assert_called_once()


# ── 4. Exception Handling & Mock Delivery Tests ────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_propagates_email_delivery_error_on_smtp_failure():
    """SMTP network or protocol errors must be wrapped and propagated as EmailDeliveryError."""
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.send_message.side_effect = ConnectionResetError("Connection lost")

    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="mailpit",
        port=1025,
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("smtplib.SMTP", return_value=mock_smtp_instance):

        with pytest.raises(EmailDeliveryError) as exc_info:
            await send_email(
                to_address="fail@example.com",
                subject="Fail Test",
                text_content="Content",
            )

        assert "Email delivery failed" in str(exc_info.value)
        assert "Connection lost" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_email_raises_when_host_unconfigured():
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="",
        mock_delivery=False,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings):
        with pytest.raises(EmailDeliveryError) as exc_info:
            await send_email(
                to_address="test@example.com",
                subject="Host Test",
                text_content="Content",
            )
        assert "LITEADS_EMAIL__HOST is empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_email_mock_delivery_skips_smtp():
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(
        host="smtp.example.com",
        mock_delivery=True,
    )

    with patch("liteads.common.email.get_settings", return_value=mock_settings), \
         patch("smtplib.SMTP") as mock_smtp_class:

        await send_email(
            to_address="mock@example.com",
            subject="Mock Test",
            text_content="Mock content",
        )

        mock_smtp_class.assert_not_called()


# ── 5. Existing Caller Compatibility Test ──────────────────────────────────────

@pytest.mark.asyncio
async def test_existing_caller_compatibility():
    """Confirm existing call pattern (as used by publisher_digest_service) works with new send_email."""
    mock_settings = MagicMock()
    mock_settings.email = EmailSettings(mock_delivery=True)

    with patch("liteads.common.email.get_settings", return_value=mock_settings):
        # Caller passing positional arguments
        await send_email(
            "publisher@domain.com",
            "Weekly Digest",
            "Plain text digest",
            "<div>HTML digest</div>",
        )
