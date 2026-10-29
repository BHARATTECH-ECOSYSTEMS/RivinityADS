"""Marketing Consent & Listmonk Subscriber Synchronization Service (Phase 6.1C).

Manages authoritative local consent records, explicit opt-in/opt-out preferences,
public double opt-in subscriptions, and idempotent synchronization with Listmonk.

Strict privacy & safety guarantees:
- Transactional email delivery and account registration NEVER imply marketing consent.
- Minimal attribute sync: ONLY allowlisted attributes ('name', 'role') are shared.
- An opted-out or revoked consent record is NEVER re-subscribed by retry or reconciliation.
- External API calls are executed strictly OUTSIDE database transactions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
import secrets
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import ListmonkSettings, get_settings
from liteads.common.email import send_email
from liteads.common.listmonk_client import (
    ListmonkClient,
    ListmonkError,
    mask_email,
    sanitize_message,
)
from liteads.models.auth import User
from liteads.models.consent import (
    ConsentCategory,
    ConsentSource,
    ConsentStatus,
    MarketingConsent,
)
from liteads.schemas.marketing import (
    MarketingConsentItem,
    MarketingPreferencesResponse,
    ReconciliationResultResponse,
)

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOUBLE_OPT_IN_EXPIRY_HOURS = 48


class MarketingConsentService:
    """Service orchestrating marketing consent, preferences, and Listmonk synchronization."""

    def __init__(self, client: Optional[ListmonkClient] = None):
        self._client = client

    def _get_client(self, settings: Optional[ListmonkSettings] = None) -> ListmonkClient:
        if self._client is not None:
            return self._client
        cfg = settings or get_settings().listmonk
        return ListmonkClient(
            base_url=cfg.url,
            username=cfg.user,
            token=cfg.token,
            request_timeout=cfg.request_timeout,
            connect_timeout=cfg.connect_timeout,
        )

    @staticmethod
    def resolve_list_id(category: str, settings: Optional[ListmonkSettings] = None) -> Optional[int]:
        """Map marketing category to configured Listmonk list ID from environment."""
        cfg = settings or get_settings().listmonk
        cat = category.strip().lower()
        if cat == ConsentCategory.PRODUCT_NEWSLETTER.value:
            return cfg.list_newsletter_id
        elif cat == ConsentCategory.ADVERTISER_EDUCATION.value:
            return cfg.list_advertiser_education_id
        elif cat == ConsentCategory.PUBLISHER_EDUCATION.value:
            return cfg.list_publisher_education_id
        return None

    # ── Authenticated User Preferences ────────────────────────────────────────

    async def get_user_preferences(
        self,
        session: AsyncSession,
        user: User,
    ) -> MarketingPreferencesResponse:
        """Retrieve marketing preferences for an authenticated user.

        Defaults to opted_out (False) for any unrecorded category: marketing consent
        is never assumed or inherited from account creation.
        """
        email = user.email.strip().lower()
        stmt = select(MarketingConsent).where(MarketingConsent.email == email)
        result = await session.execute(stmt)
        existing_consents = {c.category: c for c in result.scalars().all()}

        items: list[MarketingConsentItem] = []
        for cat in ConsentCategory:
            cat_value = cat.value
            consent_record = existing_consents.get(cat_value)

            if consent_record:
                is_opted_in = (consent_record.status == ConsentStatus.OPTED_IN.value)
                items.append(
                    MarketingConsentItem(
                        category=cat_value,
                        status=consent_record.status,
                        opted_in=is_opted_in,
                        consented_at=consent_record.consented_at,
                        confirmed_at=consent_record.confirmed_at,
                        revoked_at=consent_record.revoked_at,
                        policy_version=consent_record.policy_version,
                    )
                )
            else:
                # Explicit consent required: default is not opted in
                items.append(
                    MarketingConsentItem(
                        category=cat_value,
                        status=ConsentStatus.OPTED_OUT.value,
                        opted_in=False,
                        consented_at=None,
                        confirmed_at=None,
                        revoked_at=None,
                        policy_version=get_settings().listmonk.marketing_policy_version,
                    )
                )

        return MarketingPreferencesResponse(email=email, items=items)

    async def update_user_preference(
        self,
        session: AsyncSession,
        user: User,
        category: str,
        opted_in: bool,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> MarketingConsent:
        """Update consent state for an authenticated user and trigger Listmonk synchronization.

        Affirmative opt-in records consented_at. Opt-out records revoked_at and
        permanently suppresses future marketing dispatches for this category.
        """
        valid_categories = {c.value for c in ConsentCategory}
        cat = category.strip().lower()
        if cat not in valid_categories:
            raise ValueError(f"Invalid marketing category: {category}. Allowed: {valid_categories}")

        email = user.email.strip().lower()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        settings = get_settings().listmonk

        stmt = select(MarketingConsent).where(
            MarketingConsent.email == email,
            MarketingConsent.category == cat,
        )
        record = await session.scalar(stmt)

        if record is None:
            record = MarketingConsent(
                user_id=user.id,
                email=email,
                category=cat,
                policy_version=settings.marketing_policy_version,
                source=ConsentSource.PREFERENCES.value,
            )
            session.add(record)

        record.user_id = user.id
        record.ip_address = ip_address
        record.user_agent = user_agent[:255] if user_agent else None
        record.source = ConsentSource.PREFERENCES.value

        if opted_in:
            record.status = ConsentStatus.OPTED_IN.value
            record.consented_at = now
            record.revoked_at = None
        else:
            record.status = ConsentStatus.OPTED_OUT.value
            record.revoked_at = now

        await session.commit()
        await session.refresh(record)

        # Synchronize with Listmonk OUTSIDE the database transaction
        await self.sync_subscriber_to_listmonk(
            session=session,
            consent=record,
            name=user.name,
            role=user.role,
        )

        return record

    # ── Public Double Opt-In Subscription ─────────────────────────────────────

    async def public_subscribe(
        self,
        session: AsyncSession,
        email: str,
        name: Optional[str] = None,
        category: str = "product_newsletter",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        frontend_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Handle public subscription with double opt-in confirmation.

        Returns generic success response to prevent email enumeration attacks.
        """
        normalized_email = email.strip().lower()
        if not EMAIL_REGEX.match(normalized_email):
            # Return generic success to avoid enumeration
            return True, "If this email is eligible, a confirmation link has been sent."

        valid_categories = {c.value for c in ConsentCategory}
        cat = category.strip().lower()
        if cat not in valid_categories:
            cat = ConsentCategory.PRODUCT_NEWSLETTER.value

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        settings = get_settings()

        stmt = select(MarketingConsent).where(
            MarketingConsent.email == normalized_email,
            MarketingConsent.category == cat,
        )
        record = await session.scalar(stmt)

        # If already confirmed and active, return generic success
        if record is not None and record.status == ConsentStatus.OPTED_IN.value:
            return True, "If this email is eligible, a confirmation link has been sent."

        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=DOUBLE_OPT_IN_EXPIRY_HOURS)

        if record is None:
            record = MarketingConsent(
                email=normalized_email,
                category=cat,
                status=ConsentStatus.PENDING_CONFIRMATION.value,
                source=ConsentSource.PUBLIC_FORM.value,
                policy_version=settings.listmonk.marketing_policy_version,
                confirmation_token=token,
                confirmation_token_expires_at=expires_at,
                ip_address=ip_address,
                user_agent=user_agent[:255] if user_agent else None,
            )
            session.add(record)
        else:
            record.status = ConsentStatus.PENDING_CONFIRMATION.value
            record.source = ConsentSource.PUBLIC_FORM.value
            record.confirmation_token = token
            record.confirmation_token_expires_at=expires_at
            record.ip_address = ip_address
            record.user_agent = user_agent[:255] if user_agent else None

        await session.commit()

        # Send double opt-in confirmation email
        base_url = (frontend_url or settings.server.frontend_url).rstrip("/")
        confirm_url = f"{base_url}/marketing/confirm?token={token}"
        subject = "Confirm your RivinityADS subscription"
        text_body = (
            f"Thank you for requesting updates from RivinityADS.\n\n"
            f"Please confirm your subscription by clicking the link below:\n{confirm_url}\n\n"
            f"If you did not request this, please ignore this email."
        )
        html_body = (
            f"<p>Thank you for requesting updates from RivinityADS.</p>"
            f"<p><a href=\"{confirm_url}\">Click here to confirm your subscription</a></p>"
            f"<p>If you did not request this, please ignore this email.</p>"
        )

        try:
            await send_email(
                to_address=normalized_email,
                subject=subject,
                text_content=text_body,
                html_content=html_body,
                template_purpose="verification",
                template_data={"token": token, "confirm_url": confirm_url},
            )
        except Exception as exc:
            logger.warning(
                "Failed to dispatch double opt-in confirmation email",
                extra={"email": mask_email(normalized_email), "error": sanitize_message(exc)},
            )

        return True, "If this email is eligible, a confirmation link has been sent."

    async def public_confirm(
        self,
        session: AsyncSession,
        token: str,
    ) -> tuple[bool, str]:
        """Confirm a double opt-in token and activate the marketing subscription."""
        if not token or len(token) < 16:
            return False, "Confirmation token is invalid."

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = select(MarketingConsent).where(
            MarketingConsent.confirmation_token == token,
            MarketingConsent.confirmation_token_expires_at > now,
        )
        record = await session.scalar(stmt)

        if record is None:
            return False, "Confirmation link is invalid or has expired."

        if record.status == ConsentStatus.OPTED_IN.value:
            return True, "Subscription is already confirmed."

        record.status = ConsentStatus.OPTED_IN.value
        record.confirmed_at = now
        record.consented_at = now
        record.revoked_at = None
        record.confirmation_token = None

        await session.commit()
        await session.refresh(record)

        # Synchronize with Listmonk
        await self.sync_subscriber_to_listmonk(
            session=session,
            consent=record,
        )

        return True, "Subscription confirmed successfully. Thank you!"

    # ── Listmonk Subscriber Synchronization ───────────────────────────────────

    async def sync_subscriber_to_listmonk(
        self,
        session: AsyncSession,
        consent: MarketingConsent,
        name: str = "",
        role: str = "",
    ) -> bool:
        """Idempotently sync consent status to Listmonk.

        - Respects opt-out: if status == 'opted_out', removes subscriber from list.
        - NEVER re-subscribes an address if consent.status is opted_out or revoked_at is set.
        - Minimal allowlist: only syncs name and role attributes.
        """
        settings = get_settings().listmonk
        if not settings.enabled:
            return True

        target_list_id = self.resolve_list_id(consent.category, settings)
        if target_list_id is None:
            logger.debug(
                "Skipping Listmonk sync: no list ID configured for category",
                extra={"category": consent.category},
            )
            return True

        client = self._get_client(settings)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        email = consent.email.strip().lower()

        try:
            existing_sub = await client.get_subscriber_by_email(email)

            if consent.status == ConsentStatus.OPTED_IN.value:
                # Minimal allowlist: role only
                attribs = {"role": role} if role else {}

                if existing_sub:
                    sub_id = existing_sub["id"]
                    existing_lists = [
                        l["id"] if isinstance(l, dict) else l
                        for l in existing_sub.get("lists", [])
                    ]
                    updated_lists = sorted(list(set(existing_lists) | {target_list_id}))

                    await client.update_subscriber(
                        subscriber_id=sub_id,
                        list_ids=updated_lists,
                        status="enabled",
                        attribs=attribs,
                    )
                    consent.listmonk_subscriber_id = sub_id
                else:
                    new_sub = await client.create_subscriber(
                        email=email,
                        name=name,
                        list_ids=[target_list_id],
                        attribs=attribs,
                        preconfirm=True,
                    )
                    consent.listmonk_subscriber_id = new_sub.get("id")

                consent.last_synced_at = now
                consent.sync_error = None

            elif consent.status == ConsentStatus.OPTED_OUT.value:
                # Opt-out: remove from list
                if existing_sub:
                    sub_id = existing_sub["id"]
                    existing_lists = [
                        l["id"] if isinstance(l, dict) else l
                        for l in existing_sub.get("lists", [])
                    ]
                    updated_lists = [lid for lid in existing_lists if lid != target_list_id]

                    # If no lists remain, blocklist subscriber globally
                    new_status = "blocklisted" if not updated_lists else "enabled"
                    await client.update_subscriber(
                        subscriber_id=sub_id,
                        list_ids=updated_lists,
                        status=new_status,
                    )

                consent.last_synced_at = now
                consent.sync_error = None

            await session.execute(
                update(MarketingConsent)
                .where(MarketingConsent.id == consent.id)
                .values(
                    listmonk_subscriber_id=consent.listmonk_subscriber_id,
                    last_synced_at=consent.last_synced_at,
                    sync_error=consent.sync_error,
                )
            )
            await session.commit()
            return True

        except (ListmonkError, Exception) as exc:
            sanitized_err = sanitize_message(str(exc))
            logger.error(
                "Error synchronizing marketing subscriber to Listmonk",
                extra={"email": mask_email(email), "category": consent.category, "error": sanitized_err},
            )
            await session.execute(
                update(MarketingConsent)
                .where(MarketingConsent.id == consent.id)
                .values(
                    sync_error=sanitized_err,
                    last_synced_at=now,
                )
            )
            await session.commit()
            return False

    # ── Unsubscribe Reconciliation ────────────────────────────────────────────

    async def reconcile_unsubscribes(
        self,
        session: AsyncSession,
    ) -> ReconciliationResultResponse:
        """Poll Listmonk for blocklisted subscribers and reflect unsubscribes locally.

        Never re-subscribes local opted-out records. Detects external unsubscriptions
        occurring via Listmonk campaign unsubscribe links.
        """
        settings = get_settings().listmonk
        if not settings.enabled:
            return ReconciliationResultResponse(checked_count=0, updated_opt_outs=0)

        client = self._get_client(settings)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        errors: list[str] = []
        updated_count = 0

        try:
            # Query blocklisted subscribers from Listmonk
            blocklisted_subs = await client.get_subscribers(query="subscribers.status = 'blocklisted'")
            checked_count = len(blocklisted_subs)

            for sub in blocklisted_subs:
                sub_email = sub.get("email", "").strip().lower()
                if not sub_email:
                    continue

                # Find all active opted_in records for this email
                stmt = select(MarketingConsent).where(
                    MarketingConsent.email == sub_email,
                    MarketingConsent.status == ConsentStatus.OPTED_IN.value,
                )
                res = await session.execute(stmt)
                records = res.scalars().all()

                for record in records:
                    record.status = ConsentStatus.OPTED_OUT.value
                    record.revoked_at = now
                    record.source = ConsentSource.LISTMONK_RECONCILIATION.value
                    record.last_synced_at = now
                    updated_count += 1

            if updated_count > 0:
                await session.commit()

            return ReconciliationResultResponse(
                checked_count=checked_count,
                updated_opt_outs=updated_count,
                errors=errors,
            )

        except Exception as exc:
            err_msg = sanitize_message(str(exc))
            logger.error("Error during Listmonk unsubscribe reconciliation", extra={"error": err_msg})
            errors.append(err_msg)
            return ReconciliationResultResponse(
                checked_count=0,
                updated_opt_outs=0,
                errors=errors,
            )

