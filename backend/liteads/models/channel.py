"""
Channel adapter and external ad platform integration models (Phase 5).

Supports:
1. ChannelConnection: Tenant-scoped external ad account connection (OpenAI Ads, Google Ads).
   Credentials are stored server-side encrypted and secrets are never returned to clients.
2. ExternalCampaignMapping: Deterministic mapping between Rivinity campaigns and external provider campaigns.
   Enforces idempotency keys on writes to prevent duplicate creation on retries.
3. PublisherInventoryContract: Explicit signed agreements for verified AI publisher inventory delivery.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin


class ChannelConnection(Base, TimestampMixin):
    """
    Tenant-scoped external ad platform connection (Phase 5).
    
    Stores encrypted credentials for managing authorized external accounts
    (e.g., OpenAI Ads Manager, Google Ads).
    """

    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_id", "provider", "external_account_id",
            name="uq_advertiser_channel_account",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_type: Mapped[str] = mapped_column(
        String(32), default="advertiser", server_default="advertiser", nullable=False
    )
    advertiser_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    publisher_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Encrypted credentials (server-side authenticated encryption)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    # Status: CONNECTED, SYNCING, ERROR, DISCONNECTED, REVOKED
    status: Mapped[str] = mapped_column(
        String(32), default="CONNECTED", server_default="CONNECTED", nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Capabilities & limitations surfaced by the provider
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    advertiser = relationship("Advertiser", lazy="selectin")
    publisher = relationship("Publisher", lazy="selectin")
    campaign_mappings: Mapped[list["ExternalCampaignMapping"]] = relationship(
        "ExternalCampaignMapping", back_populates="connection", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_connected(self) -> bool:
        return self.status in ("CONNECTED", "SYNCING")


class ExternalCampaignMapping(Base, TimestampMixin):
    """
    Mapping between internal Rivinity campaigns and external ad platform campaigns.
    
    Enforces idempotency keys to guarantee retries cannot duplicate campaigns.
    """

    __tablename__ = "external_campaign_mappings"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_campaign_id",
            name="uq_connection_external_campaign",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channel_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    advertiser_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    local_campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_campaign_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_status: Mapped[str] = mapped_column(String(64), nullable=False)  # ACTIVE, PAUSED, ARCHIVED, DRAFT
    objective: Mapped[str | None] = mapped_column(String(64), nullable=True)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    total_spend: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    last_sync_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False
    )

    # Relationships
    connection = relationship("ChannelConnection", back_populates="campaign_mappings")
    local_campaign = relationship("Campaign", lazy="selectin")


class PublisherInventoryContract(Base, TimestampMixin):
    """
    Explicit signed agreement for AI publisher inventory integration.
    
    Guarantees no unapproved third-party inventory delivery can take place.
    """

    __tablename__ = "publisher_inventory_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_agreement_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    agreement_status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", server_default="ACTIVE", nullable=False
    )  # ACTIVE, EXPIRED, TERMINATED, PENDING_LEGAL
    approved_surfaces: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    policy_terms_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_signed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    contract_valid_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    publisher = relationship("Publisher", lazy="selectin")

    @property
    def is_valid(self) -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.agreement_status == "ACTIVE" and self.contract_valid_until > now

