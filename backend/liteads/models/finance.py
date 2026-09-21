"""Financial models and ledger."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TransactionType


class Transaction(Base):
    """Immutable ledger entry for all financial movements."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    
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
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    advertiser: Mapped["Advertiser"] = relationship("Advertiser", lazy="selectin")
    publisher: Mapped["Publisher"] = relationship("Publisher", lazy="selectin")
    campaign: Mapped["Campaign"] = relationship("Campaign", lazy="selectin")


class PlatformSettings(Base):
    """Global dynamic platform settings."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The percentage of spend that goes to the publisher (e.g. 0.70)
    publisher_rev_share: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.700"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
