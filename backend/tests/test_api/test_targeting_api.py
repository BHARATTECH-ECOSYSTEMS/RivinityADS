import pytest
from httpx import AsyncClient, ASGITransport
from liteads.ad_server.main import app
from liteads.common.database import get_session
from liteads.models import Campaign, TargetingRule

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def advertiser_client(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv", "email": "adv@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        # Create a campaign
        adv_id = data["user"]["advertiser_id"]
        res = await c.post("/api/v1/campaign", json={
            "advertiser_id": adv_id,
            "name": "Camp A",
            "budget_daily": 100.0,
            "budget_total": 1000.0,
            "bid_amount": 1.0,
            "bid_type": 1,
            "status": 1
        })
        camp_id = res.json()["id"]
        
        yield {"client": c, "campaign_id": camp_id}
        app.dependency_overrides.clear()


async def test_get_targeting(advertiser_client, test_db):
    client = advertiser_client["client"]
    campaign_id = advertiser_client["campaign_id"]
    
    # Setup some targeting rules
    rule = TargetingRule(
        campaign_id=campaign_id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"country": "IN", "state": "MH", "city": "mumbai"}]}
    )
    test_db.add(rule)
    
    rule_sector = TargetingRule(
        campaign_id=campaign_id,
        rule_type="sector",
        is_include=False,
        rule_value={"sectors": ["fintech"]}
    )
    test_db.add(rule_sector)
    await test_db.commit()

    # Get targeting
    response = await client.get(f"/api/v1/campaign/{campaign_id}/targeting")
    assert response.status_code == 200
    data = response.json()
    assert data["campaign_id"] == campaign_id
    assert len(data["geo_include"]) == 1
    assert data["geo_include"][0]["country"] == "IN"
    assert data["geo_include"][0]["state"] == "MH"
    assert data["geo_include"][0]["city"] == "mumbai"
    assert len(data["geo_exclude"]) == 0
    assert len(data["sector_include"]) == 0
    assert len(data["sector_exclude"]) == 1
    assert data["sector_exclude"][0] == "fintech"


async def test_update_targeting(advertiser_client, test_db):
    client = advertiser_client["client"]
    campaign_id = advertiser_client["campaign_id"]
    
    payload = {
        "geo_include": [{"country": "US"}],
        "geo_exclude": [{"country": "IN", "pincode": "400001"}],
        "sector_include": ["gaming"],
        "sector_exclude": []
    }
    
    response = await client.put(f"/api/v1/campaign/{campaign_id}/targeting", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert len(data["geo_include"]) == 1
    assert len(data["geo_exclude"]) == 1
    assert len(data["sector_include"]) == 1
    assert len(data["sector_exclude"]) == 0
    
    # Check DB
    from sqlalchemy import select
    result = await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign_id))
    rules = result.scalars().all()
    assert len(rules) == 3 # geo_include, geo_exclude, sector_include

async def test_cross_tenant_targeting_isolation(advertiser_client, test_db):
    campaign_id = advertiser_client["campaign_id"]
    
    # Create a second advertiser
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv2", "email": "adv2@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        # Try to GET targeting of the first advertiser's campaign
        res_get = await c.get(f"/api/v1/campaign/{campaign_id}/targeting")
        assert res_get.status_code == 403
        
        # Try to PUT targeting for the first advertiser's campaign
        payload = {
            "geo_include": [{"country": "US"}],
            "geo_exclude": [],
            "sector_include": [],
            "sector_exclude": []
        }
        res_put = await c.put(f"/api/v1/campaign/{campaign_id}/targeting", json=payload)
        assert res_put.status_code == 403
        
        app.dependency_overrides.clear()
