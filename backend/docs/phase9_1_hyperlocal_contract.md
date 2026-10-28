# RivinityADS — Phase 9.1 Design Note: Hyperlocal Geo-Targeting Contract

**Document Version:** 1.0  
**Phase:** 9.1 (Backend/Domain Contract & Validation)  
**Status:** Implemented & Verified  
**Scope Boundary:** Backend domain and schema contract only. Matching-engine execution is deferred to Phase 9.2. Frontend UI is deferred to Phase 9.3.

---

## 1. Executive Summary

Phase 9.1 establishes the validated domain and schema contract for **hyperlocal radius targeting** in RivinityADS. Prior to this phase, geographic targeting only supported administrative granularities (`country`, `state`, `city`, and `pincode`), while ad request IP resolution returned approximate city/metro centroids.

This phase introduces:
1. An additive, backward-compatible extension to `GeoEntry` supporting circular radius geofences (`latitude`, `longitude`, `radius_meters`, `radius_unit`, and an optional label `name`).
2. Strict coordinate and radius range validation (-90° to +90° lat, -180° to +180° lon, 100m to 100km radius).
3. Explicit mutual exclusivity rules preventing ambiguous combinations (e.g., mixing radius with city, state, or pincode).
4. Deterministic deduplication and conflict validation in `TargetingSet` (detecting direct include/exclude collisions).
5. Explicit location provenance schemas (`LocationSource` and `LocationQualityIndicator`) to safeguard privacy and prevent coarse IP centroids from masquerading as precise GPS.
6. A boundary guard in `match_geo` ensuring that radius targets are never falsely matched across an entire country before the Phase 9.2 matching engine is implemented.

---

## 2. Geo-Targeting Contract Specification

### 2.1 Domain & Schema Definition (`liteads.schemas.targeting`)

```python
class RadiusUnit(str, Enum):
    METERS = "meters"
    KILOMETERS = "km"
    MILES = "miles"

class GeoTargetType(str, Enum):
    ADMINISTRATIVE = "administrative"
    RADIUS = "radius"

class GeoEntry(BaseModel):
    # Administrative targeting fields
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 country code")
    state: str | None = Field(default=None, description="State ISO code or name")
    city: str | None = Field(default=None, description="City name")
    pincode: str | None = Field(default=None, description="Postal / ZIP code")

    # Hyperlocal radius targeting fields (Phase 9.1)
    latitude: float | None = Field(default=None, description="Center latitude in decimal degrees (WGS 84, -90.0 to 90.0)")
    longitude: float | None = Field(default=None, description="Center longitude in decimal degrees (WGS 84, -180.0 to 180.0)")
    radius_meters: float | None = Field(default=None, description="Targeting radius in meters (100.0 to 100,000.0 meters)")
    radius_unit: RadiusUnit | None = Field(default=None, description="Explicit distance unit, defaults to 'meters' for radius")
    name: str | None = Field(default=None, description="Optional human-readable label (e.g. 'Downtown Flagship Store')")
```

### 2.2 Validation Invariants

| Dimension | Rule | Justification & Failure Mode |
|---|---|---|
| **Latitude** | `-90.0 <= latitude <= 90.0` (Finite float) | Standard WGS 84 coordinate space. Rejects `NaN`, `Inf`, and out-of-bounds with `ValueError`. |
| **Longitude** | `-180.0 <= longitude <= 180.0` (Finite float) | Standard WGS 84 coordinate space. Rejects `NaN`, `Inf`, and out-of-bounds with `ValueError`. |
| **Completeness** | `latitude`, `longitude`, `radius_meters` must all be specified if any one is present | Partial definitions (e.g. lat without lon, or center without radius) are undefined geometries. |
| **Minimum Radius** | `radius_meters >= 100.0` | Mobile GPS receivers have standard outdoor errors of 5–15m and urban canyon reflections of 20–50m. Fences under 100m create erratic delivery instability. |
| **Maximum Radius** | `radius_meters <= 100,000.0` (100 km) | A 100km radius circle covers ~31,416 km² (larger than Greater London, Tokyo, or Delhi NCR). Beyond 100km, spherical distortion increases and circles cross political borders uncontrollably. Advertisers must use administrative targeting (`city`, `state`, `country`) for broader scopes. |
| **Exclusivity** | If radius is set, `state`, `city`, and `pincode` MUST be `None` | Prevents semantic ambiguity: combining a 500m radius with a city creates contradictory "AND" vs "OR" interpretations. |
| **Optional Country** | If country is provided on radius target, it must be valid ISO 3166-1 alpha-2 | Allows geographic scoping and verification, while normalizing case (e.g. `"in"` → `"IN"`). |
| **Administrative Fallback** | If radius is NOT set, `country` is strictly required | Preserves existing consistency rules (`city` requires `state`; `pincode` cannot mix with `city`/`state`). |

### 2.3 TargetingSet Deduplication & Conflict Resolution

In `TargetingSet`:
1. **Intra-list Deduplication:** A canonical key (`admin:{country}:{state}:{city}:{pincode}` or `radius:{country}:{lat}:{lon}:{radius}`) detects and rejects duplicate targets in `geo_include` or `geo_exclude` with HTTP 422.
2. **Direct Contradiction:** If the exact same target exists in both `geo_include` and `geo_exclude`, the request is rejected with `ValueError("Conflicting targeting rule...")`.
3. **Exclusion Precedence:** During serving (Phase 9.2), any match on `geo_exclude` immediately disqualifies the candidate, regardless of `geo_include`.

---

## 3. Location Precision and Privacy Architecture

### 3.1 Provenance Hierarchy (`LocationSource`)

```
+-------------------------------------------------------------------------+
| Provenance        | Typical Accuracy | Privacy / Consent Requirement    |
+-------------------------------------------------------------------------+
| DEVICE_GPS        | 5m – 50m         | Explicit user consent mandatory  |
| COARSE_NETWORK    | 500m – 3,000m    | Network/cell permission required |
| IP (MaxMind DB)   | 10km – 50km      | Coarse approximate centroid only |
| DERIVED_ADMIN     | Municipal bounds | Inferred from postal/city table  |
| UNKNOWN           | Undefined        | Location unavailable             |
+-------------------------------------------------------------------------+
```

### 3.2 Privacy & Consent Safeguards

1. **IP Coarse Centroid Guard:** MaxMind GeoIP lookups produce city or regional centroids. **The platform strictly forbids treating IP-derived coordinates as precise GPS.** In `LocationQualityIndicator`, setting `source=LocationSource.IP` automatically marks `is_approximate=True`, sets `accuracy_radius_meters >= 10,000m`, and disallows `has_user_consent`.
2. **No Unconsented Precise Tracking:** Device GPS coordinates supplied by an upstream client or publisher SDK require an explicit opt-in indicator (`has_user_consent=True`). If consent is absent, the signal is downgraded to approximate.
3. **Data Minimization:** Exact latitude and longitude must never be logged in raw impression logs, click logs, or telemetry tables. Only derived geographic identifiers (e.g. country, region, city) or geohash-truncated cells (precision <= 4, ~20km) may be retained for analytics.
4. **Bounded Retention:** Real-time coordinates in `UserContext` exist solely in-memory during the ad request pipeline and are discarded upon response completion.

---

## 4. Phase 9.1 Boundary Guard & Ad Delivery Behavior

In Phase 9.1, **radius-based ad delivery is NOT yet claimed to be active**.
To prevent false matches, `match_geo()` in `liteads.ad_server.services.targeting` implements an explicit boundary guard:
```python
for entry in entries:
    # Phase 9.1 Boundary Guard:
    # Radius targeting matching engine is scheduled for Phase 9.2.
    # If an entry is a radius target, do NOT fall back to matching on country alone!
    if entry.get("radius_meters") is not None or entry.get("latitude") is not None:
        continue
```
- If an ad set specifies a radius target, it will **not** match users simply because they reside in the same country.
- If all entries in `geo_include` are radius entries, `match_geo` returns `False` until Phase 9.2 activates the distance matching engine.
- Existing administrative entries in mixed sets continue to match as normal.

---

## 5. Database & Migration Impact

- **Schema Changes:** None.
- **Persistence Verification:** Targeting rules are persisted in `TargetingRule.rule_value` (`JSON` column). Pydantic serializes `GeoEntry.model_dump(exclude_none=True)` into `rule_value["entries"]`.
- When loading rules, `GeoEntry(**entry)` hydrates the validated model seamlessly.
- **No Alembic migrations were required or introduced for Phase 9.1.** The migration head remains `k3c4d5e6f7a8`.

---

## 6. Follow-up Roadmap for Phase 9.2 and Phase 9.3

### Phase 9.2: Matching Engine Implementation (Backend)
- [ ] Implement Haversine / Great-Circle distance calculation in `liteads.ad_server.services.targeting`.
- [ ] Support Antimeridian wrap-around (`abs(lon1 - lon2) > 180`) and polar latitude degradation.
- [ ] Define boundary semantics: `distance_meters <= radius_meters` (inclusive boundary).
- [ ] Evaluate spatial indexing (PostGIS `ST_DWithin` with geography type if deployment enables it, or bounding-box pre-filtering `[lat - delta, lat + delta]`).
- [ ] Gate radius matching on `LocationQualityIndicator.has_user_consent` and `accuracy_radius_meters <= target_radius_meters`.
- [ ] Performance benchmark matching latency at 1,000 active campaigns.

### Phase 9.3: Frontend UI & Advertiser Experience
- [ ] Add radius targeting mode in `frontend/src/pages/advertiser/wizard/AdSetsStep.tsx` and `CampaignTargeting.tsx`.
- [ ] Support coordinate inputs (Latitude, Longitude) and radius slider (100m to 100km).
- [ ] Optional text-based location search / geocoding resolver.
- [ ] Isolate map provider if interactive pin-drop is added (no hardcoded paid Google Maps dependency).
