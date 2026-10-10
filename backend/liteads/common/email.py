"""
Email service.

Handles sending emails via SMTP, with a mock provider for tests.
"""

import smtplib
from email.message import EmailMessage
import logging
import ssl

from liteads.common.config import get_settings

logger = logging.getLogger(__name__)

class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""
    pass


async def send_email(to_address: str, subject: str, text_content: str, html_content: str | None = None) -> None:
    """
    Send an email via configured SMTP.
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

    try:
        if settings.port == 465:
            # Implicit TLS
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=settings.timeout_seconds)
        else:
            # Explicit TLS
            server = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds)
            if settings.use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        # Ensure server closes properly on exception by using context manager or explicit close
        try:
            if settings.user and settings.password:
                server.login(settings.user, settings.password)
            server.send_message(msg)
        finally:
            server.quit()

    except Exception as e:
        logger.error(f"Failed to send email to {to_address}: {e}")
        raise EmailDeliveryError(f"Email delivery failed: {str(e)}") from e
