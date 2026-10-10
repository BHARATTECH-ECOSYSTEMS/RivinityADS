from dataclasses import dataclass
from typing import Literal

from liteads.models.base import ModerationStatus
from liteads.common.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ModerationDecision:
    status: ModerationStatus
    reason: str | None = None


class CreativeModerationService:
    """
    Service for validating and moderating creatives.
    
    Since Laya currently only supports campaign optimization decisions,
    this service validates the creative and defaults to REQUIRES_REVIEW 
    (PENDING_REVIEW) for manual admin review.
    """
    
    @staticmethod
    async def moderate_creative(title: str, description: str | None, image_url: str | None, video_url: str | None, landing_url: str) -> ModerationDecision:
        """
        Validates the creative payload and returns a moderation decision.
        """
        # Basic content validation - reject empty titles or overly long strings
        if not title or len(title) > 255:
            return ModerationDecision(status=ModerationStatus.REJECTED, reason="Invalid title length")
            
        # Reject if landing URL is missing
        if not landing_url:
            return ModerationDecision(status=ModerationStatus.REJECTED, reason="Landing URL is required")
            
        # Without an automated AI provider configured for image/video content safety,
        # we default to requiring human review.
        return ModerationDecision(status=ModerationStatus.PENDING_REVIEW)
