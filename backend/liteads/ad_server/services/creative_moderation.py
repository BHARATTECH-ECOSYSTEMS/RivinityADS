"""
Creative Moderation Lifecycle & Content Quality Service (Phase 7).

Manages the complete creative review lifecycle:
- Categorized moderation (policy, content, destination, quality, copyright)
- Resubmission workflow for rejected creatives
- Content-equivalence checks preventing unauthorized retention of approval on substantive edits
- Full auditability of all moderation state transitions
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any

from liteads.models.base import ModerationStatus
from liteads.common.logger import get_logger

logger = get_logger(__name__)


class ModerationCategory(str, Enum):
    """Authoritative moderation rejection and review categories."""

    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNACCEPTABLE_CONTENT = "UNACCEPTABLE_CONTENT"
    MALICIOUS_DESTINATION = "MALICIOUS_DESTINATION"
    LOW_QUALITY = "LOW_QUALITY"
    MISMATCHED_LANDING = "MISMATCHED_LANDING"
    TECHNICAL_DEFECT = "TECHNICAL_DEFECT"
    COPYRIGHT_INFRINGEMENT = "COPYRIGHT_INFRINGEMENT"
    OTHER = "OTHER"


MODERATION_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    ModerationCategory.POLICY_VIOLATION.value: "Violates advertising policy or deceptive claims guidelines",
    ModerationCategory.UNACCEPTABLE_CONTENT.value: "Contains inappropriate, adult, or offensive content",
    ModerationCategory.MALICIOUS_DESTINATION.value: "Landing destination failed safety, malware, or phishing checks",
    ModerationCategory.LOW_QUALITY.value: "Low asset resolution, illegible text, or severe formatting issues",
    ModerationCategory.MISMATCHED_LANDING.value: "Destination page does not match creative copy or advertised brand",
    ModerationCategory.TECHNICAL_DEFECT.value: "Broken click-through URL, SSL configuration error, or unreachable destination",
    ModerationCategory.COPYRIGHT_INFRINGEMENT.value: "Unauthorized trademark or intellectual property use",
    ModerationCategory.OTHER.value: "Operator custom review finding",
}


@dataclass
class ModerationDecision:
    """Outcome of a moderation evaluation."""

    status: ModerationStatus
    category: str | None = None
    reason: str | None = None
    details: dict[str, Any] | None = None


def compute_creative_content_hash(
    title: str,
    description: str | None,
    image_url: str | None,
    video_url: str | None,
    landing_url: str,
) -> str:
    """
    Computes a canonical SHA-256 fingerprint of creative substantive content.
    Used for content-equivalence checks during creative updates.
    """
    parts = [
        (title or "").strip(),
        (description or "").strip(),
        (image_url or "").strip(),
        (video_url or "").strip(),
        (landing_url or "").strip(),
    ]
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CreativeModerationService:
    """
    Manages creative pre-flight checks, content equivalence, and lifecycle transitions.
    """

    @staticmethod
    def are_contents_equivalent(
        orig_title: str,
        orig_description: str | None,
        orig_image_url: str | None,
        orig_video_url: str | None,
        orig_landing_url: str,
        new_title: str,
        new_description: str | None,
        new_image_url: str | None,
        new_video_url: str | None,
        new_landing_url: str,
    ) -> bool:
        """
        Returns True if the substantive content of two creative states is identical.
        Whitespace differences are normalized.
        """
        hash1 = compute_creative_content_hash(
            orig_title, orig_description, orig_image_url, orig_video_url, orig_landing_url
        )
        hash2 = compute_creative_content_hash(
            new_title, new_description, new_image_url, new_video_url, new_landing_url
        )
        return hash1 == hash2

    @staticmethod
    async def moderate_creative(
        title: str,
        description: str | None,
        image_url: str | None,
        video_url: str | None,
        landing_url: str,
    ) -> ModerationDecision:
        """
        Validates creative payload and returns an initial moderation decision.
        """
        # Basic validation
        if not title or len(title.strip()) == 0:
            return ModerationDecision(
                status=ModerationStatus.REJECTED,
                category=ModerationCategory.LOW_QUALITY.value,
                reason="Title cannot be empty",
            )

        if len(title) > 255:
            return ModerationDecision(
                status=ModerationStatus.REJECTED,
                category=ModerationCategory.LOW_QUALITY.value,
                reason="Title exceeds maximum length of 255 characters",
            )

        if not landing_url or len(landing_url.strip()) == 0:
            return ModerationDecision(
                status=ModerationStatus.REJECTED,
                category=ModerationCategory.TECHNICAL_DEFECT.value,
                reason="Landing URL is required",
            )

        # Enforce HTTPS scheme on landing URL
        if not landing_url.strip().lower().startswith("https://"):
            return ModerationDecision(
                status=ModerationStatus.REJECTED,
                category=ModerationCategory.TECHNICAL_DEFECT.value,
                reason="Destination URL must use HTTPS",
            )

        # Standard creatives require human or automated moderation review
        return ModerationDecision(status=ModerationStatus.PENDING_REVIEW)
