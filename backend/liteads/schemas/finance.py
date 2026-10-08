"""Financial API schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


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
    reference_id: str | None = Field(None, description="External payment reference")


class PayoutRequest(BaseModel):
    """Request a payout."""

    amount: Decimal = Field(..., gt=0, description="Amount to payout")
    reference_id: str | None = Field(None, description="External payout reference")


class PaymentOrderRequest(BaseModel):
    """Request to create a payment order."""

    amount: Decimal = Field(..., ge=100, description="Amount to top up (minimum ₹100)")


class PaymentOrderResponse(BaseModel):
    """Response containing payment order details."""

    order_id: str
    amount: Decimal
    currency: str
