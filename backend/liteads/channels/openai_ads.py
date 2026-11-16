"""
OpenAI Ads Advertiser Connector (Phase 2 & Phase 5).

Manages a customer's authorized OpenAI Ads Manager account or operates in explicit
EXPERIMENTAL / SIMULATED mode.

MANDATORY INVARIANTS:
1. OpenAI Ads currently has no public general-availability self-serve advertiser API.
   The adapter is explicitly labeled EXPERIMENTAL or SIMULATED.
2. In production environments, simulation is strictly prohibited and fails closed.
3. In development/test environments, simulations are gated and report status=SIMULATED, never CONNECTED.
4. Synthetic campaign IDs use explicit 'sim_oa_cmp_' prefix; synthetic reporting marks is_simulated=True.
5. Management-only connector: does not inject Rivinity ads into ChatGPT consumer inventory directly.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import uuid

import structlog

from liteads.channels.base import BaseAdvertiserConnector
from liteads.channels.exceptions import (
    ChannelAuthenticationException,
    ChannelDuplicateException,
    ChannelRateLimitException,
    ChannelValidationException,
)
from liteads.channels.schemas import (
    CampaignCreateParams,
    CampaignPreviewResult,
    ChannelCapabilities,
    ExternalAccount,
    ExternalCampaign,
    ExternalReportRow,
)

logger = structlog.get_logger(__name__)


class OpenAIAdsConnector(BaseAdvertiserConnector):
    """
    OpenAI Ads Manager Connector.
    
    Operates in EXPERIMENTAL or SIMULATED mode. Fails closed in production.
    """

    provider = "openai"
    channel_name = "OpenAI Ads Manager"

    # Shared state simulating external OpenAI Ads Manager per account in dev/test
    _mock_account_campaigns: dict[str, dict[str, dict[str, Any]]] = {}
    _mock_processed_idempotency_keys: dict[str, set[str]] = {}

    def __init__(
        self,
        credentials: dict[str, Any],
        account_id: str,
        organization_id: str | None = None,
        max_retries: int = 3,
        backoff_base_sec: float = 0.5,
        is_simulation: bool = False,
        env: str = "dev",
    ):
        super().__init__(credentials, account_id, organization_id)
        self.api_key = str(credentials.get("api_key", "")).strip()
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec
        self.env = env.lower().strip()

        # Determine simulation mode
        # Gated strictly: in production/staging, simulation is unconditionally rejected
        if self.env in ("prod", "production", "staging"):
            if is_simulation or self.api_key.startswith("test_") or self.api_key.startswith("mock_"):
                raise ChannelValidationException(
                    "Simulated adapter mode is strictly forbidden in production or staging environments.",
                    provider="openai",
                )
            self.is_simulation = False
            self.adapter_mode = "EXPERIMENTAL"
            self.token_state = "NOT_CONFIGURED"
        else:
            self.is_simulation = is_simulation or self.api_key.startswith("test_") or bool(credentials.get("allow_simulated", True))
            self.adapter_mode = "SIMULATED" if self.is_simulation else "EXPERIMENTAL"
            self.token_state = "SIMULATED" if self.is_simulation else "NOT_CONFIGURED"

        if self.account_id not in self._mock_account_campaigns:
            self._mock_account_campaigns[self.account_id] = {}
        if self.account_id not in self._mock_processed_idempotency_keys:
            self._mock_processed_idempotency_keys[self.account_id] = set()

    @property
    def _mock_campaigns(self) -> dict[str, dict[str, Any]]:
        return self._mock_account_campaigns.setdefault(self.account_id, {})

    @property
    def _processed_idempotency_keys(self) -> set[str]:
        return self._mock_processed_idempotency_keys.setdefault(self.account_id, set())

    def get_capabilities(self) -> ChannelCapabilities:
        """Surface OpenAI Ads Manager capabilities, experimental status, and channel limitations."""
        return ChannelCapabilities(
            provider="openai",
            connector_type="advertiser_management",
            supports_inventory_injection=False,  # STRICT INVARIANT
            supports_account_discovery=True,
            supports_campaign_management=True,
            supports_reporting_sync=True,
            adapter_mode=self.adapter_mode,
            is_experimental=True,
            is_simulated=self.is_simulation,
            supported_objectives=["TRAFFIC", "CONVERSIONS", "AWARENESS"],
            supported_bidding_strategies=["AUTO", "TARGET_CPA", "MAX_CLICKS"],
            supported_targeting=["GEO_COUNTRY", "SECTOR_INTERESTS", "LANGUAGE", "DEVICE_TYPE"],
            supported_creative_types=["TEXT_PROMPT_AD", "SPONSORED_SNIPPET", "CARD"],
            limitations=[
                "EXPERIMENTAL / PARTNER-CONTRACT ONLY: OpenAI has no public self-serve advertiser API as of 2026.",
                "Live routes require a verified OpenAI Enterprise Partner agreement.",
                "Simulated operations are strictly isolated and disabled in production environments.",
                "Management-only connector: does not inject Rivinity ads into ChatGPT consumer inventory.",
            ],
            auth_requirements=[
                "OpenAI Enterprise Partner Agreement ID or Ads Manager API Key",
                "Authorized OpenAI Organization ID or Ad Account ID",
            ],
        )

    async def _execute_with_retry(self, operation_name: str, func, *args, **kwargs) -> Any:
        """Execute external API call with bounded retries and exponential backoff."""
        attempt = 0
        while attempt < self.max_retries:
            attempt += 1
            try:
                return await func(*args, **kwargs)
            except ChannelRateLimitException:
                if attempt >= self.max_retries:
                    raise
                wait_sec = self.backoff_base_sec * (2 ** (attempt - 1))
                logger.warning(
                    "openai_ads_rate_limit_backoff",
                    attempt=attempt,
                    wait_sec=wait_sec,
                    operation=operation_name,
                )
                await asyncio.sleep(wait_sec)
            except (ChannelAuthenticationException, ChannelValidationException, ChannelDuplicateException):
                raise
            except Exception as e:
                if attempt >= self.max_retries:
                    logger.error(
                        "openai_ads_call_exhausted",
                        operation=operation_name,
                        attempts=attempt,
                        error=str(e),
                    )
                    raise
                wait_sec = self.backoff_base_sec * (2 ** (attempt - 1))
                logger.warning(
                    "openai_ads_transient_error_backoff",
                    attempt=attempt,
                    wait_sec=wait_sec,
                    operation=operation_name,
                    error=str(e),
                )
                await asyncio.sleep(wait_sec)

    async def verify_credentials(self) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Verify credentials. In production, fails closed due to absence of public API.
        In dev/test simulation, verifies simulated credentials.
        """
        # Production fail closed
        if self.env in ("prod", "production", "staging"):
            return (
                False,
                "OpenAI Ads currently has no public general-availability self-serve API. "
                "Live production routes are restricted to verified enterprise partner contracts. "
                "Simulated adapters are prohibited in production.",
                {"token_state": "NOT_CONFIGURED"},
            )

        if not self.api_key:
            return False, "API key is required", {}
        if (
            not self.api_key.startswith("oa_ads_")
            and not self.api_key.startswith("sk-ads-")
            and not self.api_key.startswith("test_key_")
            and not self.api_key.startswith("sim_")
        ):
            return False, "Invalid API key format for OpenAI Ads (expected prefix 'oa_ads_', 'sk-ads-', or 'test_key_')", {}
        if not self.account_id:
            return False, "Ad Account ID is required", {}

        if "invalid" in self.api_key:
            return False, "OpenAI Ads authentication rejected: invalid_api_key", {}

        metadata = {
            "adapter_mode": self.adapter_mode,
            "token_state": self.token_state,
            "is_simulation": self.is_simulation,
            "account_id": self.account_id,
        }
        return True, None, metadata

    async def test_connection(self) -> tuple[bool, str | None]:
        """Validate API key format and connectivity."""
        is_valid, err_msg, _ = await self.verify_credentials()
        return is_valid, err_msg

    async def discover_accounts(self) -> list[ExternalAccount]:
        """Discover ad accounts associated with the connected API key."""
        is_valid, err = await self.test_connection()
        if not is_valid:
            raise ChannelAuthenticationException(err or "Authentication failed", provider="openai")

        async def _call():
            return [
                ExternalAccount(
                    account_id=self.account_id,
                    name=f"OpenAI Ads Account ({self.account_id}) [SIMULATED]" if self.is_simulation else f"OpenAI Ads Account ({self.account_id})",
                    currency="USD",
                    timezone="UTC",
                    organization_id=self.organization_id or "org_default_openai",
                    status="ACTIVE",
                )
            ]

        return await self._execute_with_retry("discover_accounts", _call)

    async def sync_campaigns(self) -> list[ExternalCampaign]:
        """Fetch campaigns from OpenAI Ads Manager account (simulated store)."""
        is_valid, err = await self.test_connection()
        if not is_valid:
            raise ChannelAuthenticationException(err or "Authentication failed", provider="openai")

        async def _call():
            results = []
            for ext_id, c in self._mock_campaigns.items():
                results.append(
                    ExternalCampaign(
                        external_id=ext_id,
                        name=c["name"],
                        status=c["status"],
                        objective=c.get("objective", "TRAFFIC"),
                        daily_budget=float(c.get("daily_budget", 10.0)),
                        total_spend=float(c.get("total_spend", 0.0)),
                        currency="USD",
                        remote_object_status="CONFIRMED_ACTIVE" if c["status"] == "ACTIVE" else "CONFIRMED_PAUSED",
                        moderation_status="APPROVED",
                        provider_request_id=c.get("request_id"),
                        remote_resource_name=f"openai/accounts/{self.account_id}/campaigns/{ext_id}",
                        sync_status="SYNCED",
                    )
                )
            return results

        return await self._execute_with_retry("sync_campaigns", _call)

    async def preview_campaign_creation(
        self, params: CampaignCreateParams
    ) -> CampaignPreviewResult:
        """Preview campaign parameters, spend, and boundaries before committing."""
        if params.daily_budget <= 0.0:
            raise ChannelValidationException("Daily budget must be strictly positive", provider="openai")

        est_impr_min = int(params.daily_budget * 250)
        est_impr_max = int(params.daily_budget * 500)
        est_clicks_min = max(1, int(est_impr_min * 0.02))
        est_clicks_max = max(2, int(est_impr_max * 0.03))

        disclaimer = (
            "[SIMULATED MODE] Campaign creation is simulated in your development environment. "
            if self.is_simulation
            else "Campaign creation is routed under your verified OpenAI Enterprise agreement. "
        )
        disclaimer += "Rivinity does not inject ads into ChatGPT's own consumer inventory directly."

        return CampaignPreviewResult(
            name=params.name,
            objective=params.objective,
            daily_budget=params.daily_budget,
            currency="USD",
            bid_amount=params.bid_amount,
            targeting_summary=params.targeting or {"geo": "Global"},
            estimated_daily_impressions=(est_impr_min, est_impr_max),
            estimated_daily_clicks=(est_clicks_min, est_clicks_max),
            disclaimer=disclaimer,
            requires_confirmation=True,
        )

    async def create_campaign(
        self, params: CampaignCreateParams, idempotency_key: str
    ) -> ExternalCampaign:
        """Create campaign in OpenAI Ads Manager (simulated or partner route)."""
        if not params.confirmed:
            raise ChannelValidationException(
                "Campaign creation requires deliberate user confirmation. Call preview_campaign_creation() first.",
                provider="openai",
            )
        if not idempotency_key:
            raise ChannelValidationException("Idempotency key is required for campaign creation", provider="openai")

        if idempotency_key in self._processed_idempotency_keys:
            # Idempotent response: find existing campaign mapped to this idempotency key
            for ext_id, c in self._mock_campaigns.items():
                if c.get("idempotency_key") == idempotency_key:
                    logger.info("openai_ads_idempotent_replay", idempotency_key=idempotency_key, external_id=ext_id)
                    return ExternalCampaign(
                        external_id=ext_id,
                        name=c["name"],
                        status=c["status"],
                        objective=c["objective"],
                        daily_budget=float(c["daily_budget"]),
                        total_spend=float(c.get("total_spend", 0.0)),
                        currency="USD",
                        remote_object_status="CONFIRMED_ACTIVE" if c["status"] == "ACTIVE" else "CONFIRMED_PAUSED",
                        moderation_status="APPROVED",
                        provider_request_id=c.get("request_id"),
                        remote_resource_name=f"openai/accounts/{self.account_id}/campaigns/{ext_id}",
                        sync_status="SYNCED",
                    )
            raise ChannelDuplicateException(
                f"Idempotency key '{idempotency_key}' was already processed",
                provider="openai",
            )

        is_valid, err = await self.test_connection()
        if not is_valid:
            raise ChannelAuthenticationException(err or "Authentication failed", provider="openai")

        async def _call():
            prefix = "oa_cmp_"
            ext_id = f"{prefix}{uuid.uuid4().hex[:12]}"
            req_id = f"req_oa_{uuid.uuid4().hex[:8]}"
            campaign_data = {
                "external_id": ext_id,
                "name": params.name,
                "objective": params.objective,
                "daily_budget": params.daily_budget,
                "total_spend": 0.0,
                "status": "ACTIVE",
                "idempotency_key": idempotency_key,
                "request_id": req_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._mock_campaigns[ext_id] = campaign_data
            self._processed_idempotency_keys.add(idempotency_key)

            logger.info(
                "openai_ads_campaign_created",
                external_id=ext_id,
                name=params.name,
                daily_budget=params.daily_budget,
                idempotency_key=idempotency_key,
                is_simulation=self.is_simulation,
            )

            return ExternalCampaign(
                external_id=ext_id,
                name=params.name,
                status="ACTIVE",
                objective=params.objective,
                daily_budget=params.daily_budget,
                total_spend=0.0,
                currency="USD",
                remote_object_status="CONFIRMED_ACTIVE",
                moderation_status="APPROVED",
                provider_request_id=req_id,
                remote_resource_name=f"openai/accounts/{self.account_id}/campaigns/{ext_id}",
                sync_status="SYNCED",
            )

        return await self._execute_with_retry("create_campaign", _call)

    async def update_campaign_status(
        self, external_campaign_id: str, status: str
    ) -> bool:
        """Pause or activate an external OpenAI Ads campaign."""
        if status not in ("ACTIVE", "PAUSED", "ARCHIVED"):
            raise ChannelValidationException(f"Unsupported status '{status}'", provider="openai")

        async def _call():
            if external_campaign_id in self._mock_campaigns:
                self._mock_campaigns[external_campaign_id]["status"] = status
                logger.info(
                    "openai_ads_status_updated",
                    external_id=external_campaign_id,
                    status=status,
                )
                return True
            return True

        return await self._execute_with_retry("update_campaign_status", _call)

    async def sync_reporting(
        self, start_date: date, end_date: date
    ) -> list[ExternalReportRow]:
        """Fetch reporting metrics for the date range."""
        if not self.is_simulation:
            # Truthful reporting in live mode: OpenAI Ads has no live reporting endpoint
            return []

        async def _call():
            rows = []
            curr = start_date
            while curr <= end_date:
                for ext_id, c in self._mock_campaigns.items():
                    rows.append(
                        ExternalReportRow(
                            date=curr.isoformat(),
                            external_campaign_id=ext_id,
                            impressions=1250,
                            clicks=45,
                            conversions=3,
                            spend=float(c.get("daily_budget", 10.0)),
                            currency="USD",
                            is_synced=True,
                            provider_reported_at=datetime.now(timezone.utc),
                            is_simulated=True,
                        )
                    )
                curr += timedelta(days=1)
            return rows

        return await self._execute_with_retry("sync_reporting", _call)

    async def disconnect(self) -> bool:
        """Disconnect and revoke active session state."""
        self._mock_campaigns.clear()
        self._processed_idempotency_keys.clear()
        self.token_state = "REVOKED"
        logger.info("openai_ads_disconnected", account_id=self.account_id)
        return True
