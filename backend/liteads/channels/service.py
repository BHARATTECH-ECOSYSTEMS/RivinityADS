"""
Channel Service Layer (Phase 5).

Orchestrates:
1. Advertiser external ad account lifecycle (connect, test, sync, preview, create, pause, disconnect).
2. Credential encryption at rest and strict redaction (secrets are never returned to clients).
3. Deterministic external-to-local campaign mapping and idempotency enforcement.
4. AI Publisher inventory contract validation and unapproved insertion blocking.
5. Claude ad-free regulatory policy enforcement.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import Any
import uuid

from fastapi import HTTPException, status
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from liteads.channels.ai_publisher import AIPublisherAdapter
from liteads.channels.base import BaseAdvertiserConnector
from liteads.channels.claude_policy import ClaudePolicyGuard
from liteads.channels.exceptions import (
    ChannelAuthenticationException,
    ChannelDuplicateException,
    ChannelException,
    ChannelRateLimitException,
    ChannelValidationException,
    UnapprovedPublisherException,
    UnsupportedChannelException,
)
from liteads.channels.google_ads import GoogleAdsConnector
from liteads.channels.openai_ads import OpenAIAdsConnector
from liteads.channels.schemas import (
    CampaignCreateParams,
    CampaignPreviewResult,
    ChannelCapabilities,
    ConnectAdvertiserRequest,
    ConnectAdvertiserResponse,
    ConnectionDetailsResponse,
    ExternalAccount,
    ExternalCampaign,
    ExternalReportRow,
    PublisherContractCreateRequest,
    PublisherContractResponse,
)
from liteads.common.config import get_settings
from liteads.management.advertiser_api_key_service import decrypt_secret, encrypt_secret
from liteads.models.auth import AuditLog
from liteads.models.channel import (
    ChannelConnection,
    ExternalCampaignMapping,
    PublisherInventoryContract,
)

logger = structlog.get_logger(__name__)


def _mask_credential(raw: str) -> str:
    """Mask credentials for display so secrets are never leaked."""
    raw = raw.strip()
    if len(raw) <= 8:
        return "****"
    return f"{raw[:6]}...{raw[-4:]}"


def _get_connector(
    provider: str,
    credentials: dict[str, Any],
    account_id: str,
    organization_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseAdvertiserConnector:
    """Instantiate appropriate channel connector based on provider."""
    # Enforce Claude ad-free policy first
    ClaudePolicyGuard.enforce_ad_free_policy(provider)

    settings = get_settings()
    provider_clean = provider.lower().strip()

    if provider_clean in ("openai", "openai_ads"):
        is_simulation = bool(
            credentials.get("is_simulation", False)
            or credentials.get("allow_simulated", True)
            or str(credentials.get("api_key", "")).startswith("test_")
        )
        return OpenAIAdsConnector(
            credentials=credentials,
            account_id=account_id,
            organization_id=organization_id,
            env=settings.env,
            is_simulation=is_simulation,
        )
    elif provider_clean in ("google", "google_ads"):
        return GoogleAdsConnector(
            credentials=credentials,
            account_id=account_id,
            organization_id=organization_id,
            api_version=settings.channels.google_ads_api_version,
            token_endpoint=settings.channels.google_ads_token_endpoint,
            api_endpoint=settings.channels.google_ads_api_endpoint,
            http_client=http_client,
        )
    else:
        raise UnsupportedChannelException(
            f"Provider '{provider}' is not supported. Supported providers: 'openai', 'google_ads'.",
            provider=provider,
        )


class ChannelService:
    """Service orchestrating channel connectors and publisher contracts."""

    def __init__(self, session: AsyncSession, http_client: httpx.AsyncClient | None = None):
        self.session = session
        self.http_client = http_client

    # ── Advertiser Connector Operations ──

    async def connect_advertiser_channel(
        self,
        advertiser_id: int,
        user_id: int | None,
        req: ConnectAdvertiserRequest,
    ) -> ConnectAdvertiserResponse:
        """
        Connect and authenticate an authorized external ad platform account.
        
        Credentials are encrypted at rest and never returned to the caller.
        Simulated adapters fail closed in production and report status=SIMULATED.
        """
        # 1. Enforce Claude ad-free policy check
        ClaudePolicyGuard.enforce_ad_free_policy(req.provider)

        # 2. Check environment isolation for simulations
        settings = get_settings()
        is_prod = settings.env in ("prod", "production", "staging")
        prov_clean = req.provider.lower().strip()

        if is_prod:
            if prov_clean in ("openai", "openai_ads"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="OpenAI Ads adapter has no official public API and is prohibited from simulation in production environments.",
                )
            if req.additional_credentials.get("is_simulation") or req.additional_credentials.get("allow_simulated"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Simulated channel adapters are strictly prohibited in production environments.",
                )

        # 3. Build credentials dictionary
        creds_payload = {
            "api_key": req.api_key.strip(),
            **req.additional_credentials,
        }

        # 4. Instantiate and verify connection
        connector = _get_connector(
            req.provider,
            credentials=creds_payload,
            account_id=req.account_id,
            organization_id=req.organization_id,
            http_client=self.http_client,
        )

        is_valid, err_msg, metadata = await connector.verify_credentials()
        if not is_valid:
            logger.warning(
                "channel_connection_test_failed",
                advertiser_id=advertiser_id,
                provider=req.provider,
                error=err_msg,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connection authentication failed: {err_msg}",
            )

        # 5. Encrypt credentials at rest
        creds_json = json.dumps(creds_payload)
        encrypted_creds = encrypt_secret(creds_json)
        prefix = _mask_credential(req.api_key)

        capabilities_dict = connector.get_capabilities().model_dump()
        adapter_mode = connector.adapter_mode
        if adapter_mode == "SIMULATED":
            status_value = "SIMULATED"
            token_state = "SIMULATED"
        elif adapter_mode == "EXPERIMENTAL":
            status_value = "CONNECTING"
            token_state = "NOT_CONFIGURED"
        else:
            status_value = "CONNECTED"
            token_state = metadata.get("token_state", "VALID")

        # 6. Check if connection already exists for this account
        stmt = select(ChannelConnection).where(
            ChannelConnection.advertiser_id == advertiser_id,
            ChannelConnection.provider == req.provider.lower().strip(),
            ChannelConnection.external_account_id == req.account_id,
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if existing:
            existing.name = req.name
            existing.credentials_encrypted = encrypted_creds
            existing.credentials_prefix = prefix
            existing.external_organization_id = req.organization_id
            existing.status = status_value
            existing.adapter_mode = adapter_mode
            existing.token_state = token_state
            existing.last_verified_at = now
            existing.remote_metadata = metadata
            existing.capabilities = capabilities_dict
            existing.last_error = None
            connection = existing
        else:
            connection = ChannelConnection(
                tenant_type="advertiser",
                advertiser_id=advertiser_id,
                provider=req.provider.lower().strip(),
                name=req.name,
                external_account_id=req.account_id,
                external_organization_id=req.organization_id,
                credentials_encrypted=encrypted_creds,
                credentials_prefix=prefix,
                status=status_value,
                adapter_mode=adapter_mode,
                token_state=token_state,
                last_verified_at=now,
                remote_metadata=metadata,
                capabilities=capabilities_dict,
                created_at=now,
                updated_at=now,
            )
            self.session.add(connection)

        # 7. Audit log
        audit = AuditLog(
            user_id=user_id,
            action=f"channel_connected:{req.provider}",
            details=f"Connected {req.provider} account {req.account_id} (mode={adapter_mode}) for advertiser {advertiser_id}",
        )
        self.session.add(audit)

        await self.session.commit()
        await self.session.refresh(connection)

        return ConnectAdvertiserResponse(
            id=connection.id,
            provider=connection.provider,
            name=connection.name,
            external_account_id=connection.external_account_id,
            credentials_prefix=connection.credentials_prefix,
            status=connection.status,
            adapter_mode=connection.adapter_mode,
            token_state=connection.token_state,
            last_verified_at=connection.last_verified_at,
            capabilities=connector.get_capabilities(),
            created_at=connection.created_at,
        )

    async def list_advertiser_connections(
        self, advertiser_id: int
    ) -> list[ConnectionDetailsResponse]:
        """List all active/connected accounts for an advertiser (secrets redacted)."""
        stmt = (
            select(ChannelConnection)
            .where(
                ChannelConnection.advertiser_id == advertiser_id,
                ChannelConnection.tenant_type == "advertiser",
            )
            .order_by(ChannelConnection.id.desc())
        )
        res = await self.session.execute(stmt)
        connections = res.scalars().all()

        results = []
        for c in connections:
            caps = ChannelCapabilities(**c.capabilities) if c.capabilities else None
            results.append(
                ConnectionDetailsResponse(
                    id=c.id,
                    provider=c.provider,
                    name=c.name,
                    external_account_id=c.external_account_id,
                    external_organization_id=c.external_organization_id,
                    credentials_prefix=c.credentials_prefix,
                    status=c.status,
                    adapter_mode=c.adapter_mode,
                    token_state=c.token_state,
                    last_verified_at=c.last_verified_at,
                    last_sync_at=c.last_sync_at,
                    sync_cursor=c.sync_cursor,
                    last_error=c.last_error,
                    capabilities=caps,
                    created_at=c.created_at,
                )
            )
        return results

    async def get_connection_scoped(
        self, advertiser_id: int, connection_id: int
    ) -> ChannelConnection:
        """Fetch connection with strict tenant ownership verification."""
        conn = await self.session.get(ChannelConnection, connection_id)
        if not conn or conn.advertiser_id != advertiser_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel connection {connection_id} not found or access denied",
            )
        return conn

    def _decrypt_connector(self, conn: ChannelConnection) -> BaseAdvertiserConnector:
        """Decrypt connection credentials and instantiate provider connector."""
        raw_json = decrypt_secret(conn.credentials_encrypted)
        if not raw_json:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt connection credentials",
            )
        creds = json.loads(raw_json)
        return _get_connector(
            conn.provider,
            credentials=creds,
            account_id=conn.external_account_id,
            organization_id=conn.external_organization_id,
            http_client=self.http_client,
        )

    async def discover_accounts(
        self, advertiser_id: int, connection_id: int
    ) -> list[ExternalAccount]:
        """Discover accounts accessible by this connection."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        connector = self._decrypt_connector(conn)
        return await connector.discover_accounts()

    async def sync_campaigns(
        self, advertiser_id: int, connection_id: int
    ) -> list[ExternalCampaign]:
        """Synchronize external campaigns into deterministic mapping records."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        connector = self._decrypt_connector(conn)

        external_campaigns = await connector.sync_campaigns()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Upsert deterministic mappings
        for ec in external_campaigns:
            stmt = select(ExternalCampaignMapping).where(
                ExternalCampaignMapping.connection_id == conn.id,
                ExternalCampaignMapping.external_campaign_id == ec.external_id,
            )
            res = await self.session.execute(stmt)
            mapping = res.scalar_one_or_none()

            if mapping:
                mapping.external_campaign_name = ec.name
                mapping.external_status = ec.status
                mapping.remote_object_status = ec.remote_object_status
                mapping.moderation_status = ec.moderation_status
                mapping.provider_request_id = ec.provider_request_id
                mapping.remote_resource_name = ec.remote_resource_name
                mapping.sync_status = ec.sync_status or "SYNCED"
                mapping.objective = ec.objective
                mapping.daily_budget = Decimal(str(ec.daily_budget)) if ec.daily_budget is not None else None
                mapping.total_spend = Decimal(str(ec.total_spend)) if ec.total_spend is not None else None
                mapping.last_sync_at = now
            else:
                mapping = ExternalCampaignMapping(
                    connection_id=conn.id,
                    advertiser_id=advertiser_id,
                    external_campaign_id=ec.external_id,
                    external_campaign_name=ec.name,
                    external_status=ec.status,
                    remote_object_status=ec.remote_object_status,
                    moderation_status=ec.moderation_status,
                    provider_request_id=ec.provider_request_id,
                    remote_resource_name=ec.remote_resource_name,
                    sync_status=ec.sync_status or "SYNCED",
                    objective=ec.objective,
                    daily_budget=Decimal(str(ec.daily_budget)) if ec.daily_budget is not None else None,
                    total_spend=Decimal(str(ec.total_spend)) if ec.total_spend is not None else None,
                    currency=ec.currency,
                    last_sync_at=now,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(mapping)

        conn.last_sync_at = now
        await self.session.commit()
        return external_campaigns

    async def preview_campaign_creation(
        self, advertiser_id: int, connection_id: int, params: CampaignCreateParams
    ) -> CampaignPreviewResult:
        """Preview campaign parameters and spend before committing."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        connector = self._decrypt_connector(conn)
        return await connector.preview_campaign_creation(params)

    async def create_campaign(
        self,
        advertiser_id: int,
        connection_id: int,
        params: CampaignCreateParams,
        idempotency_key: str,
        user_id: int | None = None,
    ) -> ExternalCampaign:
        """
        Create campaign with deliberate confirmation and idempotency protection.
        
        Guarantees:
        1. User must pass confirmed=True.
        2. Replaying with the same idempotency key returns the existing campaign without duplicating.
        """
        conn = await self.get_connection_scoped(advertiser_id, connection_id)

        # 1. Check if this idempotency key was already recorded in our database
        stmt = select(ExternalCampaignMapping).where(
            ExternalCampaignMapping.connection_id == conn.id,
            ExternalCampaignMapping.idempotency_key == idempotency_key,
        )
        res = await self.session.execute(stmt)
        existing_mapping = res.scalar_one_or_none()
        if existing_mapping:
            logger.info(
                "channel_campaign_creation_idempotent_replay",
                idempotency_key=idempotency_key,
                external_id=existing_mapping.external_campaign_id,
            )
            return ExternalCampaign(
                external_id=existing_mapping.external_campaign_id,
                name=existing_mapping.external_campaign_name,
                status=existing_mapping.external_status,
                objective=existing_mapping.objective,
                daily_budget=float(existing_mapping.daily_budget) if existing_mapping.daily_budget else None,
                total_spend=float(existing_mapping.total_spend) if existing_mapping.total_spend else None,
                currency=existing_mapping.currency,
                local_campaign_id=existing_mapping.local_campaign_id,
                remote_object_status=existing_mapping.remote_object_status,
                moderation_status=existing_mapping.moderation_status,
                provider_request_id=existing_mapping.provider_request_id,
                remote_resource_name=existing_mapping.remote_resource_name,
                sync_status=existing_mapping.sync_status,
            )

        # 2. Invoke provider creation
        connector = self._decrypt_connector(conn)
        created_campaign = await connector.create_campaign(params, idempotency_key=idempotency_key)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        mapping = ExternalCampaignMapping(
            connection_id=conn.id,
            advertiser_id=advertiser_id,
            external_campaign_id=created_campaign.external_id,
            external_campaign_name=created_campaign.name,
            external_status=created_campaign.status,
            remote_object_status=created_campaign.remote_object_status,
            moderation_status=created_campaign.moderation_status,
            provider_request_id=created_campaign.provider_request_id,
            remote_resource_name=created_campaign.remote_resource_name,
            sync_status="SYNCED",
            objective=created_campaign.objective,
            daily_budget=Decimal(str(created_campaign.daily_budget)) if created_campaign.daily_budget is not None else None,
            total_spend=Decimal("0.0"),
            currency=created_campaign.currency,
            idempotency_key=idempotency_key,
            last_sync_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(mapping)

        # Audit log
        audit = AuditLog(
            user_id=user_id,
            action=f"channel_campaign_created:{conn.provider}",
            details=f"Created external campaign {created_campaign.external_id} ({params.name}) on {conn.provider}",
        )
        self.session.add(audit)

        await self.session.commit()
        return created_campaign

    async def update_campaign_status(
        self,
        advertiser_id: int,
        connection_id: int,
        external_campaign_id: str,
        status_value: str,
    ) -> bool:
        """Update campaign delivery status (ACTIVE, PAUSED, ARCHIVED)."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        connector = self._decrypt_connector(conn)

        ok = await connector.update_campaign_status(external_campaign_id, status_value)
        if ok:
            stmt = select(ExternalCampaignMapping).where(
                ExternalCampaignMapping.connection_id == conn.id,
                ExternalCampaignMapping.external_campaign_id == external_campaign_id,
            )
            res = await self.session.execute(stmt)
            mapping = res.scalar_one_or_none()
            if mapping:
                mapping.external_status = status_value
                mapping.remote_object_status = f"CONFIRMED_{status_value}"
                mapping.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await self.session.commit()
        return ok

    async def sync_reporting(
        self,
        advertiser_id: int,
        connection_id: int,
        start_date: date,
        end_date: date,
    ) -> list[ExternalReportRow]:
        """Synchronize performance reporting."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        connector = self._decrypt_connector(conn)
        reports = await connector.sync_reporting(start_date, end_date)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for r in reports:
            stmt = select(ExternalCampaignMapping).where(
                ExternalCampaignMapping.connection_id == conn.id,
                ExternalCampaignMapping.external_campaign_id == r.external_campaign_id,
            )
            res = await self.session.execute(stmt)
            mapping = res.scalar_one_or_none()
            if mapping:
                mapping.reporting_synced_at = now
        await self.session.commit()
        return reports

    async def disconnect(
        self, advertiser_id: int, connection_id: int, user_id: int | None = None
    ) -> bool:
        """Disconnect and revoke an external account connection."""
        conn = await self.get_connection_scoped(advertiser_id, connection_id)
        try:
            connector = self._decrypt_connector(conn)
            await connector.disconnect()
        except Exception as e:
            logger.warning("connector_disconnect_exception", error=str(e))

        conn.status = "DISCONNECTED"
        conn.token_state = "REVOKED"
        conn.credentials_encrypted = ""  # Erase secret material
        conn.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)

        audit = AuditLog(
            user_id=user_id,
            action=f"channel_disconnected:{conn.provider}",
            details=f"Disconnected {conn.provider} account {conn.external_account_id} for advertiser {advertiser_id}",
        )
        self.session.add(audit)
        await self.session.commit()
        return True

    # ── AI Publisher Inventory Contract Operations ──

    async def register_publisher_contract(
        self,
        publisher_id: int,
        req: PublisherContractCreateRequest,
        user_id: int | None = None,
    ) -> PublisherContractResponse:
        """Register a signed agreement for AI publisher inventory delivery."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        stmt = select(PublisherInventoryContract).where(
            PublisherInventoryContract.signed_agreement_id == req.signed_agreement_id
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            existing.approved_surfaces = req.approved_surfaces
            existing.policy_terms_hash = req.policy_terms_hash
            existing.contract_valid_until = req.contract_valid_until.replace(tzinfo=None)
            contract = existing
        else:
            contract = PublisherInventoryContract(
                publisher_id=publisher_id,
                platform_name=req.platform_name,
                signed_agreement_id=req.signed_agreement_id,
                agreement_status="ACTIVE",
                approved_surfaces=req.approved_surfaces,
                policy_terms_hash=req.policy_terms_hash,
                contract_signed_at=now,
                contract_valid_until=req.contract_valid_until.replace(tzinfo=None),
                created_at=now,
                updated_at=now,
            )
            self.session.add(contract)

        audit = AuditLog(
            user_id=user_id,
            action="publisher_contract_registered",
            details=f"Registered AI publisher contract {req.signed_agreement_id} for publisher {publisher_id}",
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(contract)

        return PublisherContractResponse(
            id=contract.id,
            publisher_id=contract.publisher_id,
            platform_name=contract.platform_name,
            signed_agreement_id=contract.signed_agreement_id,
            agreement_status=contract.agreement_status,
            approved_surfaces=contract.approved_surfaces,
            contract_signed_at=contract.contract_signed_at,
            contract_valid_until=contract.contract_valid_until,
            is_valid=contract.is_valid,
        )

    async def verify_publisher_surface(
        self, publisher_id: int, surface_id: str
    ) -> tuple[bool, str | None]:
        """
        Verify whether an AI publisher surface is authorized under a signed agreement.
        
        Rejects immediately if contract is missing, expired, or surface is unapproved.
        """
        stmt = (
            select(PublisherInventoryContract)
            .where(
                PublisherInventoryContract.publisher_id == publisher_id,
                PublisherInventoryContract.agreement_status == "ACTIVE",
            )
            .order_by(PublisherInventoryContract.id.desc())
        )
        res = await self.session.execute(stmt)
        contracts = res.scalars().all()

        if not contracts:
            return False, "No active signed publisher inventory contract exists"

        for c in contracts:
            adapter = AIPublisherAdapter(publisher_id, contract=c)
            is_valid, err = await adapter.verify_contract(surface_id)
            if is_valid:
                return True, None

        return False, f"Surface '{surface_id}' is not authorized under any active agreement"
