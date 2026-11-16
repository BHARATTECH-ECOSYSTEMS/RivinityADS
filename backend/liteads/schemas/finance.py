"""Financial API schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class TransactionResponse(BaseModel):
    """Ledger transaction response."""

    id: int
    type: int
    amount: Decimal
    currency: str
    advertiser_id: int | None = None
    publisher_id: int | None = None
    campaign_id: int | None = None
    reference_id: str | None = None
    provider_event_id: str | None = None
    rev_share_rate: Decimal | None = None
    hold_until: datetime | None = None
    is_cleared: bool = True
    description: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LedgerResponse(BaseModel):
    """Ledger overview and transactions."""

    balance: Decimal
    transactions: list[TransactionResponse]


class DepositRequest(BaseModel):
    """Request to deposit funds."""

    amount: Decimal = Field(..., gt=0, description="Amount to deposit")
    currency: str = Field(default="INR", description="ISO 3-letter currency code")
    reference_id: str | None = Field(None, description="External payment reference")


class PayoutRequest(BaseModel):
    """Request a payout."""

    amount: Decimal = Field(..., gt=0, description="Amount to payout")
    currency: str = Field(default="INR", description="ISO 3-letter currency code")
    reference_id: str | None = Field(None, description="External payout reference")


class PayoutRecordResponse(BaseModel):
    """Publisher payout record and lifecycle state."""

    id: int
    publisher_id: int
    amount: Decimal
    currency: str
    status: str
    reference_id: str
    provider_payout_id: str | None = None
    notes: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PayoutRejectRequest(BaseModel):
    """Request to reject a payout and restore reserved funds."""

    reason: str = Field(default="Payout rejected by admin", description="Rejection reason")


class PayoutConfirmRequest(BaseModel):
    """Request to confirm external payout execution."""

    provider_payout_id: str = Field(..., min_length=1, description="External transfer provider transaction ID")
    notes: str | None = Field(default=None, description="Confirmation notes")


class PaymentOrderRequest(BaseModel):
    """Request to create a payment order."""

    amount: Decimal = Field(..., ge=100, description="Amount to top up (minimum ₹100)")


class PaymentOrderResponse(BaseModel):
    """Response containing payment order details."""

    order_id: str
    amount: Decimal
    currency: str
    qr_image_url: str | None = None


class DisputeCreate(BaseModel):
    """Request to submit a billing dispute or IVT traffic appeal (Phase 3)."""

    dispute_type: str = Field(..., description="ADVERTISER_SPEND, PUBLISHER_TRAFFIC, or IVT_APPEAL")
    advertiser_id: int | None = None
    publisher_id: int | None = None
    campaign_id: int | None = None
    slot_id: str | None = None
    transaction_id: int | None = None
    amount_disputed: Decimal = Field(default=Decimal("0"), ge=0, description="Amount disputed")
    currency: str = Field(default="INR", max_length=3)
    reason: str = Field(..., min_length=5, max_length=255, description="Reason for dispute")
    evidence: dict[str, Any] | None = Field(default=None, description="Diagnostic logs, screenshots, or metrics")


class DisputeResponse(BaseModel):
    """Dispute / appeal representation."""

    id: int
    dispute_type: str
    user_id: int | None = None
    advertiser_id: int | None = None
    publisher_id: int | None = None
    campaign_id: int | None = None
    slot_id: str | None = None
    transaction_id: int | None = None
    amount_disputed: Decimal
    amount_credited: Decimal
    currency: str
    status: str
    reason: str
    evidence: dict[str, Any] | None = None
    resolution_notes: str | None = None
    resolved_by_id: int | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DisputeResolveRequest(BaseModel):
    """Admin resolution request for dispute."""

    status: str = Field(..., description="RESOLVED_REFUNDED, RESOLVED_CREDITED, or REJECTED")
    amount_credited: Decimal = Field(default=Decimal("0"), ge=0, description="Amount credited/refunded")
    resolution_notes: str = Field(..., min_length=5, description="Auditable explanation of decision")


class ReconciliationRequest(BaseModel):
    """Request parameters for reconciliation run."""

    start_date: date | None = None
    end_date: date | None = None
    advertiser_id: int | None = None
    publisher_id: int | None = None


class ReconciliationResponse(BaseModel):
    """Audited financial reconciliation report."""

    status: str
    start_date: date
    end_date: date
    total_event_spend: Decimal
    total_ledger_spend: Decimal
    spend_delta: Decimal
    total_ivt_events: int
    ivt_suppressed_spend: Decimal
    discrepancies: list[dict[str, Any]]
    unresolved_queue: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[str]


class PublisherEarningsBreakdownResponse(BaseModel):
    """Publisher earnings breakdown across lifecycle states."""

    publisher_id: int
    currency: str = "INR"
    estimated: Decimal
    under_review: Decimal
    finalised: Decimal
    adjusted: Decimal
    payable: Decimal
    paid: Decimal


class PublisherAdjustmentRequest(BaseModel):
    """Admin manual adjustment request for publisher earnings."""

    amount: Decimal = Field(..., gt=0, description="Positive adjustment amount to deduct")
    reason: str = Field(..., min_length=3, description="Auditable adjustment reason")
    reference_id: str | None = None
    campaign_id: int | None = None


