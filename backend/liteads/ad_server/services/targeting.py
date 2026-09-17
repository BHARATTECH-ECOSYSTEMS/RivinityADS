"""
Pure targeting matching functions for geo and sectors.
"""

from typing import Any

from liteads.schemas.internal import UserContext


def match_geo(rule_value: dict[str, Any], user_context: UserContext) -> bool:
    """
    Match geo rules against user context.
    
    A user matches the geo targeting if they match ANY of the conditions in the rule.
    Geo matching requires checking country, state, city, and pincode.
    
    rule_value format:
    {"countries": ["IN", "US"], "states": ["MH", "CA"], "cities": ["mumbai", "los angeles"], "pincodes": ["400001", "90210"]}
    (If using GeoEntry structure inside targeting rules)
    Wait, the Phase 1 schema defined TargetingSet with geo_include: list[GeoEntry].
    When converted to TargetingRule in DB, how is it stored?
    According to the implementation plan, targeting rules will be stored in `TargetingRule` with `rule_type="geo"` or `rule_type="sector"`.
    For geo, `rule_value` could be the serialized `GeoEntry`, or we can consolidate them into a single `geo` rule that holds lists of all `GeoEntry` items.
    Let's assume the CRUD API converts `TargetingSet.geo_include` to a single `TargetingRule` with `rule_type="geo", is_include=True`
    and `rule_value` is `{"entries": [{"country": "IN", "city": "mumbai"}, ...]}`.
    """
    
    entries = rule_value.get("entries", [])
    if not entries:
        return True # Empty rules always match, but this shouldn't happen.

    if not user_context.country:
        return False # If we don't know the user's location, they can't match a specific location rule.

    for entry in entries:
        # Check country
        if entry.get("country", "").lower() != user_context.country.lower():
            continue
            
        # Check state (if specified in entry)
        if entry.get("state"):
            if not user_context.region or entry["state"].lower() != user_context.region.lower():
                continue
                
        # Check city
        if entry.get("city"):
            if not user_context.city or entry["city"].lower() != user_context.city.lower():
                continue
                
        # Check pincode
        if entry.get("pincode"):
            if not user_context.pincode or entry["pincode"].lower() != user_context.pincode.lower():
                continue

        # If it passed all specified conditions in this entry, it's a match!
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
