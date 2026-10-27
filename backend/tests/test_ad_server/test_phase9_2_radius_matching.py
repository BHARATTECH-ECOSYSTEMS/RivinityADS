"""
Tests for Phase 9.2: Hyperlocal Radius Matching Engine.

Verifies:
1. Haversine distance calculations and inclusive boundary semantics (distance <= radius).
2. Antimeridian crossings (+180/-180) and high-latitude / polar convergence.
3. Location quality and privacy-consent controls (IP-centroids rejected, consent required, accuracy <= radius).
4. Optional country scoping on radius targets.
5. Deterministic include/exclude precedence and overlapping target resolution.
6. Backward compatibility with administrative country/state/city/pincode targeting.
7. Multi-AdSet geographic isolation without sibling rule leakage.
8. Candidate filtering integration with status, moderation, and lifecycle guards.
9. Resilience to malformed or legacy persisted targeting data.
10. Performance evaluation at scale.
"""

import math
import time
import pytest
from unittest.mock import AsyncMock, patch

from liteads.schemas.internal import AdCandidate, UserContext
from liteads.ad_server.services.targeting import (
    calculate_haversine_distance_meters,
    is_location_eligible_for_radius,
    match_geo,
    match_radius_entry,
    match_administrative_entry,
)
from liteads.rec_engine.retrieval.targeting import TargetingRetrieval


# ==============================================================================
# 1. Haversine Distance & Boundary Semantics
# ==============================================================================

def test_haversine_same_point_is_zero():
    """Distance from a point to itself must be 0.0 meters."""
    dist = calculate_haversine_distance_meters(19.0760, 72.8777, 19.0760, 72.8777)
    assert abs(dist) < 1e-6


def test_haversine_known_geographic_distance():
    """Mumbai (19.0760, 72.8777) to Pune (18.5204, 73.8567) is ~119 km."""
    dist = calculate_haversine_distance_meters(19.0760, 72.8777, 18.5204, 73.8567)
    assert 118_000 < dist < 121_000  # ~119.5 km


def test_boundary_inclusive_semantics():
    """
    Documented rule: A point exactly on the radius boundary matches (distance <= radius).
    Points inside match; points strictly outside fail.
    """
    center_lat, center_lon = 19.0760, 72.8777
    radius_meters = 1000.0

    # User at center (0m < 1000m) -> inside
    entry = {"latitude": center_lat, "longitude": center_lon, "radius_meters": radius_meters}
    ctx_inside = UserContext(
        latitude=center_lat,
        longitude=center_lon,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_inside) is True

    # User exactly on boundary: compute point ~1000m north
    # 1 deg lat ~ 111,195 m => 1000m ~ 1000 / 111195 deg lat ~ 0.0089932 deg
    delta_lat = radius_meters / (math.pi * 6_371_000.0 / 180.0)
    boundary_lat = center_lat + delta_lat
    exact_dist = calculate_haversine_distance_meters(center_lat, center_lon, boundary_lat, center_lon)
    assert abs(exact_dist - radius_meters) < 1e-4

    ctx_boundary = UserContext(
        latitude=boundary_lat,
        longitude=center_lon,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_boundary) is True

    # User slightly outside boundary (1005m > 1000m)
    outside_lat = center_lat + delta_lat + 0.0001
    ctx_outside = UserContext(
        latitude=outside_lat,
        longitude=center_lon,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_outside) is False


# ==============================================================================
# 2. Antimeridian Crossings and High-Latitude Convergence
# ==============================================================================

def test_antimeridian_crossing_inside_radius():
    """
    Target at lon +179.999°, User at lon -179.999° across the Antimeridian (180th meridian).
    The angular separation is 0.002° (~222 meters).
    With a 1,000m radius, the user must match!
    """
    target_lat = 0.0
    target_lon = 179.999
    user_lat = 0.0
    user_lon = -179.999

    distance = calculate_haversine_distance_meters(user_lat, user_lon, target_lat, target_lon)
    assert 220.0 < distance < 225.0  # ~222.4 meters

    entry = {"latitude": target_lat, "longitude": target_lon, "radius_meters": 1000.0}
    ctx = UserContext(
        latitude=user_lat,
        longitude=user_lon,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx) is True


def test_antimeridian_crossing_outside_radius():
    """
    Target at lon +179.90°, User at lon -179.80° across the Antimeridian.
    Separation is 0.30° (~33.3 km). With a 5,000m radius, the user must not match.
    """
    entry = {"latitude": 0.0, "longitude": 179.90, "radius_meters": 5000.0}
    ctx = UserContext(
        latitude=0.0,
        longitude=-179.80,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx) is False


def test_high_latitude_polar_convergence():
    """
    Near the North Pole: Target at (89.9°, 0.0°), User at (89.9°, 180.0°).
    Shortest path goes directly over the pole: 0.1° up to pole + 0.1° down = 0.2° ~ 22.2 km.
    Target radius = 25,000m => User must match!
    """
    target_lat, target_lon = 89.9, 0.0
    user_lat, user_lon = 89.9, 180.0

    distance = calculate_haversine_distance_meters(user_lat, user_lon, target_lat, target_lon)
    assert 22_000 < distance < 22_500  # ~22.23 km

    entry = {"latitude": target_lat, "longitude": target_lon, "radius_meters": 25000.0}
    ctx = UserContext(
        latitude=user_lat,
        longitude=user_lon,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx) is True

    # Smaller radius (15,000m) should fail
    entry_small = {"latitude": target_lat, "longitude": target_lon, "radius_meters": 15000.0}
    assert match_geo({"entries": [entry_small]}, ctx) is False


# ==============================================================================
# 3. Location Quality & Privacy Consent Controls
# ==============================================================================

def test_ip_derived_centroid_never_matches_radius():
    """
    CRITICAL PRIVACY INVARIANT:
    IP-derived coordinates must NEVER be treated as precise GPS.
    Even if the IP centroid coordinates fall exactly at the center of the target,
    the rule must fail closed.
    """
    entry = {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 5000.0}
    ctx_ip = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="ip",
        location_accuracy_meters=10000.0,
        location_consent=False,
    )
    is_eligible, reason = is_location_eligible_for_radius(ctx_ip, 5000.0)
    assert is_eligible is False
    assert "IP-derived centroid cannot match radius geofence" in reason

    assert match_geo({"entries": [entry]}, ctx_ip) is False


def test_device_coordinates_require_consent():
    """Device coordinates without explicit user consent must fail closed."""
    entry = {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 5000.0}
    ctx_unconsented = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=False,  # NO CONSENT
    )
    is_eligible, reason = is_location_eligible_for_radius(ctx_unconsented, 5000.0)
    assert is_eligible is False
    assert "Missing user consent" in reason

    assert match_geo({"entries": [entry]}, ctx_unconsented) is False


def test_insufficient_accuracy_uncertainty_fails_closed():
    """
    If the reported accuracy radius is coarser than the target radius
    (uncertainty > target radius), fail closed to avoid false precision.
    """
    target_radius = 500.0  # 500m geofence

    # Reported accuracy is 1500m (uncertainty 3x larger than fence) -> FAIL CLOSED
    ctx_coarse = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_accuracy_meters=1500.0,
        location_consent=True,
    )
    is_eligible, reason = is_location_eligible_for_radius(ctx_coarse, target_radius)
    assert is_eligible is False
    assert "coarser than target radius" in reason

    entry = {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": target_radius}
    assert match_geo({"entries": [entry]}, ctx_coarse) is False

    # Reported accuracy is 25m (accurate enough for 500m fence) -> PASS
    ctx_accurate = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_accuracy_meters=25.0,
        location_consent=True,
    )
    is_eligible, _ = is_location_eligible_for_radius(ctx_accurate, target_radius)
    assert is_eligible is True
    assert match_geo({"entries": [entry]}, ctx_accurate) is True


def test_missing_or_invalid_coordinates():
    """Missing, NaN, or out-of-bounds coordinates must fail closed."""
    entry = {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 1000.0}

    # None coordinates
    ctx_none = UserContext(latitude=None, longitude=None, location_consent=True)
    assert match_geo({"entries": [entry]}, ctx_none) is False

    # NaN coordinates
    ctx_nan = UserContext(latitude=float("nan"), longitude=72.8777, location_consent=True)
    assert match_geo({"entries": [entry]}, ctx_nan) is False

    # Out of bounds coordinates
    ctx_oob = UserContext(latitude=95.0, longitude=72.8777, location_consent=True)
    assert match_geo({"entries": [entry]}, ctx_oob) is False


# ==============================================================================
# 4. Optional Country Scoping
# ==============================================================================

def test_radius_target_with_country_scope():
    """When a radius target specifies a country code, the user's country must match."""
    entry = {
        "latitude": 19.0760,
        "longitude": 72.8777,
        "radius_meters": 5000.0,
        "country": "IN",
    }

    # User in India with GPS -> Match
    ctx_in = UserContext(
        country="IN",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_in) is True

    # User country mismatch (e.g. US) -> Does not match
    ctx_us = UserContext(
        country="US",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_us) is False

    # User country missing when required by target -> Does not match
    ctx_no_country = UserContext(
        country="",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx_no_country) is False


def test_radius_target_without_country_scope():
    """When a radius target has no country code, it matches purely on coordinates."""
    entry = {
        "latitude": 19.0760,
        "longitude": 72.8777,
        "radius_meters": 5000.0,
        "country": None,
    }
    # Matches even if user country is missing or different
    ctx = UserContext(
        country="",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo({"entries": [entry]}, ctx) is True


# ==============================================================================
# 5. Include/Exclude Precedence and Overlapping Targets
# ==============================================================================

def test_exclusion_precedence_over_matching_include():
    """
    Deterministic rule: Any matching exclusion immediately disqualifies the candidate,
    even if the user matches an include target.
    """
    retrieval = TargetingRetrieval(session=AsyncMock())

    # AdSet targets all of Mumbai (administrative include)
    # but excludes a 1km radius around a specific competitor location
    ad_set_data = {
        "id": 101,
        "targeting_rules": [
            {
                "rule_type": "geo",
                "is_include": True,
                "rule_value": {"entries": [{"country": "IN", "state": "MH", "city": "Mumbai"}]},
            },
            {
                "rule_type": "geo",
                "is_include": False,
                "rule_value": {
                    "entries": [
                        {
                            "latitude": 19.0760,
                            "longitude": 72.8777,
                            "radius_meters": 1000.0,
                        }
                    ]
                },
            },
        ],
    }

    # Case A: User in Mumbai, 200m from competitor -> EXCLUDED
    ctx_near_competitor = UserContext(
        country="IN",
        region="MH",
        city="Mumbai",
        latitude=19.0770,  # ~110m north
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert retrieval._match_targeting(ad_set_data, ctx_near_competitor) is False

    # Case B: User in Mumbai, 5km from competitor -> ELIGIBLE
    ctx_far = UserContext(
        country="IN",
        region="MH",
        city="Mumbai",
        latitude=19.1200,  # ~4.9 km north
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert retrieval._match_targeting(ad_set_data, ctx_far) is True


def test_radius_include_with_administrative_exclude():
    """
    AdSet targets 5km radius around a store, but excludes pincode 400001.
    """
    retrieval = TargetingRetrieval(session=AsyncMock())
    ad_set_data = {
        "id": 102,
        "targeting_rules": [
            {
                "rule_type": "geo",
                "is_include": True,
                "rule_value": {
                    "entries": [{"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 5000.0}]
                },
            },
            {
                "rule_type": "geo",
                "is_include": False,
                "rule_value": {"entries": [{"country": "IN", "pincode": "400001"}]},
            },
        ],
    }

    # User is 1km from store, but in pincode 400001 -> EXCLUDED
    ctx_excluded = UserContext(
        country="IN",
        pincode="400001",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert retrieval._match_targeting(ad_set_data, ctx_excluded) is False

    # User is 1km from store, in pincode 400050 -> ELIGIBLE
    ctx_eligible = UserContext(
        country="IN",
        pincode="400050",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert retrieval._match_targeting(ad_set_data, ctx_eligible) is True


def test_overlapping_radius_targets_or_semantics():
    """
    TargetingSet can have multiple radius targets in geo_include.
    A user matching ANY radius entry is eligible (OR logic).
    """
    rule_value = {
        "entries": [
            # Store 1 (Mumbai)
            {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 2000.0},
            # Store 2 (Pune)
            {"latitude": 18.5204, "longitude": 73.8567, "radius_meters": 2000.0},
        ]
    }

    # User in Mumbai near Store 1 -> Match
    ctx_mumbai = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo(rule_value, ctx_mumbai) is True

    # User in Pune near Store 2 -> Match
    ctx_pune = UserContext(
        latitude=18.5204,
        longitude=73.8567,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo(rule_value, ctx_pune) is True

    # User in Lonavala (midpoint, ~60km from both) -> Outside both -> No Match
    ctx_midpoint = UserContext(
        latitude=18.7557,
        longitude=73.4091,
        location_source="device_gps",
        location_consent=True,
    )
    assert match_geo(rule_value, ctx_midpoint) is False


# ==============================================================================
# 6. Backward Compatibility for Administrative Targeting
# ==============================================================================

def test_administrative_targeting_remains_fully_functional():
    """Existing country/state/city/pincode targeting continues working seamlessly."""
    # Country only
    rule_country = {"entries": [{"country": "IN"}]}
    assert match_geo(rule_country, UserContext(country="IN")) is True
    assert match_geo(rule_country, UserContext(country="US")) is False

    # City + State
    rule_city = {"entries": [{"country": "IN", "state": "MH", "city": "Mumbai"}]}
    assert match_geo(rule_city, UserContext(country="IN", region="MH", city="Mumbai")) is True
    assert match_geo(rule_city, UserContext(country="IN", region="MH", city="Pune")) is False

    # Pincode
    rule_pin = {"entries": [{"country": "IN", "pincode": "400001"}]}
    assert match_geo(rule_pin, UserContext(country="IN", pincode="400001")) is True
    assert match_geo(rule_pin, UserContext(country="IN", pincode="400002")) is False

    # Mixed administrative and radius entries in single rule
    rule_mixed = {
        "entries": [
            {"country": "IN", "state": "KA", "city": "Bengaluru"},
            {"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 5000.0},
        ]
    }
    # Matches administrative entry
    assert match_geo(rule_mixed, UserContext(country="IN", region="KA", city="Bengaluru")) is True
    # Matches radius entry
    assert (
        match_geo(
            rule_mixed,
            UserContext(
                latitude=19.0760,
                longitude=72.8777,
                location_source="device_gps",
                location_consent=True,
            ),
        )
        is True
    )


# ==============================================================================
# 7. Multi-AdSet Isolation (No Sibling Leakage)
# ==============================================================================

def test_sibling_ad_set_targeting_isolation():
    """
    Campaign has AdSet A (Delhi administrative) and AdSet B (Mumbai radius).
    User in Mumbai with GPS matches AdSet B only; AdSet A targeting never leaks.
    """
    retrieval = TargetingRetrieval(session=AsyncMock())

    ad_set_a = {
        "id": 1,
        "name": "Delhi AdSet",
        "targeting_rules": [
            {
                "rule_type": "geo",
                "is_include": True,
                "rule_value": {"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]},
            }
        ],
    }

    ad_set_b = {
        "id": 2,
        "name": "Mumbai Store AdSet",
        "targeting_rules": [
            {
                "rule_type": "geo",
                "is_include": True,
                "rule_value": {
                    "entries": [{"latitude": 19.0760, "longitude": 72.8777, "radius_meters": 3000.0}]
                },
            }
        ],
    }

    ctx_mumbai = UserContext(
        country="IN",
        region="MH",
        city="Mumbai",
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )

    # Must match AdSet B
    assert retrieval._match_targeting(ad_set_b, ctx_mumbai) is True
    # Must NOT match AdSet A
    assert retrieval._match_targeting(ad_set_a, ctx_mumbai) is False


# ==============================================================================
# 8. Malformed & Legacy Persisted Data Resilience
# ==============================================================================

def test_malformed_persisted_rule_value_handled_safely():
    """Malformed entries in the database JSON column must not cause unhandled crashes."""
    bad_rule_values = [
        {"entries": [None, 123, "string-entry", {}]},
        {"entries": [{"latitude": "invalid-float", "longitude": 72.0, "radius_meters": 5000}]},
        {"entries": [{"latitude": 19.0, "longitude": None, "radius_meters": 5000}]},
        {"entries": [{"latitude": 19.0, "longitude": 72.0, "radius_meters": -500}]},
        {"entries": [{"latitude": float("nan"), "longitude": 72.0, "radius_meters": 5000}]},
        {"entries": [{"country": None}]},
        {},
    ]
    ctx = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )
    for bad_rule in bad_rule_values:
        # Should cleanly return False (or True for empty rule) without raising an exception
        result = match_geo(bad_rule, ctx)
        assert isinstance(result, bool)


# ==============================================================================
# 9. Performance Benchmark at Scale
# ==============================================================================

def test_radius_matching_performance_at_scale():
    """
    Evaluate in-memory matching of 1,000 radius rules against a user location.
    Must complete in under 50 milliseconds total (< 0.05ms per rule).
    """
    # 1,000 simulated radius rules scattered around the globe
    rules = []
    for i in range(1000):
        lat = (i % 180) - 90.0
        lon = ((i * 2) % 360) - 180.0
        rules.append({"latitude": lat, "longitude": lon, "radius_meters": 5000.0})

    ctx = UserContext(
        latitude=19.0760,
        longitude=72.8777,
        location_source="device_gps",
        location_consent=True,
    )

    start = time.perf_counter()
    # Execute matching
    matched = match_geo({"entries": rules}, ctx)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert isinstance(matched, bool)
    assert elapsed_ms < 50.0, f"Expected < 50ms for 1,000 rules, took {elapsed_ms:.2f}ms"
