"""
Database models for LiteAds.
"""

from liteads.models.ad import (
    Advertiser,
    AdvertiserApiKey,
    AdEvent,
    AdSet,
    Campaign,
    CampaignAssignment,
    Creative,
    HourlyStat,
    TargetingRule,
    Suggestion,
    CampaignChangeLog,
    OptimizationDecision,
)
from liteads.models.geo import GeoLocation
from liteads.models.publisher import AdSlot, Publisher, PublisherApiKey, PublisherDailyStat, PublisherProperty, ApiKeyDailyStat
from liteads.models.auth import AuditLog, OneTimeToken, RefreshToken, User
from liteads.models.finance import Transaction, PlatformSettings, PaymentRequest, PublisherPayout, BillingDispute
from liteads.models.webhook import Webhook, WebhookDelivery
from liteads.models.domain import AdvertiserDomain, VerificationStatus, VerificationMethod
from liteads.models.notification import (
    Notification,
    NotificationPreference,
    NotificationDelivery,
    NotificationChannel,
    NotificationSeverity,
    NotificationCategory,
    NotificationType,
)
from liteads.models.consent import (
    MarketingConsent,
    ConsentCategory,
    ConsentStatus,
    ConsentSource,
)
from liteads.models.email_marketing import (
    EmailMarketingList,
    EmailMarketingContact,
    EmailMarketingCampaign,
)
from liteads.models.channel import (
    ChannelConnection,
    ExternalCampaignMapping,
    PublisherInventoryContract,
)
from liteads.models.website import (
    WebsiteProject,
    WebsitePage,
    WebsiteVersion,
    WebsiteContactSubmission,
    WebsiteConsentLog,
    WebsiteStatus,
    WebsiteMonetizationStatus,
    WebsiteTemplate,
)
from liteads.models.base import (
    Base,
    BidType,
    CreativeType,
    EventType,
    Status,
    ModerationStatus,
    TransactionType,
    TimestampMixin,
)

__all__ = [
    # Base
    "Base",
    "TimestampMixin",
    # Enums
    "Status",
    "ModerationStatus",
    "BidType",
    "CreativeType",
    "EventType",
    "TransactionType",
    "WebsiteStatus",
    "WebsiteMonetizationStatus",
    # Channel adapters
    "ChannelConnection",
    "ExternalCampaignMapping",
    "PublisherInventoryContract",
    # Models
    "WebsiteProject",
    "WebsitePage",
    "WebsiteVersion",
    "WebsiteContactSubmission",
    "WebsiteConsentLog",
    "WebsiteTemplate",
    # Models
    "Advertiser",
    "AdvertiserApiKey",
    "AdEvent",
    "AdSet",
    "Campaign",
    "CampaignAssignment",
    "Creative",
    "TargetingRule",
    "HourlyStat",
    "Publisher",
    "AdSlot",
    "PublisherDailyStat",
    "PublisherProperty",
    "PublisherApiKey",
    "ApiKeyDailyStat",
    "User",
    "RefreshToken",
    "OneTimeToken",
    "AuditLog",
    "GeoLocation",
    "Transaction",
    "PlatformSettings",
    "PaymentRequest",
    "PublisherPayout",
    "BillingDispute",
    "Webhook",
    "WebhookDelivery",
    "Suggestion",
    "CampaignChangeLog",
    "OptimizationDecision",
    "AdvertiserDomain",
    "VerificationStatus",
    "VerificationMethod",
    "Notification",
    "NotificationPreference",
    "NotificationDelivery",
    "NotificationChannel",
    "NotificationSeverity",
    "NotificationCategory",
    "NotificationType",
    "MarketingConsent",
    "ConsentCategory",
    "ConsentStatus",
    "ConsentSource",
    "EmailMarketingList",
    "EmailMarketingContact",
    "EmailMarketingCampaign",
]
