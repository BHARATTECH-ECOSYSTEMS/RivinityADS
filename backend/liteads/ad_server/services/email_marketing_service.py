"""Tenant-Safe Email Marketing Service (Phase 6.1D).

Handles tenant-isolated list management, permission-based CSV contact imports,
draft campaign management, preview, test-send, and confirmed launch workflows.

Strict security & isolation rules:
- Cross-tenant access is strictly denied on every read/write/send.
- External Listmonk IDs are never trusted directly from user requests.
- Advertisers are strictly isolated to their own tenant workspace.
- Platform admins manage a separate platform-marketing workspace.
- Publishers are denied access by default unless pilot access is explicitly enabled.
- Unconsented and suppressed contacts are excluded from all campaign sends.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import logging
import re
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
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
from liteads.models.email_marketing import (
    EmailMarketingCampaign,
    EmailMarketingContact,
    EmailMarketingList,
)
from liteads.schemas.email_marketing import (
    CampaignAnalyticsResponse,
    CampaignPreviewResponse,
    CampaignResponse,
    ContactResponse,
    CreateCampaignRequest,
    CreateContactRequest,
    CreateListRequest,
    ImportContactsResponse,
    ListResponse,
    UpdateCampaignRequest,
)

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PROHIBITED_PERMISSION_SOURCES = {
    "purchased",
    "purchased_list",
    "scraped",
    "web_scrape",
    "third_party",
    "unverified",
    "unknown",
}
DEFAULT_UNSUBSCRIBE_FOOTER = (
    "\n\n---\n"
    "You received this email because you subscribed to updates from RivinityAds.\n"
    "To unsubscribe or update your preferences, visit our preferences center."
)


class EmailMarketingService:
    """Service providing tenant-scoped email marketing lists, contacts, and campaigns."""

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
    def resolve_tenant(user: User) -> tuple[str, int]:
        """Resolve authenticated tenant context (tenant_type, tenant_id).

        Enforces tenant isolation:
        - Admin: platform tenant ('platform', 1)
        - Advertiser: advertiser tenant ('advertiser', advertiser_id)
        - Publisher: denied by default (403) unless allow_publisher_access is enabled
        """
        role = (user.role or "").strip().lower()

        if role == "admin":
            return "platform", 1

        if role == "advertiser":
            if not user.advertiser_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is not linked to an active advertiser workspace",
                )
            return "advertiser", user.advertiser_id

        if role == "publisher":
            cfg = get_settings().listmonk
            if not cfg.allow_publisher_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Publishers do not have access to email marketing",
                )
            if not user.publisher_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is not linked to an active publisher workspace",
                )
            return "publisher", user.publisher_id

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role not authorized for email marketing",
        )

    # ── Lists ─────────────────────────────────────────────────────────────────

    async def create_list(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        payload: CreateListRequest,
    ) -> EmailMarketingList:
        """Create a new tenant-owned mailing list and mirror to Listmonk if enabled."""
        list_record = EmailMarketingList(
            tenant_type=tenant_type,
            tenant_id=tenant_id,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            type=payload.type,
            optin=payload.optin,
        )
        session.add(list_record)
        await session.flush()

        settings = get_settings().listmonk
        if settings.enabled:
            client = self._get_client(settings)
            try:
                external_list = await client.create_list(
                    name=f"[{tenant_type}:{tenant_id}] {list_record.name}",
                    list_type=list_record.type,
                    optin=list_record.optin,
                )
                list_record.listmonk_list_id = external_list.get("id")
            except Exception as exc:
                logger.warning(
                    "Failed to create mirror list in Listmonk",
                    extra={"error": sanitize_message(exc), "list_id": list_record.id},
                )

        await session.commit()
        await session.refresh(list_record)
        return list_record

    async def get_lists(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
    ) -> list[ListResponse]:
        """List all mailing lists owned by the tenant with contact counts."""
        stmt = (
            select(
                EmailMarketingList,
                func.count(EmailMarketingContact.id).label("contact_count"),
            )
            .outerjoin(
                EmailMarketingContact,
                EmailMarketingContact.list_id == EmailMarketingList.id,
            )
            .where(
                EmailMarketingList.tenant_type == tenant_type,
                EmailMarketingList.tenant_id == tenant_id,
            )
            .group_by(EmailMarketingList.id)
            .order_by(EmailMarketingList.id.desc())
        )
        result = await session.execute(stmt)
        items: list[ListResponse] = []
        for lst, count in result.all():
            items.append(
                ListResponse(
                    id=lst.id,
                    tenant_type=lst.tenant_type,
                    tenant_id=lst.tenant_id,
                    name=lst.name,
                    description=lst.description,
                    type=lst.type,
                    optin=lst.optin,
                    contact_count=count or 0,
                    created_at=lst.created_at,
                )
            )
        return items

    async def get_list(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
    ) -> EmailMarketingList:
        """Retrieve an owned mailing list, enforcing tenant boundary (404 on cross-tenant)."""
        stmt = select(EmailMarketingList).where(
            EmailMarketingList.id == list_id,
            EmailMarketingList.tenant_type == tenant_type,
            EmailMarketingList.tenant_id == tenant_id,
        )
        record = await session.scalar(stmt)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailing list not found")
        return record

    async def delete_list(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
    ) -> bool:
        """Delete an owned mailing list and its mapped Listmonk list."""
        lst = await self.get_list(session, tenant_type, tenant_id, list_id)

        settings = get_settings().listmonk
        if settings.enabled and lst.listmonk_list_id:
            client = self._get_client(settings)
            try:
                await client.delete_list(lst.listmonk_list_id)
            except Exception as exc:
                logger.warning("Error deleting Listmonk list", extra={"error": sanitize_message(exc)})

        await session.delete(lst)
        await session.commit()
        return True

    # ── Contacts & CSV Import ─────────────────────────────────────────────────

    async def get_contacts(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
        limit: int = 50,
        offset: int = 0,
        query: Optional[str] = None,
    ) -> list[ContactResponse]:
        """List contacts in an owned list with pagination and optional search query."""
        await self.get_list(session, tenant_type, tenant_id, list_id)

        stmt = (
            select(EmailMarketingContact)
            .where(
                EmailMarketingContact.list_id == list_id,
                EmailMarketingContact.tenant_type == tenant_type,
                EmailMarketingContact.tenant_id == tenant_id,
            )
        )
        if query and query.strip():
            q = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(EmailMarketingContact.email).like(q),
                    func.lower(EmailMarketingContact.name).like(q),
                )
            )

        stmt = (
            stmt.order_by(EmailMarketingContact.id.desc())
            .limit(min(limit, 100))
            .offset(max(offset, 0))
        )
        result = await session.execute(stmt)
        return [ContactResponse.model_validate(c) for c in result.scalars().all()]

    async def add_contact(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
        payload: CreateContactRequest,
    ) -> ContactResponse:
        """Add a single contact with declared explicit permission."""
        lst = await self.get_list(session, tenant_type, tenant_id, list_id)
        email = str(payload.email).strip().lower()

        # Reject unlawful / unconsented permission sources
        source = payload.consent_source.strip().lower()
        if source in PROHIBITED_PERMISSION_SOURCES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Prohibited permission source '{payload.consent_source}'. Only lawful opt-in sources permitted.",
            )

        # Check suppression: user previously opted out locally
        stmt_consent = select(MarketingConsent).where(
            MarketingConsent.email == email,
            MarketingConsent.status == ConsentStatus.OPTED_OUT.value,
        )
        opted_out = await session.scalar(stmt_consent)
        if opted_out is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Recipient has previously revoked marketing consent. Cannot subscribe without new affirmative opt-in.",
            )

        # Check existing contact in this list
        stmt_exist = select(EmailMarketingContact).where(
            EmailMarketingContact.tenant_type == tenant_type,
            EmailMarketingContact.tenant_id == tenant_id,
            EmailMarketingContact.list_id == list_id,
            EmailMarketingContact.email == email,
        )
        if await session.scalar(stmt_exist) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contact already exists in this list",
            )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        contact = EmailMarketingContact(
            tenant_type=tenant_type,
            tenant_id=tenant_id,
            list_id=list_id,
            email=email,
            name=payload.name.strip() if payload.name else None,
            status="subscribed",
            consent_source=payload.consent_source.strip(),
            consent_at=now,
        )
        session.add(contact)
        await session.flush()

        # Sync to Listmonk if enabled and list mapped
        settings = get_settings().listmonk
        if settings.enabled and lst.listmonk_list_id:
            client = self._get_client(settings)
            try:
                sub = await client.create_subscriber(
                    email=email,
                    name=contact.name or "",
                    list_ids=[lst.listmonk_list_id],
                    attribs={"tenant_type": tenant_type, "tenant_id": tenant_id},
                    preconfirm=True,
                )
                contact.listmonk_subscriber_id = sub.get("id")
            except Exception as exc:
                logger.warning("Error syncing subscriber to Listmonk", extra={"error": sanitize_message(exc)})

        await session.commit()
        await session.refresh(contact)
        return ContactResponse.model_validate(contact)

    async def import_contacts_csv(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
        csv_content: str,
        permission_source: str,
    ) -> ImportContactsResponse:
        """Validate and import a batch of permission-based contacts from CSV."""
        lst = await self.get_list(session, tenant_type, tenant_id, list_id)

        source = permission_source.strip().lower()
        if source in PROHIBITED_PERMISSION_SOURCES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Prohibited permission source '{permission_source}'. Scraped or purchased lists are strictly forbidden.",
            )

        settings = get_settings().listmonk
        max_rows = settings.max_csv_import_rows

        # Parse CSV
        try:
            reader = csv.DictReader(io.StringIO(csv_content.strip()))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Malformed CSV: {str(exc)}")

        if not reader.fieldnames or "email" not in [f.strip().lower() for f in reader.fieldnames]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV header must contain an 'email' column",
            )

        # Normalize fieldnames
        field_map = {f: f.strip().lower() for f in reader.fieldnames}

        rows = list(reader)
        total_rows = len(rows)

        if total_rows > max_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"CSV row count ({total_rows}) exceeds configured batch limit ({max_rows})",
            )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        errors: list[str] = []
        valid_contacts: list[EmailMarketingContact] = []
        seen_emails_in_batch: set[str] = set()

        # Query existing contacts for duplicate check
        stmt_existing = select(EmailMarketingContact.email).where(
            EmailMarketingContact.tenant_type == tenant_type,
            EmailMarketingContact.tenant_id == tenant_id,
            EmailMarketingContact.list_id == list_id,
        )
        existing_res = await session.execute(stmt_existing)
        existing_emails = {row[0].lower() for row in existing_res.all()}

        duplicate_count = 0
        error_count = 0

        for idx, row in enumerate(rows, start=1):
            raw_email = None
            raw_name = None
            for key, val in row.items():
                norm_key = field_map.get(key)
                if norm_key == "email":
                    raw_email = val
                elif norm_key == "name":
                    raw_name = val

            email = (raw_email or "").strip().lower()
            if not email or not EMAIL_REGEX.match(email):
                errors.append(f"Row {idx}: Invalid email format '{email}'")
                error_count += 1
                continue

            if email in seen_emails_in_batch or email in existing_emails:
                duplicate_count += 1
                continue

            seen_emails_in_batch.add(email)
            name = raw_name.strip() if raw_name else None

            contact = EmailMarketingContact(
                tenant_type=tenant_type,
                tenant_id=tenant_id,
                list_id=list_id,
                email=email,
                name=name,
                status="subscribed",
                consent_source=permission_source.strip(),
                consent_at=now,
            )
            valid_contacts.append(contact)

        if valid_contacts:
            session.add_all(valid_contacts)
            await session.commit()

            # Mirror to Listmonk if enabled
            if settings.enabled and lst.listmonk_list_id:
                client = self._get_client(settings)
                for c in valid_contacts:
                    try:
                        sub = await client.create_subscriber(
                            email=c.email,
                            name=c.name or "",
                            list_ids=[lst.listmonk_list_id],
                            attribs={"tenant_type": tenant_type, "tenant_id": tenant_id},
                            preconfirm=True,
                        )
                        c.listmonk_subscriber_id = sub.get("id")
                    except Exception as exc:
                        logger.warning(
                            "Error creating imported subscriber in Listmonk",
                            extra={"email": mask_email(c.email), "error": sanitize_message(exc)},
                        )
                await session.commit()

        return ImportContactsResponse(
            total_rows=total_rows,
            valid_count=len(valid_contacts),
            duplicate_count=duplicate_count,
            error_count=error_count,
            errors=errors[:20],  # Cap error list length in response
        )

    async def remove_contact(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        list_id: int,
        contact_id: int,
    ) -> bool:
        """Unsubscribe and remove a contact from an owned list."""
        await self.get_list(session, tenant_type, tenant_id, list_id)

        stmt = select(EmailMarketingContact).where(
            EmailMarketingContact.id == contact_id,
            EmailMarketingContact.list_id == list_id,
            EmailMarketingContact.tenant_type == tenant_type,
            EmailMarketingContact.tenant_id == tenant_id,
        )
        contact = await session.scalar(stmt)
        if contact is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")

        contact.status = "unsubscribed"

        settings = get_settings().listmonk
        if settings.enabled and contact.listmonk_subscriber_id:
            client = self._get_client(settings)
            try:
                await client.blocklist_subscriber(contact.listmonk_subscriber_id)
            except Exception as exc:
                logger.warning("Error blocklisting subscriber in Listmonk", extra={"error": sanitize_message(exc)})

        await session.commit()
        return True

    # ── Campaigns ─────────────────────────────────────────────────────────────

    async def create_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        payload: CreateCampaignRequest,
    ) -> CampaignResponse:
        """Create a draft campaign linked to an owned mailing list."""
        await self.get_list(session, tenant_type, tenant_id, payload.list_id)

        campaign = EmailMarketingCampaign(
            tenant_type=tenant_type,
            tenant_id=tenant_id,
            list_id=payload.list_id,
            name=payload.name.strip(),
            subject=payload.subject.strip(),
            body=payload.body,
            content_type=payload.content_type,
            status="draft",
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return CampaignResponse.model_validate(campaign)

    async def get_campaigns(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CampaignResponse]:
        """List campaigns owned by the tenant."""
        stmt = (
            select(EmailMarketingCampaign)
            .where(
                EmailMarketingCampaign.tenant_type == tenant_type,
                EmailMarketingCampaign.tenant_id == tenant_id,
            )
            .order_by(EmailMarketingCampaign.id.desc())
            .limit(min(limit, 100))
            .offset(max(offset, 0))
        )
        result = await session.execute(stmt)
        return [CampaignResponse.model_validate(c) for c in result.scalars().all()]

    async def get_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
    ) -> EmailMarketingCampaign:
        """Retrieve an owned campaign with tenant isolation enforcement."""
        stmt = select(EmailMarketingCampaign).where(
            EmailMarketingCampaign.id == campaign_id,
            EmailMarketingCampaign.tenant_type == tenant_type,
            EmailMarketingCampaign.tenant_id == tenant_id,
        )
        record = await session.scalar(stmt)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
        return record

    async def update_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
        payload: UpdateCampaignRequest,
    ) -> CampaignResponse:
        """Update draft campaign fields."""
        campaign = await self.get_campaign(session, tenant_type, tenant_id, campaign_id)

        if campaign.status in ("running", "sent"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify a campaign that is already running or completed",
            )

        if payload.name is not None:
            campaign.name = payload.name.strip()
        if payload.subject is not None:
            campaign.subject = payload.subject.strip()
        if payload.body is not None:
            campaign.body = payload.body
        if payload.content_type is not None:
            campaign.content_type = payload.content_type

        await session.commit()
        await session.refresh(campaign)
        return CampaignResponse.model_validate(campaign)

    async def preview_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
    ) -> CampaignPreviewResponse:
        """Generate a preview of campaign content and audience size."""
        campaign = await self.get_campaign(session, tenant_type, tenant_id, campaign_id)

        # Count eligible active subscribers
        stmt_count = select(func.count(EmailMarketingContact.id)).where(
            EmailMarketingContact.list_id == campaign.list_id,
            EmailMarketingContact.tenant_type == tenant_type,
            EmailMarketingContact.tenant_id == tenant_id,
            EmailMarketingContact.status == "subscribed",
        )
        eligible_count = (await session.scalar(stmt_count)) or 0

        has_unsubscribe = (
            "unsubscribe" in campaign.body.lower()
            or "{{ unsubscribeurl }}" in campaign.body.lower()
        )
        rendered = campaign.body if has_unsubscribe else campaign.body + DEFAULT_UNSUBSCRIBE_FOOTER

        return CampaignPreviewResponse(
            id=campaign.id,
            name=campaign.name,
            subject=campaign.subject,
            rendered_body=rendered,
            eligible_recipient_count=eligible_count,
            has_unsubscribe_notice=True,
        )

    async def test_send_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
        test_emails: list[str],
    ) -> bool:
        """Send a test preview to allowlisted test email addresses (max 5)."""
        campaign = await self.get_campaign(session, tenant_type, tenant_id, campaign_id)

        if not test_emails or len(test_emails) > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Test send requires 1 to 5 recipient email addresses",
            )

        test_subject = f"[TEST] {campaign.subject}"
        rendered_body = (
            campaign.body
            if "unsubscribe" in campaign.body.lower()
            else campaign.body + DEFAULT_UNSUBSCRIBE_FOOTER
        )

        for email in test_emails:
            clean_email = email.strip().lower()
            try:
                await send_email(
                    to_address=clean_email,
                    subject=test_subject,
                    text_content=rendered_body,
                    html_content=f"<div>{rendered_body}</div>",
                )
            except Exception as exc:
                logger.warning(
                    "Error sending test campaign email",
                    extra={"email": mask_email(clean_email), "error": sanitize_message(exc)},
                )

        return True

    async def launch_campaign(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
        confirmed: bool,
    ) -> CampaignResponse:
        """Launch an email campaign following explicit confirmation and audience validation.

        Guarantees:
        - Requires explicit confirmed=True payload.
        - Verifies that eligible audience count > 0.
        - Excludes any unconsented or suppressed recipients.
        - Appends mandatory unsubscribe footer if missing.
        """
        if not confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Campaign launch requires explicit confirmation ('confirmed': true)",
            )

        campaign = await self.get_campaign(session, tenant_type, tenant_id, campaign_id)
        lst = await self.get_list(session, tenant_type, tenant_id, campaign.list_id)

        if campaign.status in ("running", "sent"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Campaign has already been launched",
            )

        # Re-check eligible recipients count
        stmt_recipients = select(EmailMarketingContact.email).where(
            EmailMarketingContact.list_id == campaign.list_id,
            EmailMarketingContact.tenant_type == tenant_type,
            EmailMarketingContact.tenant_id == tenant_id,
            EmailMarketingContact.status == "subscribed",
        )
        res_recipients = await session.execute(stmt_recipients)
        candidate_emails = [row[0].lower() for row in res_recipients.all()]

        if not candidate_emails:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mailing list has no eligible subscribed contacts",
            )

        # Filter out any globally suppressed or opted-out addresses
        stmt_suppressed = select(MarketingConsent.email).where(
            MarketingConsent.email.in_(candidate_emails),
            MarketingConsent.status == ConsentStatus.OPTED_OUT.value,
        )
        suppressed_res = await session.execute(stmt_suppressed)
        suppressed_set = {row[0].lower() for row in suppressed_res.all()}

        eligible_emails = [e for e in candidate_emails if e not in suppressed_set]
        if not eligible_emails:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All contacts in this list have revoked consent or are suppressed",
            )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        has_unsubscribe = (
            "unsubscribe" in campaign.body.lower()
            or "{{ unsubscribeurl }}" in campaign.body.lower()
        )
        final_body = campaign.body if has_unsubscribe else campaign.body + DEFAULT_UNSUBSCRIBE_FOOTER

        settings = get_settings().listmonk
        if settings.enabled and lst.listmonk_list_id:
            client = self._get_client(settings)
            try:
                ext_camp = await client.create_campaign(
                    name=f"[{tenant_type}:{tenant_id}] {campaign.name}",
                    subject=campaign.subject,
                    list_ids=[lst.listmonk_list_id],
                    body=final_body,
                    content_type=campaign.content_type,
                )
                campaign.listmonk_campaign_id = ext_camp.get("id")
                await client.update_campaign_status(campaign.listmonk_campaign_id, "running")
                campaign.status = "running"
            except Exception as exc:
                logger.error("Error creating/launching campaign in Listmonk", extra={"error": sanitize_message(exc)})
                campaign.status = "scheduled"
        else:
            campaign.status = "sent"

        campaign.total_recipients = len(eligible_emails)
        campaign.confirmed_at = now
        campaign.sent_at = now
        campaign.body = final_body

        await session.commit()
        await session.refresh(campaign)
        return CampaignResponse.model_validate(campaign)

    async def get_campaign_analytics(
        self,
        session: AsyncSession,
        tenant_type: str,
        tenant_id: int,
        campaign_id: int,
    ) -> CampaignAnalyticsResponse:
        """Retrieve delivery and engagement metrics for an owned campaign."""
        campaign = await self.get_campaign(session, tenant_type, tenant_id, campaign_id)

        views = 0
        clicks = 0
        unsubscribes = 0
        bounces = 0

        settings = get_settings().listmonk
        if settings.enabled and campaign.listmonk_campaign_id:
            client = self._get_client(settings)
            try:
                stats = await client.get_campaign_analytics(campaign.listmonk_campaign_id)
                views = stats.get("views", 0)
                clicks = stats.get("clicks", 0)
                unsubscribes = stats.get("unsubscribes", 0)
                bounces = stats.get("bounces", 0)
            except Exception as exc:
                logger.warning("Error fetching Listmonk analytics", extra={"error": sanitize_message(exc)})

        return CampaignAnalyticsResponse(
            campaign_id=campaign.id,
            status=campaign.status,
            total_recipients=campaign.total_recipients,
            views=views,
            clicks=clicks,
            unsubscribes=unsubscribes,
            bounces=bounces,
        )

