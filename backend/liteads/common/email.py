"""Email service and provider adapters (Phase 6 & Phase 6.1B).

Supports pluggable delivery through an EmailProvider interface:
- SmtpEmailProvider: Delivers via SMTP / STARTTLS / SSL or mock delivery.
- ListmonkEmailProvider: Delivers via Listmonk transactional API (POST /api/tx)
  in external-recipient mode without enrolling users in marketing lists.

Provides unified send_email() interface preserving full backward compatibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from email.message import EmailMessage
import logging
import smtplib
import ssl
from typing import Any, Optional

from liteads.common.config import EmailSettings, ListmonkSettings, Settings, get_settings
from liteads.common.email_error import EmailDeliveryError
from liteads.common.email_templates import (
    EmailContent,
    build_password_changed_email,
    build_password_reset_email,
    build_verification_email,
)
from liteads.common.listmonk_client import (
    ListmonkClient,
    ListmonkConfigError,
    ListmonkError,
)

logger = logging.getLogger(__name__)


# ── Provider Abstract Interface ───────────────────────────────────────────────

class EmailProvider(ABC):
    """Abstract interface for transactional email delivery providers."""

    @abstractmethod
    async def send_email(
        self,
        to_address: str,
        subject: str,
        text_content: str,
        html_content: Optional[str] = None,
        template_purpose: Optional[str] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> None:
        """Deliver a transactional email."""
        pass


# ── SMTP Implementation ───────────────────────────────────────────────────────

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


class SmtpEmailProvider(EmailProvider):
    """Email delivery provider backed by standard SMTP."""

    def __init__(self, settings: Optional[EmailSettings] = None):
        self._settings = settings

    async def send_email(
        self,
        to_address: str,
        subject: str,
        text_content: str,
        html_content: Optional[str] = None,
        template_purpose: Optional[str] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> None:
        settings = self._settings or get_settings().email

        if settings.mock_delivery:
            logger.info(f"Mock email delivery to {to_address}. Subject: {subject}")
            return

        if not settings.host:
            raise EmailDeliveryError("Email delivery is not configured (LITEADS_EMAIL__HOST is empty).")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.from_address
        msg["To"] = to_address
        msg.set_content(text_content)

        if html_content:
            msg.add_alternative(html_content, subtype="html")

        await asyncio.to_thread(_send_smtp_sync, settings, msg, to_address)


# ── Listmonk Implementation ───────────────────────────────────────────────────

class ListmonkEmailProvider(EmailProvider):
    """Email delivery provider backed by Listmonk transactional API."""

    def __init__(
        self,
        settings: Optional[ListmonkSettings] = None,
        client: Optional[ListmonkClient] = None,
    ):
        self._settings = settings
        self._client = client

    def _get_client(self) -> ListmonkClient:
        if self._client is not None:
            return self._client
        cfg = self._settings or get_settings().listmonk
        return ListmonkClient(
            base_url=cfg.url,
            username=cfg.user,
            token=cfg.token,
            request_timeout=cfg.request_timeout,
            connect_timeout=cfg.connect_timeout,
        )

    def _resolve_template_id(self, template_purpose: Optional[str], settings: ListmonkSettings) -> int:
        purpose = (template_purpose or "").strip().lower()
        tmpl_id: Optional[int] = None

        if purpose in ("verification", "email_verification"):
            tmpl_id = settings.template_verification_id
        elif purpose in ("password_reset", "forgot_password"):
            tmpl_id = settings.template_password_reset_id
        elif purpose in ("security_notice", "security_alert", "password_changed"):
            tmpl_id = settings.template_security_notice_id
        elif purpose in ("digest", "weekly_digest", "publisher_digest"):
            tmpl_id = settings.template_digest_id

        # Fallback to generic template if specific is unconfigured or purpose unspecified
        if tmpl_id is None:
            tmpl_id = settings.template_generic_id

        if tmpl_id is None:
            raise ListmonkConfigError(
                f"No Listmonk template ID configured for purpose '{template_purpose or 'generic'}'. "
                f"Configure LISTMONK_TEMPLATE_{(template_purpose or 'generic').upper()}_ID or LISTMONK_TEMPLATE_GENERIC_ID."
            )
        return tmpl_id

    async def send_email(
        self,
        to_address: str,
        subject: str,
        text_content: str,
        html_content: Optional[str] = None,
        template_purpose: Optional[str] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> None:
        settings = self._settings or get_settings().listmonk
        template_id = self._resolve_template_id(template_purpose, settings)

        payload_data: dict[str, Any] = {
            "subject": subject,
            "body_text": text_content,
            "body_html": html_content or "",
        }
        if template_data:
            payload_data.update(template_data)

        client = self._get_client()
        await client.send_transactional(
            recipient_email=to_address,
            template_id=template_id,
            data=payload_data,
            subscriber_mode="external",
        )


# ── Provider Factory & Facade ─────────────────────────────────────────────────

def get_email_provider(settings: Optional[Settings] = None) -> EmailProvider:
    """Resolve active EmailProvider based on current configuration.

    Listmonk is selected ONLY when explicitly enabled (`settings.listmonk.enabled is True`).
    Otherwise falls back to standard SmtpEmailProvider.
    """
    if settings is None:
        settings = get_settings()

    listmonk_cfg = getattr(settings, "listmonk", None)
    if listmonk_cfg is not None and getattr(listmonk_cfg, "enabled", False) is True:
        return ListmonkEmailProvider(settings=listmonk_cfg)

    email_cfg = getattr(settings, "email", None)
    return SmtpEmailProvider(settings=email_cfg)


async def send_email(
    to_address: str,
    subject: str,
    text_content: str,
    html_content: Optional[str] = None,
    template_purpose: Optional[str] = None,
    template_data: Optional[dict[str, Any]] = None,
    provider: Optional[EmailProvider] = None,
) -> None:
    """Send an email via the configured active provider asynchronously."""
    active_provider = provider or get_email_provider()
    await active_provider.send_email(
        to_address=to_address,
        subject=subject,
        text_content=text_content,
        html_content=html_content,
        template_purpose=template_purpose,
        template_data=template_data,
    )


__all__ = [
    "EmailDeliveryError",
    "EmailProvider",
    "SmtpEmailProvider",
    "ListmonkEmailProvider",
    "get_email_provider",
    "send_email",
    "EmailContent",
    "build_verification_email",
    "build_password_reset_email",
    "build_password_changed_email",
]
