"""Publisher inventory and reporting models."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, JSON, UniqueConstraint, text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, Status, TimestampMixin


class Publisher(Base, TimestampMixin):
    """Publisher account that owns ad inventory."""

    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE, nullable=False)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    payout_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    slots: Mapped[list["AdSlot"]] = relationship(
        "AdSlot", back_populates="publisher", cascade="all, delete-orphan", lazy="selectin"
    )
    daily_stats: Mapped[list["PublisherDailyStat"]] = relationship(
        "PublisherDailyStat", back_populates="publisher", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["PublisherApiKey"]] = relationship(
        "PublisherApiKey", back_populates="publisher", cascade="all, delete-orphan"
    )
    properties: Mapped[list["PublisherProperty"]] = relationship(
        "PublisherProperty", back_populates="publisher", cascade="all, delete-orphan", lazy="selectin"
    )


class PublisherProperty(Base, TimestampMixin):
    """A website or app owned by a publisher."""

    __tablename__ = "publisher_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE, nullable=False)

    publisher: Mapped["Publisher"] = relationship("Publisher", back_populates="properties")
    slots: Mapped[list["AdSlot"]] = relationship(
        "AdSlot", back_populates="property", cascade="all, delete-orphan", lazy="selectin"
    )


class AdSlot(Base, TimestampMixin):
    """A publisher placement addressable by the ad request API."""

    __tablename__ = "ad_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=Status.ACTIVE, nullable=False)
    is_chat_surface: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    floor_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    property_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publisher_properties.id", ondelete="SET NULL"), nullable=True
    )

    publisher: Mapped[Publisher] = relationship("Publisher", back_populates="slots")
    property: Mapped["PublisherProperty"] = relationship("PublisherProperty", back_populates="slots")


class PublisherApiKey(Base, TimestampMixin):
    """Publisher API key for authenticating third-party ad/event API calls.

    The raw key is shown exactly once on creation.
    Only the hash is persisted; authentication hashes or verifies the inbound
    key and compares it to key_hash.

    Lifecycle:
    - status=1 (revoked_at is None) active
    - status=0 (revoked_at is not None) revoked
    """

    __tablename__ = "publisher_api_keys"
    __table_args__ = (
        CheckConstraint("rate_limit_per_minute > 0", name="ck_publisher_api_keys_rate_limit_positive"),
        CheckConstraint("daily_quota > 0", name="ck_publisher_api_keys_daily_quota_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    secret_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=False
    )
    daily_quota: Mapped[int] = mapped_column(
        Integer, default=10000, server_default=text("10000"), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    publisher: Mapped[Publisher] = relationship("Publisher", back_populates="api_keys")

    def __init__(self, **kwargs):
        if "label" in kwargs and "name" not in kwargs:
            kwargs["name"] = kwargs.pop("label")
        if "status" in kwargs:
            status_val = kwargs.pop("status")
            if "revoked_at" not in kwargs and status_val == 0:
                kwargs["revoked_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)

    @property
    def label(self) -> str:
        return self.name

    @label.setter
    def label(self, value: str) -> None:
        self.name = value

    @hybrid_property
    def status(self) -> int:
        return 0 if self.revoked_at is not None else 1

    @status.inplace.setter
    def _status_setter(self, value: int) -> None:
        self.revoked_at = None if value == 1 else datetime.now(timezone.utc)

    @status.inplace.expression
    @classmethod
    def _status_expression(cls):
        from sqlalchemy import case
        return case((cls.revoked_at.isnot(None), 0), else_=1)


class PublisherDailyStat(Base, TimestampMixin):
    """Persisted publisher delivery and earnings aggregate for one day."""

    __tablename__ = "publisher_daily_stats"
    __table_args__ = (
        UniqueConstraint("publisher_id", "slot_id", "stat_date", name="uq_pub_daily_stats_slot_date"),
        Index("idx_pub_daily_stats_lookup", "publisher_id", "stat_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    earnings: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    slot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ad_slots.id", ondelete="CASCADE"), nullable=True
    )

    publisher: Mapped[Publisher] = relationship("Publisher", back_populates="daily_stats")
    slot: Mapped["AdSlot"] = relationship("AdSlot")


class ApiKeyDailyStat(Base, TimestampMixin):
    """Persisted daily usage metrics for an API key."""

    __tablename__ = "api_key_daily_stats"
    __table_args__ = (
        UniqueConstraint("api_key_id", "stat_date", name="uq_api_key_daily_stats_key_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publisher_api_keys.id", ondelete="CASCADE"), nullable=False
    )
    publisher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    admitted_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_limited_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quota_exceeded_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    served_ads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    api_key: Mapped["PublisherApiKey"] = relationship("PublisherApiKey", backref="daily_stats")

