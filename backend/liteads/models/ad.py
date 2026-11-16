"""
Ad-related database models.

Defines: Advertiser, Campaign, AdSet, Creative, TargetingRule, HourlyStat
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from liteads.models.base import (
    Base,
    BidType,
    CreativeType,
    EventType,
    Status,
    ModerationStatus,
    TimestampMixin,
)


class Advertiser(Base, TimestampMixin):
    """Advertiser account."""

    __tablename__ = "advertisers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    daily_budget: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Relationships
    campaigns: Mapped[list["Campaign"]] = relationship(
        "Campaign", back_populates="advertiser", lazy="selectin"
    )


class Campaign(Base, TimestampMixin):
    """Advertising campaign."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("advertisers.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Budget
    budget_daily: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    budget_total: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    spent_today: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    spent_total: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))

    # Bidding
    bid_type: Mapped[int] = mapped_column(Integer, default=BidType.CPM)
    bid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))

    # Frequency cap
    freq_cap_daily: Mapped[int] = mapped_column(Integer, default=10)
    freq_cap_hourly: Mapped[int] = mapped_column(Integer, default=3)

    # Schedule
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Status
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE)

    # Optimization
    autopilot: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    optimization_fencing_token: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, server_default="0"
    )

    # Stats (cached)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    advertiser: Mapped["Advertiser"] = relationship(
        "Advertiser", back_populates="campaigns", lazy="selectin"
    )
    creatives: Mapped[list["Creative"]] = relationship(
        "Creative", back_populates="campaign", lazy="selectin"
    )
    targeting_rules: Mapped[list["TargetingRule"]] = relationship(
        "TargetingRule", back_populates="campaign", lazy="selectin"
    )
    assignments: Mapped[list["CampaignAssignment"]] = relationship(
        "CampaignAssignment", back_populates="campaign", lazy="selectin", cascade="all, delete-orphan"
    )
    ad_sets: Mapped[list["AdSet"]] = relationship(
        "AdSet", back_populates="campaign", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        """Return whether the campaign is currently eligible to serve."""
        reference_time = self.start_time or self.end_time
        now = datetime.now(reference_time.tzinfo) if reference_time else datetime.now()
        return (
            self.status == Status.ACTIVE
            and (self.start_time is None or self.start_time <= now)
            and (self.end_time is None or self.end_time >= now)
        )


class AdSet(Base, TimestampMixin):
    """
    Ad set belonging to a campaign (Phase 7A).

    Represents the grouping layer between Campaign and Ads (Creatives),
    controlling sub-budgets, bidding, schedules, frequency caps, and targeting.
    """

    __tablename__ = "ad_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE, nullable=False)

    # Sub-budget (sub-limit of campaign.budget_daily)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    # Bidding
    bid_type: Mapped[int] = mapped_column(Integer, default=BidType.CPM, nullable=False)
    bid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"), nullable=False)

    # Schedule
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Frequency cap
    freq_cap_daily: Mapped[int | None] = mapped_column(Integer, default=10, nullable=True)
    freq_cap_hourly: Mapped[int | None] = mapped_column(Integer, default=3, nullable=True)

    # Delivery & configuration
    delivery_mode: Mapped[str] = mapped_column(
        String(32), default="standard", server_default="standard", nullable=False
    )
    targeting_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="ad_sets")
    creatives: Mapped[list["Creative"]] = relationship(
        "Creative", back_populates="ad_set", lazy="selectin"
    )
    targeting_rules: Mapped[list["TargetingRule"]] = relationship(
        "TargetingRule", back_populates="ad_set", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        """Return whether the ad set is currently eligible to serve."""
        reference_time = self.start_time or self.end_time
        now = datetime.now(reference_time.tzinfo) if reference_time else datetime.now()
        return (
            self.status == Status.ACTIVE
            and (self.start_time is None or self.start_time <= now)
            and (self.end_time is None or self.end_time >= now)
        )


class Creative(Base, TimestampMixin):
    """Ad creative (banner, video, native, etc.)."""

    __tablename__ = "creatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=False
    )
    ad_set_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ad_sets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    landing_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Creative metadata
    creative_type: Mapped[int] = mapped_column(Integer, default=CreativeType.BANNER)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    format: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Status
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE)

    # Moderation
    moderation_status: Mapped[int] = mapped_column(Integer, default=ModerationStatus.PENDING_REVIEW, server_default="2")
    moderation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    moderated_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Quality score (0-100)
    quality_score: Mapped[int] = mapped_column(Integer, default=80)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="creatives")
    ad_set: Mapped["AdSet | None"] = relationship("AdSet", back_populates="creatives")

    @validates("ad_set")
    def validate_ad_set(self, key: str, ad_set: Any) -> Any:
        if ad_set is not None:
            if self.campaign_id is not None and ad_set.campaign_id is not None:
                if self.campaign_id != ad_set.campaign_id:
                    raise ValueError(
                        f"Creative campaign_id ({self.campaign_id}) does not match "
                        f"AdSet campaign_id ({ad_set.campaign_id})"
                    )
        return ad_set


class TargetingRule(Base, TimestampMixin):
    """Targeting rules for campaigns."""

    __tablename__ = "targeting_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=False
    )
    ad_set_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ad_sets.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Rule type: geo, device, age, gender, interest, app_category, time, etc.
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Rule value (JSON) - flexible format for different rule types
    # Examples:
    # - geo: {"countries": ["CN", "US"], "cities": ["shanghai"]}
    # - device: {"os": ["android", "ios"], "brands": ["Samsung"]}
    # - age: {"min": 18, "max": 35}
    # - interest: {"categories": ["gaming", "sports"]}
    rule_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Include (whitelist) or exclude (blacklist)
    is_include: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    campaign: Mapped["Campaign"] = relationship(
        "Campaign", back_populates="targeting_rules"
    )
    ad_set: Mapped["AdSet | None"] = relationship("AdSet", back_populates="targeting_rules")

    @validates("ad_set")
    def validate_ad_set(self, key: str, ad_set: Any) -> Any:
        if ad_set is not None:
            if self.campaign_id is not None and ad_set.campaign_id is not None:
                if self.campaign_id != ad_set.campaign_id:
                    raise ValueError(
                        f"TargetingRule campaign_id ({self.campaign_id}) does not match "
                        f"AdSet campaign_id ({ad_set.campaign_id})"
                    )
        return ad_set


class HourlyStat(Base):
    """Hourly statistics for campaigns."""

    __tablename__ = "hourly_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=False
    )
    stat_hour: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))

    # Calculated metrics
    ctr: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0"))
    cvr: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0"))


class AdEvent(Base, TimestampMixin):
    """Recorded impression, click, or conversion event."""

    __tablename__ = "ad_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    ad_set_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ad_sets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    creative_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("creatives.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[int] = mapped_column(Integer, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    cost: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )

    # Phase 11: Conversion Measurement fields
    conversion_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    currency: Mapped[str | None] = mapped_column(
        String(3), server_default="INR", default="INR", nullable=True
    )
    is_attributed: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False
    )
    attribution_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    attributed_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ad_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_order_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    is_test: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False, index=True
    )

    # Phase 3: Invalid Traffic (IVT) Measurement fields
    is_invalid: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False, index=True
    )
    invalid_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    invalid_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )

    # Self-referential relationship to attributed click/impression
    attributed_event: Mapped["AdEvent | None"] = relationship(
        "AdEvent", remote_side=[id], foreign_keys=[attributed_event_id], backref="conversions"
    )

    __table_args__ = (
        Index("ix_ad_events_attribution", "campaign_id", "is_attributed", "is_test"),
        Index("ix_ad_events_click_lookup", "request_id", "event_type"),
        Index("ix_ad_events_ivt", "is_invalid", "event_type"),
        Index(
            "uq_ad_events_order_dedup",
            "campaign_id",
            "external_order_id",
            unique=True,
            postgresql_where=text("external_order_id IS NOT NULL AND is_test = false"),
            sqlite_where=text("external_order_id IS NOT NULL AND is_test = 0"),
        ),
    )



class CampaignAssignment(Base, TimestampMixin):
    """Explicit assignment of a campaign to a publisher and optionally a specific ad slot."""

    __tablename__ = "campaign_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("ad_slots.slot_id", ondelete="CASCADE"), nullable=True
    )

    campaign: Mapped["Campaign"] = relationship(
        "Campaign", back_populates="assignments"
    )


class Suggestion(Base, TimestampMixin):
    """Autonomous optimization suggestion generated by Laya."""

    __tablename__ = "suggestions"
    __table_args__ = (
        Index(
            "uq_suggestions_pending_dedup",
            "campaign_id",
            "dedup_key",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=False
    )
    action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="PENDING", default="PENDING")
    dedup_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class CampaignChangeLog(Base, TimestampMixin):
    """Log of autonomous optimization actions applied to campaigns."""

    __tablename__ = "campaign_changelogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=False
    )
    action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class OptimizationDecision(Base, TimestampMixin):
    """Audit log of campaign optimization decisions and actions (Phase 5)."""

    __tablename__ = "optimization_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)  # applied, suggested, shadow_logged, rejected, failed
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    change_applied: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system_optimizer")
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AdvertiserApiKey(Base, TimestampMixin):
    """Advertiser API key for authenticating S2S conversion postbacks and management calls.

    The raw key & raw secret are shown exactly once upon creation.
    Only hashes/encrypted secret material are persisted.

    Lifecycle:
    - status=1 (revoked_at is None) active
    - status=0 (revoked_at is not None) revoked
    """

    __tablename__ = "advertiser_api_keys"
    __table_args__ = (
        CheckConstraint("rate_limit_per_minute > 0", name="ck_advertiser_api_keys_rate_limit_positive"),
        CheckConstraint("daily_quota > 0", name="ck_advertiser_api_keys_daily_quota_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    secret_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=False
    )
    daily_quota: Mapped[int] = mapped_column(
        Integer, default=10000, server_default=text("10000"), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    advertiser: Mapped["Advertiser"] = relationship("Advertiser", backref="api_keys", lazy="selectin")
    campaign: Mapped["Campaign | None"] = relationship("Campaign", lazy="selectin")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
