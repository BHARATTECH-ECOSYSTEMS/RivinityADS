"""
AI Publisher Inventory Delivery Adapter (Phase 5).

Delivers Rivinity-controlled inventory on platforms that have explicitly approved
and integrated Rivinity under a signed publisher agreement.

MANDATORY INVARIANTS:
1. Delivery is strictly contingent on an active, verified signed agreement.
2. Unapproved third-party inventory insertion is unconditionally prohibited.
3. Inventory delivery requests for unapproved surfaces or expired contracts are rejected.
"""

from datetime import datetime, timezone
from typing import Any

import structlog

from liteads.channels.base import BasePublisherConnector
from liteads.channels.exceptions import UnapprovedPublisherException
from liteads.channels.schemas import ChannelCapabilities

logger = structlog.get_logger(__name__)


class AIPublisherAdapter(BasePublisherConnector):
    """
    Adapter for delivering inventory on partner AI platforms with signed contracts.
    
    Lacks public self-serve open delivery; strictly EXPERIMENTAL / partner-contract-only.
    """

    provider = "ai_publisher"
    platform_name = "Rivinity Verified AI Publisher"
    adapter_mode = "EXPERIMENTAL"

    def __init__(self, publisher_id: int, contract: Any | None = None):
        super().__init__(publisher_id, contract)

    def get_capabilities(self) -> ChannelCapabilities:
        """Surface publisher delivery capabilities."""
        return ChannelCapabilities(
            provider="ai_publisher",
            connector_type="publisher_delivery",
            supports_inventory_injection=True,  # Approved publisher inventory
            supports_account_discovery=False,
            supports_campaign_management=False,
            supports_reporting_sync=True,
            supports_live_delivery=True,
            supports_oauth=False,
            supports_reporting=True,
            adapter_mode="EXPERIMENTAL",
            is_experimental=True,
            is_simulated=False,
            supported_objectives=["NATIVE_DELIVERY", "RECOMMENDATION"],
            supported_bidding_strategies=["FLOOR_PRICE", "FIRST_PRICE", "SECOND_PRICE"],
            supported_targeting=["CONTEXTUAL_SECTOR", "GEO_COARSE", "DEVICE_TYPE"],
            supported_creative_types=["NATIVE_CARD", "SPONSORED_RECOMMENDATION", "TEXT_SNIPPET"],
            limitations=[
                "EXPERIMENTAL / PARTNER-CONTRACT ONLY: Requires an active signed inventory agreement and legal contract ID.",
                "Delivery restricted strictly to pre-approved surfaces listed in the contract.",
                "Contract must be unexpired and policy-compliance verified.",
                "Empty delivery results indicate absence of matching supply; empty results must never be treated as an active connected supply source.",
            ],
            auth_requirements=[
                "Signed agreement ID (e.g. AGR-AI-2026-XXXX)",
                "Publisher API Key with 'inventory.deliver' capability",
            ],
        )

    async def verify_contract(self, surface_id: str) -> tuple[bool, str | None]:
        """
        Verify that a signed contract exists, is active, unexpired, and approves surface_id.
        """
        if not self.contract:
            return False, "No signed publisher agreement found for this inventory channel"

        status = getattr(self.contract, "agreement_status", "PENDING")
        if status != "ACTIVE":
            return False, f"Publisher contract is not active (current status: '{status}')"

        valid_until = getattr(self.contract, "contract_valid_until", None)
        if not valid_until:
            return False, "Publisher contract has no expiration date"

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if valid_until.tzinfo is not None:
            valid_until = valid_until.replace(tzinfo=None)

        if valid_until <= now:
            return False, f"Publisher contract expired on {valid_until.isoformat()}"

        approved_surfaces = getattr(self.contract, "approved_surfaces", []) or []
        if surface_id not in approved_surfaces:
            return False, (
                f"Surface '{surface_id}' is not in approved surfaces {approved_surfaces} "
                f"under contract '{self.contract.signed_agreement_id}'"
            )

        return True, None

    async def deliver_inventory(
        self, surface_id: str, user_context: Any, num_ads: int = 1
    ) -> list[Any]:
        """
        Deliver approved inventory.
        
        Enforces contract verification before allowing any candidate retrieval or rendering.
        """
        is_verified, reason = await self.verify_contract(surface_id)
        if not is_verified:
            logger.warning(
                "unapproved_inventory_insertion_blocked",
                publisher_id=self.publisher_id,
                surface_id=surface_id,
                reason=reason,
            )
            raise UnapprovedPublisherException(
                message=f"Unapproved inventory insertion rejected: {reason}",
                provider="ai_publisher",
                details={
                    "publisher_id": self.publisher_id,
                    "surface_id": surface_id,
                    "reason": reason,
                },
            )

        # Inventory is authorized under signed contract
        logger.info(
            "ai_publisher_inventory_delivery_authorized",
            publisher_id=self.publisher_id,
            surface_id=surface_id,
            agreement_id=getattr(self.contract, "signed_agreement_id", "unknown"),
        )
        return []

