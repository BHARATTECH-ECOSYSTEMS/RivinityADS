"""
Tests for Phase 9.1: Hyperlocal Geo-Targeting Contract & Validation.

Verifies:
1. Valid and invalid latitude/longitude ranges and non-finite values.
2. Missing, malformed, negative, zero, sub-minimum, and excessive radius values.
3. Existing administrative targeting compatibility (country/state/city/pincode).
4. Incompatible field combinations (mixing radius with city/state/pincode).
5. TargetingSet deduplication and include/exclude conflict detection.
6. LocationSource and LocationQualityIndicator privacy constraints (IP vs device GPS).
7. Phase 9.1 boundary guard: radius targets are not evaluated as country-wide matches in ad delivery.
"""

import math
import pytest
from pydantic import ValidationError

from liteads.schemas.targeting import (
    GeoEntry,
    GeoTargetType,
    LocationQualityIndicator,
    LocationSource,
    MIN_RADIUS_METERS,
    MAX_RADIUS_METERS,
    RadiusUnit,
    TargetingSet,
)
from liteads.schemas.internal import UserContext
from liteads.ad_server.services.targeting import match_geo


# ==============================================================================
# 1. Coordinate Validation Tests (Latitude / Longitude)
# ==============================================================================

def test_valid_coordinates_wgs84():
    """Valid latitude [-90, 90] and longitude [-180, 180] must be accepted."""
    test_cases = [
        (0.0, 0.0),                          # Equator / Prime Meridian
        (19.0760, 72.8777),                  # Mumbai, India
        (37.7749, -122.4194),                # San Francisco, USA
        (-33.8688, 151.2093),                # Sydney, Australia
        (90.0, 0.0),                         # North Pole boundary
        (-90.0, 0.0),                        # South Pole boundary
        (0.0, 180.0),                        # Antimeridian East boundary
        (0.0, -180.0),                       # Antimeridian West boundary
        (90.0, 180.0),                       # Extreme positive boundary
        (-90.0, -180.0),                     # Extreme negative boundary
    ]
    for lat, lon in test_cases:
        entry = GeoEntry(latitude=lat, longitude=lon, radius_meters=1000.0)
        assert entry.is_radius is True
        assert entry.target_type == GeoTargetType.RADIUS
        assert entry.latitude == lat
        assert entry.longitude == lon
        assert entry.radius_meters == 1000.0
        assert entry.radius_unit == RadiusUnit.METERS


def test_invalid_latitude_out_of_bounds():
    """Latitude > 90.0 or < -90.0 must raise ValidationError."""
    invalid_lats = [90.0001, 91.0, 150.0, -90.0001, -91.0, -120.0]
    for lat in invalid_lats:
        with pytest.raises(ValidationError) as exc:
            GeoEntry(latitude=lat, longitude=0.0, radius_meters=1000.0)
        assert "latitude must be between -90.0 and 90.0 degrees" in str(exc.value)


def test_invalid_longitude_out_of_bounds():
    """Longitude > 180.0 or < -180.0 must raise ValidationError."""
    invalid_lons = [180.0001, 181.0, 250.0, -180.0001, -181.0, -300.0]
    for lon in invalid_lons:
        with pytest.raises(ValidationError) as exc:
            GeoEntry(latitude=0.0, longitude=lon, radius_meters=1000.0)
        assert "longitude must be between -180.0 and 180.0 degrees" in str(exc.value)


def test_non_finite_coordinates_rejected():
    """NaN and Infinite values must be rejected."""
    for bad_val in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValidationError):
            GeoEntry(latitude=bad_val, longitude=0.0, radius_meters=1000.0)
        with pytest.raises(ValidationError):
            GeoEntry(latitude=0.0, longitude=bad_val, radius_meters=1000.0)


# ==============================================================================
# 2. Radius Completeness and Range Validation Tests
# ==============================================================================

def test_radius_field_completeness_required():
    """If any radius field is provided, all 3 (lat, lon, radius_meters) must be provided."""
    # Missing radius_meters
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, longitude=72.8777)
    assert "radius_meters" in str(exc.value)

    # Missing latitude
    with pytest.raises(ValidationError) as exc:
        GeoEntry(longitude=72.8777, radius_meters=5000.0)
    assert "latitude" in str(exc.value)

    # Missing longitude
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, radius_meters=5000.0)
    assert "longitude" in str(exc.value)

    # Only radius_meters provided
    with pytest.raises(ValidationError) as exc:
        GeoEntry(radius_meters=5000.0)
    assert "latitude" in str(exc.value)
    assert "longitude" in str(exc.value)


def test_radius_zero_and_negative_rejected():
    """Radius <= 0 must be rejected."""
    for bad_r in [0.0, -1.0, -500.0]:
        with pytest.raises(ValidationError) as exc:
            GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=bad_r)
        assert "radius_meters must be strictly positive" in str(exc.value)


def test_radius_sub_minimum_rejected():
    """Radius < 100.0 meters must be rejected due to GPS jitter and privacy boundaries."""
    for sub_min in [0.1, 10.0, 50.0, 99.9]:
        with pytest.raises(ValidationError) as exc:
            GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=sub_min)
        assert f"radius_meters must be at least {MIN_RADIUS_METERS}" in str(exc.value)


def test_radius_excessive_rejected():
    """Radius > 100,000.0 meters (100km) must be rejected with recommendation to use administrative targeting."""
    for excessive in [100000.1, 150000.0, 500000.0]:
        with pytest.raises(ValidationError) as exc:
            GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=excessive)
        assert f"radius_meters must not exceed {MAX_RADIUS_METERS}" in str(exc.value)
        assert "administrative" in str(exc.value)


def test_radius_exact_boundaries_accepted():
    """Boundary values (exactly 100m and exactly 100,000m) must be accepted."""
    entry_min = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=100.0)
    assert entry_min.radius_meters == 100.0

    entry_max = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=100000.0)
    assert entry_max.radius_meters == 100000.0


def test_radius_non_finite_rejected():
    """NaN and Inf radius must be rejected."""
    for bad_r in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValidationError):
            GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=bad_r)


def test_radius_explicit_unit_support():
    """Explicit units (meters, km, miles) must be validated."""
    entry_meters = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, radius_unit=RadiusUnit.METERS)
    assert entry_meters.radius_unit == RadiusUnit.METERS

    entry_km = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, radius_unit=RadiusUnit.KILOMETERS)
    assert entry_km.radius_unit == RadiusUnit.KILOMETERS


# ==============================================================================
# 3. Incompatible Combinations & Ambiguity Rejection
# ==============================================================================

def test_radius_cannot_mix_with_city():
    """A radius target must not specify city."""
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, city="Mumbai")
    assert "Radius targeting cannot be combined with city" in str(exc.value)


def test_radius_cannot_mix_with_state():
    """A radius target must not specify state."""
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, state="MH")
    assert "Radius targeting cannot be combined with state" in str(exc.value)


def test_radius_cannot_mix_with_pincode():
    """A radius target must not specify pincode."""
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, pincode="400001")
    assert "Radius targeting cannot be combined with pincode" in str(exc.value)


def test_radius_optional_country_handling():
    """Radius target can optionally include a valid 2-letter ISO country code or omit it."""
    # With valid country code (case-normalized)
    entry_with_country = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, country="in")
    assert entry_with_country.country == "IN"

    # Without country code
    entry_without_country = GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0)
    assert entry_without_country.country is None

    # Invalid country code
    with pytest.raises(ValidationError) as exc:
        GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, country="INDIA")
    assert "ISO 3166-1 alpha-2" in str(exc.value)


# ==============================================================================
# 4. Existing Administrative Targeting Compatibility
# ==============================================================================

def test_administrative_country_only_valid():
    """Country-only targeting must remain valid and backward-compatible."""
    entry = GeoEntry(country="IN")
    assert entry.is_radius is False
    assert entry.target_type == GeoTargetType.ADMINISTRATIVE
    assert entry.country == "IN"
    assert entry.state is None
    assert entry.city is None
    assert entry.pincode is None
    assert entry.radius_meters is None
    assert entry.radius_unit is None


def test_administrative_state_and_city_valid():
    """Country + State and Country + State + City must remain valid."""
    entry_state = GeoEntry(country="IN", state="MH")
    assert entry_state.state == "MH"

    entry_city = GeoEntry(country="IN", state="MH", city="Mumbai")
    assert entry_city.city == "Mumbai"


def test_administrative_pincode_valid():
    """Country + Pincode must remain valid."""
    entry_pincode = GeoEntry(country="IN", pincode="400001")
    assert entry_pincode.pincode == "400001"


def test_administrative_errors_preserved():
    """Existing administrative validation errors must be preserved."""
    # Missing country
    with pytest.raises(ValidationError) as exc:
        GeoEntry(state="MH")
    assert "country is required for administrative geo targeting" in str(exc.value)

    # City without state
    with pytest.raises(ValidationError) as exc:
        GeoEntry(country="IN", city="Mumbai")
    assert "city entries must also set state" in str(exc.value)

    # Pincode with state/city
    with pytest.raises(ValidationError) as exc:
        GeoEntry(country="IN", pincode="400001", state="MH")
    assert "pincode entries must not also set state/city" in str(exc.value)


# ==============================================================================
# 5. TargetingSet Deduplication & Conflict Detection
# ==============================================================================

def test_targeting_set_rejects_duplicate_admin_entries():
    """Duplicate administrative targets in geo_include must be rejected."""
    with pytest.raises(ValidationError) as exc:
        TargetingSet(
            geo_include=[
                GeoEntry(country="IN", state="MH", city="Mumbai"),
                GeoEntry(country="IN", state="MH", city="Mumbai"),
            ]
        )
    assert "Duplicate geo target found in geo_include" in str(exc.value)


def test_targeting_set_rejects_duplicate_radius_entries():
    """Duplicate radius targets in geo_include must be rejected."""
    with pytest.raises(ValidationError) as exc:
        TargetingSet(
            geo_include=[
                GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0),
                GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0),
            ]
        )
    assert "Duplicate geo target found in geo_include" in str(exc.value)


def test_targeting_set_rejects_identical_include_exclude_collision():
    """Targeting the exact same target in both include and exclude is a direct contradiction."""
    with pytest.raises(ValidationError) as exc:
        TargetingSet(
            geo_include=[GeoEntry(country="IN")],
            geo_exclude=[GeoEntry(country="IN")],
        )
    assert "Conflicting targeting rule: identical target exists in both geo_include and geo_exclude" in str(exc.value)

    # Radius collision
    with pytest.raises(ValidationError) as exc:
        TargetingSet(
            geo_include=[GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0)],
            geo_exclude=[GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0)],
        )
    assert "Conflicting targeting rule" in str(exc.value)


def test_targeting_set_sector_deduplication_and_conflict():
    """Sector include/exclude deduplication and conflict checks."""
    # Duplicate in sector_include
    with pytest.raises(ValidationError) as exc:
        TargetingSet(sector_include=["fintech", "Fintech"])
    assert "Duplicate sector found in sector_include" in str(exc.value)

    # Conflict in both include and exclude
    with pytest.raises(ValidationError) as exc:
        TargetingSet(sector_include=["gaming"], sector_exclude=["GAMING"])
    assert "Conflicting sector rule: 'gaming' exists in both" in str(exc.value)


def test_mixed_targeting_set_valid():
    """TargetingSet can contain both administrative and radius targets."""
    tset = TargetingSet(
        geo_include=[
            GeoEntry(country="IN", state="KA", city="Bengaluru"),
            GeoEntry(latitude=19.0760, longitude=72.8777, radius_meters=5000.0, name="Bandra Store"),
        ],
        geo_exclude=[
            GeoEntry(country="IN", pincode="560001"),
        ],
        sector_include=["ecommerce"],
        sector_exclude=["gambling"],
    )
    assert len(tset.geo_include) == 2
    assert len(tset.geo_exclude) == 1
    assert tset.geo_include[0].is_radius is False
    assert tset.geo_include[1].is_radius is True


# ==============================================================================
# 6. Location Precision & Privacy Constraints
# ==============================================================================

def test_location_quality_indicator_ip_is_always_approximate():
    """IP resolution must always be marked approximate with an error radius >= 5km."""
    lqi = LocationQualityIndicator(source=LocationSource.IP)
    assert lqi.is_approximate is True
    assert lqi.accuracy_radius_meters >= 5000.0
    assert lqi.has_user_consent is False


def test_location_quality_indicator_gps_requires_consent():
    """Device GPS without consent must be marked approximate."""
    lqi_no_consent = LocationQualityIndicator(source=LocationSource.DEVICE_GPS, has_user_consent=False)
    assert lqi_no_consent.is_approximate is True

    lqi_with_consent = LocationQualityIndicator(source=LocationSource.DEVICE_GPS, has_user_consent=True, accuracy_radius_meters=15.0)
    assert lqi_with_consent.is_approximate is False
    assert lqi_with_consent.accuracy_radius_meters == 15.0


# ==============================================================================
# 7. Phase 9.1 Boundary Guard: Ad Delivery Safety
# ==============================================================================

def test_phase9_1_boundary_guard_radius_does_not_match_whole_country():
    """
    CRITICAL: In Phase 9.1, the contract and validation are established,
    but the matching engine is deferred to Phase 9.2.
    An ad with a radius rule MUST NOT match a user simply because their country matches!
    """
    rule = {
        "entries": [
            {
                "country": "IN",
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
            }
        ]
    }
    # User is in India, but radius matching is deferred to Phase 9.2.
    # match_geo must skip the radius rule and NOT return True.
    ctx = UserContext(country="IN", latitude=19.0760, longitude=72.8777)
    assert match_geo(rule, ctx) is False


def test_phase9_1_boundary_guard_approximate_ip_does_not_match_radius():
    """
    Coarse IP resolution (centroid) must not falsely match radius rules.
    """
    rule = {
        "entries": [
            {
                "country": "IN",
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 1000.0,
            }
        ]
    }
    # User has coarse IP centroid coordinates
    ctx = UserContext(
        country="IN",
        latitude=19.0,
        longitude=72.8,
        location_source="ip",
        location_accuracy_meters=10000.0,
        location_consent=False,
    )
    assert match_geo(rule, ctx) is False


def test_phase9_1_mixed_rules_still_match_administrative_entries():
    """
    In a mixed rule set, administrative entries match normally while radius entries
    are guarded until Phase 9.2.
    """
    rule = {
        "entries": [
            # Radius entry (Phase 9.2 deferred)
            {"country": "IN", "latitude": 19.0760, "longitude": 72.8777, "radius_meters": 5000.0},
            # Administrative entry (active now)
            {"country": "IN", "state": "MH", "city": "Mumbai"},
        ]
    }
    # User matches the administrative entry
    ctx_mumbai = UserContext(country="IN", region="MH", city="Mumbai")
    assert match_geo(rule, ctx_mumbai) is True

    # User in Pune matches neither the administrative entry nor the radius entry
    ctx_pune = UserContext(country="IN", region="MH", city="Pune")
    assert match_geo(rule, ctx_pune) is False
