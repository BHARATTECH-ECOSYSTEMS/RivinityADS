"""
Provider-neutral connector base interfaces (Phase 5).

Strictly separates:
1. BaseAdvertiserConnector: Manages a customer's authorized external ad platform account.
2. BasePublisherConnector: Delivers Rivinity-controlled inventory on an explicitly approved AI platform.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from liteads.channels.schemas import (
    CampaignCreateParams,
    CampaignPreviewResult,
    ChannelCapabilities,
    ExternalAccount,
    ExternalCampaign,
    ExternalReportRow,
)


class BaseAdvertiserConnector(ABC):
    """
    Abstract connector for external advertiser ad accounts.
    
    Manages accounts, campaigns, status, and reporting on third-party ad platforms
    where the advertiser holds an authorized account (e.g., OpenAI Ads, Google Ads).
    
    Invariant: This connector is strictly for account management.
    It does NOT inject inventory into third-party surfaces.
    """

    provider: str
    channel_name: str

    def __init__(self, credentials: dict[str, Any], account_id: str, organization_id: str | None = None):
        self.credentials = credentials
        self.account_id = account_id
        self.organization_id = organization_id

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str | None]:
        """Verify authentication and API connectivity against provider."""
        pass

    @abstractmethod
    async def discover_accounts(self) -> list[ExternalAccount]:
        """List accessible ad accounts and workspaces."""
        pass

    @abstractmethod
    async def sync_campaigns(self) -> list[ExternalCampaign]:
        """Synchronize campaigns from the external provider."""
        pass

    @abstractmethod
    async def preview_campaign_creation(
        self, params: CampaignCreateParams
    ) -> CampaignPreviewResult:
        """Preview campaign parameters, spend, and warnings before committing writes."""
        pass

    @abstractmethod
    async def create_campaign(
        self, params: CampaignCreateParams, idempotency_key: str
    ) -> ExternalCampaign:
        """Create external campaign. Requires confirmed=True and idempotency_key."""
        pass

    @abstractmethod
    async def update_campaign_status(
        self, external_campaign_id: str, status: str
    ) -> bool:
        """Update campaign delivery status (ACTIVE, PAUSED, ARCHIVED)."""
        pass

    @abstractmethod
    async def sync_reporting(
        self, start_date: date, end_date: date
    ) -> list[ExternalReportRow]:
        """Fetch historical performance reporting metrics from provider."""
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """Revoke tokens or clean up external connection session."""
        pass

    @abstractmethod
    def get_capabilities(self) -> ChannelCapabilities:
        """Surface provider capabilities, objectives, and limitations."""
        pass


class BasePublisherConnector(ABC):
    """
    Abstract connector for delivering Rivinity-controlled inventory
    on an authorized external platform (Phase 5).
    
    Invariant: May ONLY be activated under an explicit signed publisher agreement.
    Unapproved third-party inventory insertion is strictly prohibited.
    """

    provider: str
    platform_name: str

    def __init__(self, publisher_id: int, contract: Any | None = None):
        self.publisher_id = publisher_id
        self.contract = contract

    @abstractmethod
    async def verify_contract(self, surface_id: str) -> tuple[bool, str | None]:
        """Verify that a signed, unexpired contract explicitly approves this inventory surface."""
        pass

    @abstractmethod
    async def deliver_inventory(
        self, surface_id: str, user_context: Any, num_ads: int = 1
    ) -> list[Any]:
        """Deliver approved ad inventory for a verified publisher surface."""
        pass

    @abstractmethod
    def get_capabilities(self) -> ChannelCapabilities:
        """Surface publisher delivery capabilities and policy boundaries."""
        pass

