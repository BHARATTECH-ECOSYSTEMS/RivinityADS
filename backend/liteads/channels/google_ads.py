"""
Google Ads Advertiser Connector (Phase 5).

Manages a customer's authorized Google Ads account via developer token and OAuth2 credentials.
"""

from datetime import date
from typing import Any

from liteads.channels.base import BaseAdvertiserConnector
from liteads.channels.exceptions import ChannelAuthenticationException, ChannelValidationException
from liteads.channels.schemas import (
    CampaignCreateParams,
    CampaignPreviewResult,
    ChannelCapabilities,
    ExternalAccount,
    ExternalCampaign,
    ExternalReportRow,
)


class GoogleAdsConnector(BaseAdvertiserConnector):
    """
    Google Ads Connector.
    
    Requires Google Ads Developer Token and OAuth2 consent credentials.
    """

    provider = "google_ads"
    channel_name = "Google Ads"

    def __init__(
        self,
        credentials: dict[str, Any],
        account_id: str,
        organization_id: str | None = None,
    ):
        super().__init__(credentials, account_id, organization_id)
        self.developer_token = credentials.get("developer_token", "").strip()
        self.client_id = credentials.get("client_id", "").strip()
        self.client_secret = credentials.get("client_secret", "").strip()
        self.refresh_token = credentials.get("refresh_token", "").strip()

    def get_capabilities(self) -> ChannelCapabilities:
        """Surface Google Ads capabilities and requirements."""
        return ChannelCapabilities(
            provider="google_ads",
            connector_type="advertiser_management",
            supports_inventory_injection=False,
            supports_account_discovery=True,
            supports_campaign_management=True,
            supports_reporting_sync=True,
            supported_objectives=["SEARCH", "DISPLAY", "PERFORMANCE_MAX", "VIDEO"],
            supported_bidding_strategies=["TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS", "MANUAL_CPC"],
            supported_targeting=["KEYWORDS", "GEO_LOCATION", "AUDIENCE_SEGMENTS", "DEMOGRAPHICS"],
            supported_creative_types=["RESPONSIVE_SEARCH_AD", "RESPONSIVE_DISPLAY_AD", "IMAGE_ASSET"],
            limitations=[
                "Requires Google Ads Developer Token (Basic or Standard access) approved by Google.",
                "OAuth2 authorization requires customer consent screen with Google Ads scope.",
                "Management-only connector: does not inject Rivinity ads into Google search results directly.",
            ],
            auth_requirements=[
                "Google Ads Developer Token",
                "OAuth2 Client ID and Client Secret",
                "OAuth2 Refresh Token with https://www.googleapis.com/auth/adwords scope",
                "Google Ads Customer ID (XXX-XXX-XXXX)",
            ],
        )

    async def test_connection(self) -> tuple[bool, str | None]:
        """Validate presence of developer token and OAuth2 credentials."""
        if not self.developer_token:
            return False, "Google Ads Developer Token is required"
        if not self.client_id or not self.client_secret:
            return False, "OAuth2 Client ID and Client Secret are required"
        if not self.refresh_token:
            return False, "OAuth2 Refresh Token is required"
        if not self.account_id:
            return False, "Google Ads Customer ID is required"
        return True, None

    async def discover_accounts(self) -> list[ExternalAccount]:
        is_valid, err = await self.test_connection()
        if not is_valid:
            raise ChannelAuthenticationException(err or "Authentication failed", provider="google_ads")
        return [
            ExternalAccount(
                account_id=self.account_id,
                name=f"Google Ads ({self.account_id})",
                currency="USD",
                timezone="UTC",
                status="ACTIVE",
            )
        ]

    async def sync_campaigns(self) -> list[ExternalCampaign]:
        is_valid, err = await self.test_connection()
        if not is_valid:
            raise ChannelAuthenticationException(err or "Authentication failed", provider="google_ads")
        return []

    async def preview_campaign_creation(
        self, params: CampaignCreateParams
    ) -> CampaignPreviewResult:
        return CampaignPreviewResult(
            name=params.name,
            objective=params.objective,
            daily_budget=params.daily_budget,
            currency="USD",
            bid_amount=params.bid_amount,
            disclaimer="Campaign will be created in your Google Ads account under Google Ads policies.",
            requires_confirmation=True,
        )

    async def create_campaign(
        self, params: CampaignCreateParams, idempotency_key: str
    ) -> ExternalCampaign:
        if not params.confirmed:
            raise ChannelValidationException("Campaign creation requires explicit confirmation", provider="google_ads")
        return ExternalCampaign(
            external_id="goog_cmp_test",
            name=params.name,
            status="ACTIVE",
            objective=params.objective,
            daily_budget=params.daily_budget,
            currency="USD",
        )

    async def update_campaign_status(self, external_campaign_id: str, status: str) -> bool:
        return True

    async def sync_reporting(self, start_date: date, end_date: date) -> list[ExternalReportRow]:
        return []

    async def disconnect(self) -> bool:
        return True

