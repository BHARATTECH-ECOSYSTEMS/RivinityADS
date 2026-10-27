"""
Targeting request/response contracts (geo hierarchy, sectors, and hyperlocal radius targeting).
"""

import math
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, model_validator


class RadiusUnit(str, Enum):
    """Supported distance units for radius targeting."""
    METERS = "meters"
    KILOMETERS = "km"
    MILES = "miles"


class GeoTargetType(str, Enum):
    """Discriminator for geographic targeting types."""
    ADMINISTRATIVE = "administrative"
    RADIUS = "radius"


class LocationSource(str, Enum):
    """
    Provenance of location signals.
    Distinguishes coarse/approximate IP-derived centroids from high-precision device coordinates.
    """
    IP = "ip"                         # Approximate city/metro centroid from MaxMind (accuracy ~10-50km)
    DEVICE_GPS = "device_gps"         # High-precision GPS from device/browser with explicit consent (accuracy ~5-50m)
    COARSE_NETWORK = "coarse_network" # Cell tower or WiFi BSSID triangulation (accuracy ~500-3000m)
    DERIVED_ADMIN = "derived_admin"   # Centroid inferred from administrative territory (pincode/city table)
    UNKNOWN = "unknown"               # Unspecified or missing location signal


class LocationQualityIndicator(BaseModel):
    """
    Quality and privacy-consent metadata for location signals.
    Guards against treating coarse IP centroids as precise GPS coordinates.
    """
    source: LocationSource = Field(default=LocationSource.UNKNOWN, description="Signal provenance")
    accuracy_radius_meters: float | None = Field(
        default=None,
        ge=0,
        description="95% confidence accuracy radius in meters. None if unknown or unmeasurable."
    )
    has_user_consent: bool = Field(
        default=False,
        description="Explicit user consent indicator for geolocation processing (DPDP / GDPR / CCPA compliant)"
    )
    is_approximate: bool = Field(
        default=True,
        description="True for IP-derived or coarse-network estimates; False only for verified device GPS"
    )

    @model_validator(mode="after")
    def enforce_privacy_rules(self) -> "LocationQualityIndicator":
        if self.source == LocationSource.IP:
            # IP-derived coordinates must ALWAYS be marked approximate with no claim of user consent
            self.is_approximate = True
            if self.accuracy_radius_meters is None or self.accuracy_radius_meters < 5000.0:
                # MaxMind IP city resolution typically has a 5km - 50km error radius
                self.accuracy_radius_meters = 10000.0
        elif self.source == LocationSource.DEVICE_GPS:
            # Device GPS requires explicit consent
            if not self.has_user_consent:
                # Without explicit consent, device GPS cannot be legally treated as authorized precise signal
                self.is_approximate = True
            else:
                self.is_approximate = False
        return self


# Radius bounds justified:
# - Minimum: 100.0 meters. GPS receiver drift / urban canyon reflections typically induce 10-50m jitter.
#   Sub-100m fences lead to severe delivery drop-off and erratic impressions.
# - Maximum: 100,000.0 meters (100 km). A 100km circle covers ~31,416 km², spanning entire metropolitan areas.
#   Beyond 100km, spherical distortion increases and radius targeting crosses administrative boundaries uncontrollably.
#   For areas > 100km, administrative country/state/city targeting must be used.
MIN_RADIUS_METERS = 100.0
MAX_RADIUS_METERS = 100_000.0


class GeoEntry(BaseModel):
    """One location entry.

    Supports:
    1. Administrative hierarchy (backward-compatible):
       {'country': 'IN'}
       {'country': 'IN', 'state': 'MH'}
       {'country': 'IN', 'state': 'MH', 'city': 'Mumbai'}
       {'country': 'IN', 'pincode': '400001'}

    2. Hyperlocal radius targeting (Phase 9.1 contract):
       {
           'latitude': 19.0760,
           'longitude': 72.8777,
           'radius_meters': 5000.0,
           'radius_unit': 'meters',
           'country': 'IN'  # optional for radius
       }
    """

    # Administrative targeting fields
    country: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2 country code. Required for administrative targeting; optional for radius."
    )
    state: str | None = Field(default=None, description="State ISO code or name")
    city: str | None = Field(default=None, description="City name")
    pincode: str | None = Field(default=None, description="Postal / ZIP code")

    # Hyperlocal radius targeting fields (Phase 9.1)
    latitude: float | None = Field(
        default=None,
        description="Center latitude in decimal degrees (WGS 84, -90.0 to 90.0)"
    )
    longitude: float | None = Field(
        default=None,
        description="Center longitude in decimal degrees (WGS 84, -180.0 to 180.0)"
    )
    radius_meters: float | None = Field(
        default=None,
        description="Targeting radius in meters (100.0 to 100,000.0 meters)"
    )
    radius_unit: RadiusUnit | None = Field(
        default=None,
        description="Explicit distance unit, defaults to 'meters' when radius targeting is used"
    )
    name: str | None = Field(
        default=None,
        description="Optional human-readable label for radius target (e.g. 'Downtown Flagship Store')"
    )

    @property
    def target_type(self) -> GeoTargetType:
        """Identifies whether this entry is an administrative or radius target."""
        if self.radius_meters is not None or self.latitude is not None or self.longitude is not None:
            return GeoTargetType.RADIUS
        return GeoTargetType.ADMINISTRATIVE

    @property
    def is_radius(self) -> bool:
        return self.target_type == GeoTargetType.RADIUS

    @model_validator(mode="after")
    def validate_geo_entry(self) -> "GeoEntry":
        has_lat = self.latitude is not None
        has_lon = self.longitude is not None
        has_radius = self.radius_meters is not None

        # --- A. Radius Targeting Validation ---
        if has_lat or has_lon or has_radius:
            # 1. Completeness: all 3 fields required together
            if not (has_lat and has_lon and has_radius):
                missing = []
                if not has_lat:
                    missing.append("latitude")
                if not has_lon:
                    missing.append("longitude")
                if not has_radius:
                    missing.append("radius_meters")
                raise ValueError(
                    f"Radius targeting requires latitude, longitude, and radius_meters to all be specified. Missing: {', '.join(missing)}"
                )

            # 2. Finite number checks (reject NaN, Inf)
            assert self.latitude is not None
            assert self.longitude is not None
            assert self.radius_meters is not None

            if math.isnan(self.latitude) or math.isinf(self.latitude):
                raise ValueError("latitude must be a finite number")
            if math.isnan(self.longitude) or math.isinf(self.longitude):
                raise ValueError("longitude must be a finite number")
            if math.isnan(self.radius_meters) or math.isinf(self.radius_meters):
                raise ValueError("radius_meters must be a finite number")

            # 3. Coordinate range checks (WGS 84)
            if not (-90.0 <= self.latitude <= 90.0):
                raise ValueError(
                    f"latitude must be between -90.0 and 90.0 degrees, got {self.latitude}"
                )
            if not (-180.0 <= self.longitude <= 180.0):
                raise ValueError(
                    f"longitude must be between -180.0 and 180.0 degrees, got {self.longitude}"
                )

            # 4. Radius range checks
            if self.radius_meters <= 0:
                raise ValueError(
                    f"radius_meters must be strictly positive (> 0), got {self.radius_meters}"
                )
            if self.radius_meters < MIN_RADIUS_METERS:
                raise ValueError(
                    f"radius_meters must be at least {MIN_RADIUS_METERS} meters (got {self.radius_meters}m). "
                    "Sub-100m geofencing is unreliable due to GPS drift and urban canyon reflection."
                )
            if self.radius_meters > MAX_RADIUS_METERS:
                raise ValueError(
                    f"radius_meters must not exceed {MAX_RADIUS_METERS} meters ({int(MAX_RADIUS_METERS / 1000)} km, got {self.radius_meters}m). "
                    "For broader regions, use administrative country/state/city targeting."
                )

            # 5. Incompatible field combinations (cannot mix radius with state/city/pincode)
            if self.pincode is not None:
                raise ValueError("Radius targeting cannot be combined with pincode")
            if self.city is not None:
                raise ValueError("Radius targeting cannot be combined with city")
            if self.state is not None:
                raise ValueError("Radius targeting cannot be combined with state")

            # 6. Optional country validation for radius
            if self.country is not None:
                c_clean = self.country.strip().upper()
                if len(c_clean) != 2 or not c_clean.isalpha():
                    raise ValueError(
                        f"country must be a 2-letter ISO 3166-1 alpha-2 code, got '{self.country}'"
                    )
                self.country = c_clean

            if self.radius_unit is None:
                self.radius_unit = RadiusUnit.METERS

            return self

        # --- B. Administrative Hierarchy Validation ---
        self.radius_unit = None
        if not self.country:
            raise ValueError("country is required for administrative geo targeting")

        c_clean = self.country.strip().upper()
        if len(c_clean) != 2 or not c_clean.isalpha():
            raise ValueError(
                f"country must be a 2-letter ISO 3166-1 alpha-2 code, got '{self.country}'"
            )
        self.country = c_clean

        if self.pincode and (self.state or self.city):
            raise ValueError("pincode entries must not also set state/city")
        if self.city and not self.state:
            raise ValueError("city entries must also set state")

        return self

    def canonical_key(self) -> str:
        """Deterministic string key for duplicate and conflict detection."""
        if self.is_radius:
            assert self.latitude is not None
            assert self.longitude is not None
            assert self.radius_meters is not None
            c = (self.country or "").upper()
            lat = round(self.latitude, 6)
            lon = round(self.longitude, 6)
            r = round(self.radius_meters, 1)
            return f"radius:{c}:{lat}:{lon}:{r}"
        else:
            c = (self.country or "").upper()
            s = (self.state or "").strip().lower()
            city = (self.city or "").strip().lower()
            p = (self.pincode or "").strip().lower()
            return f"admin:{c}:{s}:{city}:{p}"


class TargetingSet(BaseModel):
    """Full targeting payload for PUT /campaign/{id}/targeting and PUT /ad-set/{id}/targeting."""

    geo_include: list[GeoEntry] = Field(default_factory=list)
    geo_exclude: list[GeoEntry] = Field(default_factory=list)
    sector_include: list[str] = Field(default_factory=list)
    sector_exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_targeting_set(self) -> "TargetingSet":
        # 1. Deduplicate & validate geo_include
        include_keys: set[str] = set()
        for idx, entry in enumerate(self.geo_include):
            key = entry.canonical_key()
            if key in include_keys:
                raise ValueError(f"Duplicate geo target found in geo_include at index {idx}: {key}")
            include_keys.add(key)

        # 2. Deduplicate & validate geo_exclude
        exclude_keys: set[str] = set()
        for idx, entry in enumerate(self.geo_exclude):
            key = entry.canonical_key()
            if key in exclude_keys:
                raise ValueError(f"Duplicate geo target found in geo_exclude at index {idx}: {key}")
            exclude_keys.add(key)

        # 3. Direct conflict between include and exclude
        conflicts = include_keys.intersection(exclude_keys)
        if conflicts:
            conflict_item = sorted(list(conflicts))[0]
            raise ValueError(
                f"Conflicting targeting rule: identical target exists in both geo_include and geo_exclude ({conflict_item})"
            )

        # 4. Sector deduplication and conflict validation
        sec_include_normalized: set[str] = set()
        for sec in self.sector_include:
            sec_clean = sec.strip().lower()
            if not sec_clean:
                raise ValueError("Sector identifier cannot be empty")
            if sec_clean in sec_include_normalized:
                raise ValueError(f"Duplicate sector found in sector_include: '{sec}'")
            sec_include_normalized.add(sec_clean)

        sec_exclude_normalized: set[str] = set()
        for sec in self.sector_exclude:
            sec_clean = sec.strip().lower()
            if not sec_clean:
                raise ValueError("Sector identifier cannot be empty")
            if sec_clean in sec_exclude_normalized:
                raise ValueError(f"Duplicate sector found in sector_exclude: '{sec}'")
            sec_exclude_normalized.add(sec_clean)

        sec_conflicts = sec_include_normalized.intersection(sec_exclude_normalized)
        if sec_conflicts:
            c = sorted(list(sec_conflicts))[0]
            raise ValueError(f"Conflicting sector rule: '{c}' exists in both sector_include and sector_exclude")

        return self


class TargetingResponse(TargetingSet):
    """Response containing targeting set and campaign ID."""
    campaign_id: int
    ad_set_id: int | None = None


class AdSetTargetingResponse(TargetingSet):
    """Response containing targeting set, ad set ID, and campaign ID."""
    ad_set_id: int
    campaign_id: int

