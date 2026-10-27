"""
Pydantic schemas for API requests and responses.
"""

from liteads.schemas.internal import (
    AdCandidate,
    BudgetInfo,
    FeatureVector,
    FrequencyInfo,
    PredictionResult,
    UserContext,
)
from liteads.schemas.request import (
    AdRequest,
    ContextInfo,
    DeviceInfo,
    EventRequest,
    GeoInfo,
    UserContextRequest,
    UserFeatures,
)
from liteads.schemas.response import (
    AdListResponse,
    AdResponse,
    CreativeResponse,
    ErrorResponse,
    EventResponse,
    HealthResponse,
    TrackingUrls,
)
from liteads.schemas.notification import (
    MarkAllReadResponse,
    NotificationListResponse,
    NotificationPreferenceItem,
    NotificationPreferenceListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    NotificationResponse,
    UnreadCountResponse,
)
from liteads.schemas.targeting import (
    GeoEntry,
    TargetingResponse,
    TargetingSet,
)

__all__ = [
    # Request schemas
    "AdRequest",
    "EventRequest",
    "DeviceInfo",
    "GeoInfo",
    "UserContextRequest",
    "ContextInfo",
    "UserFeatures",
    # Response schemas
    "AdResponse",
    "AdListResponse",
    "EventResponse",
    "HealthResponse",
    "ErrorResponse",
    "CreativeResponse",
    "TrackingUrls",
    # Notification schemas
    "NotificationResponse",
    "NotificationListResponse",
    "UnreadCountResponse",
    "MarkAllReadResponse",
    "NotificationPreferenceItem",
    "NotificationPreferenceResponse",
    "NotificationPreferenceListResponse",
    "NotificationPreferenceUpdateRequest",
    # Targeting schemas
    "GeoEntry",
    "TargetingSet",
    "TargetingResponse",
    # Internal schemas
    "AdCandidate",
    "UserContext",
    "FeatureVector",
    "PredictionResult",
    "FrequencyInfo",
    "BudgetInfo",
]
