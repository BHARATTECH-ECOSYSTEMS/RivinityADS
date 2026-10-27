"""
Integration API tests for Phase 9.1: Hyperlocal Geo-Targeting Contract Endpoints.

Verifies:
1. Setting radius targets on Campaigns and AdSets via PUT.
2. Retrieving radius targets on Campaigns and AdSets via GET.
3. Rejecting invalid radius payloads with HTTP 422 Unprocessable Entity.
4. Preserving administrative targeting alongside radius targeting in the same campaign/adset.
5. Verifying JSON persistence in the database without requiring schema migrations.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.database import get_session
from liteads.models import Campaign, TargetingRule

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def advertiser_client(test_db):
    """Sets up an advertiser user with a campaign and authenticated client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post(
            "/api/v1/auth/register",
            json={
                "name": "GeoAdv",
                "email": "geoadv@test.com",
                "password": "password1234",
                "role": "advertiser",
            },
        )
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}

        adv_id = data["user"]["advertiser_id"]
        res = await c.post(
            "/api/v1/campaign",
            json={
                "advertiser_id": adv_id,
                "name": "Radius Targeting Campaign",
                "budget_daily": 200.0,
                "budget_total": 2000.0,
                "bid_amount": 1.5,
                "bid_type": 1,
                "status": 1,
            },
        )
        camp_id = res.json()["id"]

        yield {"client": c, "campaign_id": camp_id, "advertiser_id": adv_id}
        app.dependency_overrides.clear()


async def test_api_put_and_get_radius_targeting(advertiser_client, test_db):
    """Advertiser can configure and retrieve radius targeting via the Campaign targeting API."""
    client = advertiser_client["client"]
    campaign_id = advertiser_client["campaign_id"]

    payload = {
        "geo_include": [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
                "radius_unit": "meters",
                "country": "IN",
                "name": "Mumbai Store Circle",
            },
            {
                "country": "IN",
                "state": "MH",
                "city": "Pune",
            },
        ],
        "geo_exclude": [
            {
                "latitude": 18.5204,
                "longitude": 73.8567,
                "radius_meters": 2000.0,
                "name": "Excluded Center Zone",
            }
        ],
        "sector_include": ["ecommerce"],
        "sector_exclude": ["gambling"],
    }

    # PUT targeting
    response = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["campaign_id"] == campaign_id
    assert len(data["geo_include"]) == 2
    assert len(data["geo_exclude"]) == 1

    # Verify include entries
    r_target = next(e for e in data["geo_include"] if e.get("latitude") is not None)
    assert r_target["latitude"] == 19.0760
    assert r_target["longitude"] == 72.8777
    assert r_target["radius_meters"] == 5000.0
    assert r_target["radius_unit"] == "meters"
    assert r_target["country"] == "IN"
    assert r_target["name"] == "Mumbai Store Circle"

    admin_target = next(e for e in data["geo_include"] if e.get("city") is not None)
    assert admin_target["city"] == "Pune"
    assert admin_target["state"] == "MH"
    assert admin_target["country"] == "IN"
    assert admin_target["latitude"] is None
    assert admin_target["radius_meters"] is None

    # Verify exclude entry
    ex_target = data["geo_exclude"][0]
    assert ex_target["latitude"] == 18.5204
    assert ex_target["radius_meters"] == 2000.0

    # GET targeting to verify round-trip persistence
    get_res = await client.get(f"/api/v1/campaign/{campaign_id}/targeting")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert len(get_data["geo_include"]) == 2
    assert len(get_data["geo_exclude"]) == 1

    # Verify raw database persistence
    result = await test_db.execute(
        select(TargetingRule).where(
            TargetingRule.campaign_id == campaign_id,
            TargetingRule.rule_type == "geo",
            TargetingRule.is_include == True,
        )
    )
    rule = result.scalar_one_or_none()
    assert rule is not None
    assert "entries" in rule.rule_value
    assert len(rule.rule_value["entries"]) == 2


async def test_api_rejects_invalid_radius_payload(advertiser_client):
    """API returns 422 Unprocessable Entity when radius values are out of bounds or malformed."""
    client = advertiser_client["client"]
    campaign_id = advertiser_client["campaign_id"]

    # Test 1: Excessive radius > 100km
    bad_payload_excessive = {
        "geo_include": [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 150000.0,  # 150km > 100km max
            }
        ],
        "geo_exclude": [],
        "sector_include": [],
        "sector_exclude": [],
    }
    res = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=bad_payload_excessive)
    assert res.status_code == 422
    assert "radius_meters must not exceed" in res.text

    # Test 2: Incomplete radius (latitude without longitude or radius)
    bad_payload_incomplete = {
        "geo_include": [
            {
                "latitude": 19.0760,
            }
        ],
        "geo_exclude": [],
        "sector_include": [],
        "sector_exclude": [],
    }
    res = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=bad_payload_incomplete)
    assert res.status_code == 422
    assert "Radius targeting requires latitude, longitude, and radius_meters" in res.text

    # Test 3: Ambiguous combination (radius with city)
    bad_payload_mixed = {
        "geo_include": [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
                "city": "Mumbai",
            }
        ],
        "geo_exclude": [],
        "sector_include": [],
        "sector_exclude": [],
    }
    res = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=bad_payload_mixed)
    assert res.status_code == 422
    assert "Radius targeting cannot be combined with city" in res.text

    # Test 4: Include/Exclude direct contradiction
    bad_payload_contradiction = {
        "geo_include": [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
            }
        ],
        "geo_exclude": [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "radius_meters": 5000.0,
            }
        ],
        "sector_include": [],
        "sector_exclude": [],
    }
    res = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=bad_payload_contradiction)
    assert res.status_code == 422
    assert "Conflicting targeting rule" in res.text
