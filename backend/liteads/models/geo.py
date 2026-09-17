"""
Geo location reference data model.
"""

from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from liteads.models.base import Base


class GeoLocation(Base):
    """
    Geographic reference data (countries, states, cities, pincodes).
    Typically loaded from GeoNames or similar services.
    """

    __tablename__ = "geo_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    state_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    state_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pincode: Mapped[str] = mapped_column(String(16), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("country", "state_code", "city", "pincode", name="uq_geo_location_full"),
        Index("idx_geo_country_state", "country", "state_code"),
        Index("idx_geo_city", "country", "city"),
    )
