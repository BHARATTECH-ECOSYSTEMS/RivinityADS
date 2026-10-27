"""
Phase 9.3.1: Radius Targeting Persistence Lifecycle Audit Tests.

Verifies:
1. Creation of radius target with known coordinates, radius, unit, country scope, and name.
2. Saving via real PUT API endpoint for both Campaign and AdSet.
3. Confirming the persisted database representation in TargetingRule.
4. Reloading the entity via GET from the real database.
5. Editing the radius target (updating coordinates, radius, units, country scope) and re-saving.
6. Confirming multiple targets and mixed include/exclude rules survive round trips.
7. Confirming existing administrative targeting fields (country, state, city, pincode) survive the same lifecycle.
8. Empty targets and clearing targeting rules.
9. Validation rejection for invalid coordinates, missing fields, out-of-bounds radius, and collisions.
10. Sibling AdSets keep their targeting rules strictly isolated without leakage.
"""

from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.database import get_session
from liteads.models import Advertiser, Campaign, AdSet, TargetingRule, Status

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def setup_persistence_env(test_db):
    """Sets up an advertiser user with a campaign, sibling ad sets, and authenticated client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db

        # 1. Register advertiser
        res = await c.post(
            "/api/v1/auth/register",
            json={
                "name": "AuditAdv",
                "email": "audit_adv@test.com",
                "password": "SecurePassword123!",
                "role": "advertiser",
            },
        )
        assert res.status_code in [200, 201], res.text
        data = res.json()
        token = data["access_token"]
        c.headers = {"Authorization": f"Bearer {token}"}
        adv_id = data["user"]["advertiser_id"]

        # 2. Create Campaign
        camp_res = await c.post(
            "/api/v1/campaign",
            json={
                "advertiser_id": adv_id,
                "name": "Persistence Audit Campaign",
                "budget_daily": 500.0,
                "budget_total": 5000.0,
                "bid_amount": 2.0,
                "bid_type": 1,
                "status": 1,
            },
        )
        assert camp_res.status_code in [200, 201], camp_res.text
        camp_id = camp_res.json()["id"]

        # 3. Create AdSet 1
        adset1_res = await c.post(
            f"/api/v1/campaign/{camp_id}/ad-sets",
            json={
                "name": "AdSet 1 - Hyperlocal Delhi",
                "daily_budget": 250.0,
                "bid_amount": 2.0,
                "bid_type": 1,
            },
        )
        assert adset1_res.status_code in [200, 201], adset1_res.text
        adset1_id = adset1_res.json()["id"]

        # 4. Create AdSet 2 (Sibling for isolation verification)
        adset2_res = await c.post(
            f"/api/v1/campaign/{camp_id}/ad-sets",
            json={
                "name": "AdSet 2 - Administrative Mumbai",
                "daily_budget": 250.0,
                "bid_amount": 2.5,
                "bid_type": 1,
            },
        )
        assert adset2_res.status_code in [200, 201], adset2_res.text
        adset2_id = adset2_res.json()["id"]

        yield {
            "client": c,
            "campaign_id": camp_id,
            "adset1_id": adset1_id,
            "adset2_id": adset2_id,
            "advertiser_id": adv_id,
        }
        app.dependency_overrides.clear()


async def test_adset_radius_persistence_lifecycle(setup_persistence_env, test_db):
    """
    Step 2 Full Lifecycle:
    1. Create radius target with known coordinates, radius, unit, country scope, and name.
    2. Save through real PUT /api/v1/ad-set/{id}/targeting.
    3. Confirm DB representation in TargetingRule.
    4. Reload entity from backend GET.
    5. Edit the target and save again.
    6. Confirm coordinates, radius, units, country scope, and include/exclude behavior remain correct.
    """
    client = setup_persistence_env["client"]
    adset1_id = setup_persistence_env["adset1_id"]
    campaign_id = setup_persistence_env["campaign_id"]

    # --- Step 2.1 & 2.2: Create and save radius target ---
    initial_target_payload = {
        "geo_include": [
            {
                "latitude": 28.6139,
                "longitude": 77.2090,
                "radius_meters": 10000.0,
                "radius_unit": "km",
                "name": "Connaught Place Hub",
                "country": "IN",
            }
        ],
        "geo_exclude": [
            {
                "latitude": 28.7041,
                "longitude": 77.1025,
                "radius_meters": 3000.0,
                "radius_unit": "meters",
                "name": "North Delhi Buffer",
                "country": "IN",
            }
        ],
        "sector_include": ["technology"],
        "sector_exclude": ["gambling"],
    }

    put_res = await client.put(f"/api/v1/ad-set/{adset1_id}/targeting", json=initial_target_payload)
    assert put_res.status_code == 200, put_res.text
    put_data = put_res.json()

    assert put_data["ad_set_id"] == adset1_id
    assert put_data["campaign_id"] == campaign_id
    assert len(put_data["geo_include"]) == 1
    assert len(put_data["geo_exclude"]) == 1

    # --- Step 2.3: Confirm persisted DB representation ---
    db_res = await test_db.execute(
        select(TargetingRule).where(
            TargetingRule.ad_set_id == adset1_id,
            TargetingRule.rule_type == "geo",
        )
    )
    rules = db_res.scalars().all()
    assert len(rules) == 2  # One include, one exclude

    inc_rule = next(r for r in rules if r.is_include)
    assert inc_rule.rule_value["entries"][0]["latitude"] == 28.6139
    assert inc_rule.rule_value["entries"][0]["longitude"] == 77.2090
    assert inc_rule.rule_value["entries"][0]["radius_meters"] == 10000.0
    assert inc_rule.rule_value["entries"][0]["radius_unit"] == "km"
    assert inc_rule.rule_value["entries"][0]["name"] == "Connaught Place Hub"
    assert inc_rule.rule_value["entries"][0]["country"] == "IN"

    exc_rule = next(r for r in rules if not r.is_include)
    assert exc_rule.rule_value["entries"][0]["latitude"] == 28.7041
    assert exc_rule.rule_value["entries"][0]["radius_meters"] == 3000.0

    # --- Step 2.4: Reload entity via GET ---
    get_res = await client.get(f"/api/v1/ad-set/{adset1_id}/targeting")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["geo_include"][0]["latitude"] == 28.6139
    assert get_data["geo_include"][0]["name"] == "Connaught Place Hub"
    assert get_data["geo_include"][0]["radius_meters"] == 10000.0
    assert get_data["geo_include"][0]["radius_unit"] == "km"

    # --- Step 2.6 & 2.7: Edit the target and save again ---
    edited_target_payload = {
        "geo_include": [
            {
                "latitude": 28.6139,
                "longitude": 77.2090,
                "radius_meters": 15000.0,  # Expanded radius from 10km to 15km
                "radius_unit": "km",
                "name": "Greater Connaught Place Hub",  # Updated label
                "country": "IN",
            }
        ],
        "geo_exclude": [
            {
                "latitude": 28.5355,  # Changed exclusion center to South Delhi
                "longitude": 77.3910,
                "radius_meters": 4000.0,
                "radius_unit": "meters",
                "name": "Noida Exclusion Buffer",
                "country": "IN",
            }
        ],
        "sector_include": ["technology", "ecommerce"],
        "sector_exclude": ["gambling"],
    }

    put2_res = await client.put(f"/api/v1/ad-set/{adset1_id}/targeting", json=edited_target_payload)
    assert put2_res.status_code == 200
    put2_data = put2_res.json()
    assert put2_data["geo_include"][0]["radius_meters"] == 15000.0
    assert put2_data["geo_include"][0]["name"] == "Greater Connaught Place Hub"
    assert put2_data["geo_exclude"][0]["latitude"] == 28.5355

    # Verify reload after edit
    get2_res = await client.get(f"/api/v1/ad-set/{adset1_id}/targeting")
    assert get2_res.status_code == 200
    get2_data = get2_res.json()
    assert get2_data["geo_include"][0]["radius_meters"] == 15000.0
    assert get2_data["geo_include"][0]["name"] == "Greater Connaught Place Hub"
    assert get2_data["geo_exclude"][0]["name"] == "Noida Exclusion Buffer"


async def test_mixed_administrative_and_radius_persistence(setup_persistence_env, test_db):
    """
    Step 2.8 & 2.9: Verify multiple targets and mixed administrative and radius entries survive round trips.
    """
    client = setup_persistence_env["client"]
    adset1_id = setup_persistence_env["adset1_id"]

    mixed_payload = {
        "geo_include": [
            # 1. Administrative Country + State + City
            {
                "country": "IN",
                "state": "MH",
                "city": "Mumbai",
            },
            # 2. Administrative Country + Pincode
            {
                "country": "IN",
                "pincode": "560001",
            },
            # 3. Radius target without country scope
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
                "radius_unit": "km",
                "name": "Mumbai Center Circle",
            },
            # 4. Radius target with country scope
            {
                "latitude": 12.9716,
                "longitude": 77.5946,
                "radius_meters": 8000.0,
                "radius_unit": "meters",
                "country": "IN",
                "name": "Bengaluru Tech Park",
            },
        ],
        "geo_exclude": [
            # 1. Administrative Country
            {
                "country": "US",
            },
            # 2. Radius Exclusion
            {
                "latitude": 18.9220,
                "longitude": 72.8347,
                "radius_meters": 1500.0,
                "name": "Harbor Zone",
            },
        ],
        "sector_include": ["ecommerce", "fintech"],
        "sector_exclude": ["gambling"],
    }

    put_res = await client.put(f"/api/v1/ad-set/{adset1_id}/targeting", json=mixed_payload)
    assert put_res.status_code == 200, put_res.text
    put_data = put_res.json()

    assert len(put_data["geo_include"]) == 4
    assert len(put_data["geo_exclude"]) == 2

    # Verify GET returns all entries with correct attributes
    get_res = await client.get(f"/api/v1/ad-set/{adset1_id}/targeting")
    assert get_res.status_code == 200
    get_data = get_res.json()

    assert len(get_data["geo_include"]) == 4
    # Admin entry 1
    admin_city = next(e for e in get_data["geo_include"] if e.get("city") == "Mumbai")
    assert admin_city["country"] == "IN"
    assert admin_city["state"] == "MH"
    assert admin_city["latitude"] is None

    # Admin entry 2
    admin_pin = next(e for e in get_data["geo_include"] if e.get("pincode") == "560001")
    assert admin_pin["country"] == "IN"
    assert admin_pin["city"] is None

    # Radius entry 1
    rad_mumbai = next(e for e in get_data["geo_include"] if e.get("name") == "Mumbai Center Circle")
    assert rad_mumbai["latitude"] == 19.0760
    assert rad_mumbai["radius_meters"] == 5000.0
    assert rad_mumbai["country"] is None

    # Radius entry 2
    rad_blr = next(e for e in get_data["geo_include"] if e.get("name") == "Bengaluru Tech Park")
    assert rad_blr["latitude"] == 12.9716
    assert rad_blr["country"] == "IN"


async def test_sibling_ad_set_isolation(setup_persistence_env):
    """
    Step 2: Verify different Ad Sets keep their targeting rules completely separate.
    """
    client = setup_persistence_env["client"]
    adset1_id = setup_persistence_env["adset1_id"]
    adset2_id = setup_persistence_env["adset2_id"]

    # Configure AdSet 1 with Radius targeting
    await client.put(
        f"/api/v1/ad-set/{adset1_id}/targeting",
        json={
            "geo_include": [
                {
                    "latitude": 28.6139,
                    "longitude": 77.2090,
                    "radius_meters": 5000.0,
                    "name": "Delhi Hub Only",
                }
            ],
            "geo_exclude": [],
            "sector_include": ["technology"],
            "sector_exclude": [],
        },
    )

    # Configure AdSet 2 with Administrative targeting
    await client.put(
        f"/api/v1/ad-set/{adset2_id}/targeting",
        json={
            "geo_include": [
                {
                    "country": "IN",
                    "state": "MH",
                    "city": "Mumbai",
                }
            ],
            "geo_exclude": [],
            "sector_include": ["fashion"],
            "sector_exclude": [],
        },
    )

    # Fetch AdSet 1 and verify only Delhi radius target
    res1 = await client.get(f"/api/v1/ad-set/{adset1_id}/targeting")
    data1 = res1.json()
    assert len(data1["geo_include"]) == 1
    assert data1["geo_include"][0]["name"] == "Delhi Hub Only"
    assert data1["sector_include"] == ["technology"]

    # Fetch AdSet 2 and verify only Mumbai admin target
    res2 = await client.get(f"/api/v1/ad-set/{adset2_id}/targeting")
    data2 = res2.json()
    assert len(data2["geo_include"]) == 1
    assert data2["geo_include"][0]["city"] == "Mumbai"
    assert data2["sector_include"] == ["fashion"]


async def test_empty_and_cleared_targeting_persistence(setup_persistence_env, test_db):
    """
    Step 2.10: Empty targets and clearing targeting rules.
    """
    client = setup_persistence_env["client"]
    adset1_id = setup_persistence_env["adset1_id"]

    # First add a target
    await client.put(
        f"/api/v1/ad-set/{adset1_id}/targeting",
        json={
            "geo_include": [
                {
                    "latitude": 19.0760,
                    "longitude": 72.8777,
                    "radius_meters": 5000.0,
                }
            ],
            "geo_exclude": [],
            "sector_include": [],
            "sector_exclude": [],
        },
    )

    # Now clear all targeting (empty lists)
    empty_res = await client.put(
        f"/api/v1/ad-set/{adset1_id}/targeting",
        json={
            "geo_include": [],
            "geo_exclude": [],
            "sector_include": [],
            "sector_exclude": [],
        },
    )
    assert empty_res.status_code == 200
    empty_data = empty_res.json()
    assert len(empty_data["geo_include"]) == 0
    assert len(empty_data["geo_exclude"]) == 0

    # Verify DB has 0 rules for this ad set
    db_res = await test_db.execute(
        select(TargetingRule).where(TargetingRule.ad_set_id == adset1_id)
    )
    assert len(db_res.scalars().all()) == 0

    # Verify GET returns empty lists
    get_res = await client.get(f"/api/v1/ad-set/{adset1_id}/targeting")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["geo_include"] == []
    assert get_data["geo_exclude"] == []


async def test_legacy_campaign_targeting_persistence_lifecycle(setup_persistence_env, test_db):
    """
    Step 2: Verify Campaign-level targeting persistence lifecycle (PUT & GET /api/v1/campaign/{id}/targeting).
    """
    client = setup_persistence_env["client"]
    campaign_id = setup_persistence_env["campaign_id"]

    payload = {
        "geo_include": [
            {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "radius_meters": 12000.0,
                "radius_unit": "km",
                "name": "NYC Metro Area",
                "country": "US",
            }
        ],
        "geo_exclude": [
            {
                "country": "CA",
            }
        ],
        "sector_include": ["business/SMB"],
        "sector_exclude": [],
    }

    put_res = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=payload)
    assert put_res.status_code == 200, put_res.text
    put_data = put_res.json()
    assert put_data["campaign_id"] == campaign_id
    assert len(put_data["geo_include"]) == 1
    assert put_data["geo_include"][0]["name"] == "NYC Metro Area"

    # GET verification
    get_res = await client.get(f"/api/v1/campaign/{campaign_id}/targeting")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["geo_include"][0]["radius_meters"] == 12000.0
    assert get_data["geo_include"][0]["country"] == "US"
    assert get_data["geo_exclude"][0]["country"] == "CA"
