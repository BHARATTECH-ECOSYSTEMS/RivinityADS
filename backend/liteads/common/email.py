"""
Email service.

Handles sending emails via SMTP asynchronously without blocking the event loop,
with a mock provider for tests and reusable email templates.
"""

import asyncio
from email.message import EmailMessage
import logging
import smtplib
import ssl

from liteads.common.config import EmailSettings, get_settings
from liteads.common.email_templates import (
    EmailContent,
    build_password_changed_email,
    build_password_reset_email,
    build_verification_email,
)

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""
    pass


def _send_smtp_sync(settings: EmailSettings, msg: EmailMessage, to_address: str) -> None:
    """Synchronous worker that performs network I/O with the SMTP server."""
    try:
        if settings.port == 465:
            # Implicit TLS / SSL
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=settings.timeout_seconds)
        else:
            # Explicit TLS / STARTTLS or plain SMTP
            server = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds)
            if settings.use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        try:
            if settings.user and settings.password:
                server.login(settings.user, settings.password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                server.close()

    except Exception as e:
        logger.error(f"Failed to send email to {to_address}: {e}")
        raise EmailDeliveryError(f"Email delivery failed: {str(e)}") from e


async def send_email(to_address: str, subject: str, text_content: str, html_content: str | None = None) -> None:
    """
    Send an email via configured SMTP without blocking the asyncio event loop.
    """
    settings = get_settings().email

    if settings.mock_delivery:
        logger.info(f"Mock email delivery to {to_address}. Subject: {subject}")
        # In a real test environment, we might store these in a mock backend list.
        # But this suffices to skip real transmission.
        return

    if not settings.host:
        raise EmailDeliveryError("Email delivery is not configured (LITEADS_EMAIL__HOST is empty).")

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = settings.from_address
    msg['To'] = to_address
    msg.set_content(text_content)

    if html_content:
        msg.add_alternative(html_content, subtype='html')

    await asyncio.to_thread(_send_smtp_sync, settings, msg, to_address)


__all__ = [
    "EmailDeliveryError",
    "send_email",
    "EmailContent",
    "build_verification_email",
    "build_password_reset_email",
    "build_password_changed_email",
]
