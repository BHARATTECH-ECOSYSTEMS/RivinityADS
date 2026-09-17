"""
Targeting request/response contracts (geo hierarchy + sectors).
"""

from pydantic import BaseModel, Field, model_validator


class GeoEntry(BaseModel):
    """One location entry. Use the shallowest fields you need:
    {'country':'IN'} | {'country':'IN','state':'MH'} |
    {'country':'IN','state':'MH','city':'Mumbai'} | {'country':'IN','pincode':'400001'}
    """
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    state: str | None = None      # state ISO code or name, as loaded in geo_locations
    city: str | None = None
    pincode: str | None = None

    @model_validator(mode="after")
    def consistency(self):
        if self.pincode and (self.state or self.city):
            raise ValueError("pincode entries must not also set state/city")
        if self.city and not self.state:
            raise ValueError("city entries must also set state")
        return self


class TargetingSet(BaseModel):
    """Full targeting payload for PUT /campaign/{id}/targeting."""
    geo_include: list[GeoEntry] = Field(default_factory=list)
    geo_exclude: list[GeoEntry] = Field(default_factory=list)
    sector_include: list[str] = Field(default_factory=list)   # sector ids, from taxonomy
    sector_exclude: list[str] = Field(default_factory=list)


class TargetingResponse(TargetingSet):
    """Response containing targeting set and campaign ID."""
    campaign_id: int
