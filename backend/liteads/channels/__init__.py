"""
Channel Adapters and Connectors (Phase 5).
"""

from liteads.channels.ai_publisher import AIPublisherAdapter
from liteads.channels.base import BaseAdvertiserConnector, BasePublisherConnector
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
from liteads.channels.service import ChannelService

__all__ = [
    "BaseAdvertiserConnector",
    "BasePublisherConnector",
    "OpenAIAdsConnector",
    "GoogleAdsConnector",
    "AIPublisherAdapter",
    "ClaudePolicyGuard",
    "ChannelService",
    "ChannelCapabilities",
    "ExternalAccount",
    "ExternalCampaign",
    "CampaignCreateParams",
    "CampaignPreviewResult",
    "ExternalReportRow",
    "ConnectAdvertiserRequest",
    "ConnectAdvertiserResponse",
    "ConnectionDetailsResponse",
    "PublisherContractCreateRequest",
    "PublisherContractResponse",
    "ChannelException",
    "UnsupportedChannelException",
    "UnapprovedPublisherException",
    "ChannelAuthenticationException",
    "ChannelRateLimitException",
    "ChannelValidationException",
    "ChannelDuplicateException",
]

