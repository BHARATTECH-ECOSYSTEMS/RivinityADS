"""
Pure targeting matching functions for geo (administrative + hyperlocal radius) and sectors.
"""

import math
from typing import Any

from liteads.schemas.internal import UserContext

# Mean Earth radius in meters under WGS 84
EARTH_RADIUS_METERS: float = 6_371_000.0

# 1 degree of latitude is approximately 111,195 meters.
# 111,000 meters provides a conservative lower bound for bounding-box pre-filtering.
METERS_PER_DEGREE_LAT_CONSERVATIVE: float = 111_000.0


def calculate_haversine_distance_meters(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    Calculate the great-circle distance between two points on the WGS 84 sphere
    using the Haversine formula.

    Robustness guarantees:
    - Normalizes longitude differences to [-pi, +pi] to safely handle Antimeridian crossings (+180/-180).
    - Clamps intermediate chord length squared 'a' to [0.0, 1.0] to prevent floating-point overshoot.
    - Handles polar singularities and high-latitude convergence smoothly.
    - Returns distance in meters.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)

    # Normalize dlambda across the antimeridian to [-pi, pi]
    dlambda = math.radians(lon2 - lon1)
    dlambda = (dlambda + math.pi) % (2.0 * math.pi) - math.pi

    sin_half_dphi = math.sin(dphi / 2.0)
    sin_half_dlambda = math.sin(dlambda / 2.0)

    a = (sin_half_dphi * sin_half_dphi) + (
        math.cos(phi1) * math.cos(phi2) * sin_half_dlambda * sin_half_dlambda
    )
    # Clamp to avoid floating point domain error in math.sqrt(1.0 - a)
    a_clamped = max(0.0, min(1.0, a))

    c = 2.0 * math.atan2(math.sqrt(a_clamped), math.sqrt(1.0 - a_clamped))
    return EARTH_RADIUS_METERS * c


def is_location_eligible_for_radius(
    user_context: UserContext,
    target_radius_meters: float,
) -> tuple[bool, str]:
    """
    Evaluates whether the user's location signal satisfies accuracy and privacy-consent
    standards to participate in radius geofence matching.

    Guarantees:
    1. Coordinates must exist and be finite numbers in valid WGS 84 ranges.
    2. IP Centroid Guard: IP resolution is coarse (~10-50km) and cannot match radius fences.
    3. User Consent Guard: Device coordinates require explicit opt-in consent.
    4. Location Accuracy Guard: If reported uncertainty radius > target radius, fail closed.
    5. Authorized Source Guard: Source must be a verified device/GPS signal.
    """
    # 1. Coordinate presence
    if user_context.latitude is None or user_context.longitude is None:
        return False, "Coordinates missing"

    # 2. Finite number check (reject NaN, Inf)
    if not (math.isfinite(user_context.latitude) and math.isfinite(user_context.longitude)):
        return False, "Coordinates non-finite"

    # 3. WGS 84 range check
    if not (-90.0 <= user_context.latitude <= 90.0) or not (-180.0 <= user_context.longitude <= 180.0):
        return False, "Coordinates out of bounds"

    # 4. IP Centroid Guard: Never treat an IP-derived city centroid as precise GPS
    source = (user_context.location_source or "").lower().strip()
    if source == "ip":
        return False, "IP-derived centroid cannot match radius geofence"

    # 5. User Consent Guard: Device coordinates require explicit opt-in consent
    if not user_context.location_consent:
        return False, "Missing user consent for device geolocation processing"

    # 6. Location Accuracy / Uncertainty Guard:
    # If reported accuracy radius is worse than the target radius, fail closed
    if user_context.location_accuracy_meters is not None:
        if not math.isfinite(user_context.location_accuracy_meters) or user_context.location_accuracy_meters < 0:
            return False, "Invalid location accuracy value"
        if user_context.location_accuracy_meters > target_radius_meters:
            return (
                False,
                f"Location accuracy ({user_context.location_accuracy_meters}m) is coarser than target radius ({target_radius_meters}m)",
            )

    # 7. Valid authorized source check
    valid_precise_sources = {"device_gps", "device_request", "gps", "precise"}
    if source and source not in valid_precise_sources:
        if user_context.location_accuracy_meters is None or user_context.location_accuracy_meters > target_radius_meters:
            return False, f"Untrusted or insufficient location source: {source}"

    return True, "Eligible"


def match_radius_entry(entry: dict[str, Any], user_context: UserContext) -> bool:
    """
    Match a single radius targeting entry against the user context.

    Boundary rule: distance_meters <= radius_meters (inclusive).
    """
    target_lat = entry.get("latitude")
    target_lon = entry.get("longitude")
    target_radius = entry.get("radius_meters")

    if target_lat is None or target_lon is None or target_radius is None:
        return False

    try:
        target_lat = float(target_lat)
        target_lon = float(target_lon)
        target_radius = float(target_radius)
    except (ValueError, TypeError):
        return False

    if not (math.isfinite(target_lat) and math.isfinite(target_lon) and math.isfinite(target_radius)):
        return False
    if not (-90.0 <= target_lat <= 90.0) or not (-180.0 <= target_lon <= 180.0) or target_radius <= 0:
        return False

    # Check location signal eligibility (accuracy, consent, source != ip)
    is_eligible, _ = is_location_eligible_for_radius(user_context, target_radius)
    if not is_eligible:
        return False

    # Check optional country scope if specified on the target
    target_country = entry.get("country")
    if target_country:
        target_country_clean = str(target_country).strip().upper()
        user_country_clean = str(user_context.country or "").strip().upper()
        if not user_country_clean or user_country_clean != target_country_clean:
            return False

    assert user_context.latitude is not None
    assert user_context.longitude is not None

    # Bounding-box latitude pre-filter (fast rejection of far-away points)
    delta_lat_max = target_radius / METERS_PER_DEGREE_LAT_CONSERVATIVE
    if abs(user_context.latitude - target_lat) > (delta_lat_max + 1e-6):
        return False

    # Calculate exact great-circle distance
    distance = calculate_haversine_distance_meters(
        user_context.latitude,
        user_context.longitude,
        target_lat,
        target_lon,
    )

    # Documented boundary rule: inclusive matching (distance <= radius)
    return distance <= (target_radius + 1e-6)


def match_administrative_entry(entry: dict[str, Any], user_context: UserContext) -> bool:
    """
    Match a single administrative geographic entry (country, state, city, pincode).
    """
    if not user_context.country:
        return False

    target_country = entry.get("country")
    if not target_country:
        return False

    if str(target_country).strip().lower() != str(user_context.country).strip().lower():
        return False

    # Check state (if specified in entry)
    if entry.get("state"):
        if not user_context.region or str(entry["state"]).strip().lower() != str(user_context.region).strip().lower():
            return False

    # Check city (if specified in entry)
    if entry.get("city"):
        if not user_context.city or str(entry["city"]).strip().lower() != str(user_context.city).strip().lower():
            return False

    # Check pincode (if specified in entry)
    if entry.get("pincode"):
        if not user_context.pincode or str(entry["pincode"]).strip().lower() != str(user_context.pincode).strip().lower():
            return False

    return True


def match_geo(rule_value: dict[str, Any], user_context: UserContext) -> bool:
    """
    Match geo rules against user context.

    A user matches the geo targeting if they match ANY condition in the rule entries.
    An entry can be:
    - Administrative: country, state?, city?, pincode?
    - Hyperlocal Radius: latitude, longitude, radius_meters, country?
    """
    entries = rule_value.get("entries", [])
    if not entries:
        return True  # Empty rules always match

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        # --- A. Radius Target Entry ---
        if entry.get("radius_meters") is not None or entry.get("latitude") is not None:
            if match_radius_entry(entry, user_context):
                return True
            continue

        # --- B. Administrative Target Entry ---
        if match_administrative_entry(entry, user_context):
            return True

    return False


def match_sector(rule_value: dict[str, Any], user_context: UserContext) -> bool:
    """
    Match sector rules against user context.

    rule_value: {"sectors": ["fintech", "ecommerce"]}
    """
    target_sectors = rule_value.get("sectors", [])
    if not target_sectors:
        return True

    if not user_context.sectors:
        return False

    user_sectors_lower = {s.lower() for s in user_context.sectors}
    for sector in target_sectors:
        if sector.lower() in user_sectors_lower:
            return True

    return False

