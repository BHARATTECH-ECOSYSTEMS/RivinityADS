"""Financial models and ledger."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func, text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin, TransactionType


class Transaction(Base):
    """Immutable ledger entry for all financial movements."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default=text("'INR'"), nullable=False
    )
    
    # Relationships to entities (nullable because some transactions don't apply to all)
    advertiser_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="SET NULL"), nullable=True
    )
    publisher_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    
    # E.g. Stripe charge ID, payout reference, or internal tracking string
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    advertiser: Mapped["Advertiser"] = relationship("Advertiser", lazy="selectin")
    publisher: Mapped["Publisher"] = relationship("Publisher", lazy="selectin")
    campaign: Mapped["Campaign"] = relationship("Campaign", lazy="selectin")


class PublisherPayout(Base, TimestampMixin):
    """Publisher payout record and lifecycle state machine (Phase 1).

    Statuses:
    - requested: Payout requested by publisher, funds held/reserved.
    - approved: Payout approved by admin, pending batch/provider processing.
    - processing: Transfer submitted to external payment provider.
    - paid: Transfer successfully confirmed by provider.
    - rejected: Payout rejected by admin; funds returned to balance.
    - failed: Provider transfer failed; funds returned to balance.
    - reversed: Paid transaction was clawed back/reversed.
    """

    __tablename__ = "publisher_payouts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_publisher_payouts_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default=text("'INR'"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="requested", server_default=text("'requested'"), nullable=False, index=True
    )
    reference_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    provider_payout_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    publisher: Mapped["Publisher"] = relationship("Publisher", lazy="selectin", backref="payout_records")


class PaymentRequest(Base):
    """Pending payment requests for verification against webhooks."""

    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    
    provider_order_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True, unique=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False) # e.g. pending, paid, failed

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    advertiser: Mapped["Advertiser"] = relationship("Advertiser", lazy="selectin")


class PlatformSettings(Base):
    """Global dynamic platform settings."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The percentage of spend that goes to the publisher (e.g. 0.70)
    publisher_rev_share: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.700"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingDispute(Base, TimestampMixin):
    """Manual review and appeal record for billing disputes and IVT traffic adjustments (Phase 3)."""

    __tablename__ = "billing_disputes"
    __table_args__ = (
        CheckConstraint("amount_disputed >= 0", name="ck_disputes_amount_disputed_nonneg"),
        CheckConstraint("amount_credited >= 0", name="ck_disputes_amount_credited_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispute_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    advertiser_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    publisher_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slot_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount_disputed: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), server_default=text("0"), default=Decimal("0"), nullable=False
    )
    amount_credited: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), server_default=text("0"), default=Decimal("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), server_default=text("'INR'"), default="INR", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'OPEN'"), default="OPEN", nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id], lazy="selectin")
    advertiser: Mapped["Advertiser | None"] = relationship("Advertiser", lazy="selectin")
    publisher: Mapped["Publisher | None"] = relationship("Publisher", lazy="selectin")
    campaign: Mapped["Campaign | None"] = relationship("Campaign", lazy="selectin")
    transaction: Mapped["Transaction | None"] = relationship("Transaction", lazy="selectin")
    resolved_by: Mapped["User | None"] = relationship("User", foreign_keys=[resolved_by_id], lazy="selectin")

