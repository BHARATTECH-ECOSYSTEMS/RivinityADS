import pytest
from httpx import AsyncClient, ASGITransport
from liteads.ad_server.main import app
from liteads.common.database import get_session
from sqlalchemy import select

@pytest.fixture
async def advertiser_a(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv A API", "email": "a_api@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"], "adv_id": data["user"]["advertiser_id"]}
        app.dependency_overrides.clear()

@pytest.fixture
async def advertiser_b(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        app.dependency_overrides[get_session] = lambda: test_db
        res = await c.post("/api/v1/auth/register", json={"name": "Adv B API", "email": "b_api@adv.com", "password": "password1234", "role": "advertiser"})
        data = res.json()
        c.headers = {"Authorization": f"Bearer {data['access_token']}"}
        yield {"client": c, "user": data["user"], "adv_id": data["user"]["advertiser_id"]}
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_autopilot_off(advertiser_a, test_db):
    client = advertiser_a["client"]
    adv_id = advertiser_a["adv_id"]
    
    # Create campaign
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Autopilot Off",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    })
    camp_id = res.json()["id"]

    # Test GET
    res = await client.get(f"/api/v1/campaign/{camp_id}")
    assert res.status_code == 200
    data = res.json()
    assert "autopilot" in data
    assert data["autopilot"] is False

@pytest.mark.asyncio
async def test_get_autopilot_on(advertiser_a, test_db):
    client = advertiser_a["client"]
    adv_id = advertiser_a["adv_id"]
    
    # Create campaign
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Autopilot On",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    })
    camp_id = res.json()["id"]

    # Patch to ON
    await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True})

    # Test GET
    res = await client.get(f"/api/v1/campaign/{camp_id}")
    assert res.status_code == 200
    data = res.json()
    assert "autopilot" in data
    assert data["autopilot"] is True

@pytest.mark.asyncio
async def test_patch_autopilot_off_to_on(advertiser_a, test_db):
    client = advertiser_a["client"]
    adv_id = advertiser_a["adv_id"]
    
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Toggle ON",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0
    })
    camp_id = res.json()["id"]

    res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True})
    assert res.status_code == 200
    assert res.json()["autopilot"] is True

    # Database persistence verification
    from liteads.models.ad import Campaign
    camp = await test_db.get(Campaign, camp_id)
    assert camp.autopilot is True

@pytest.mark.asyncio
async def test_patch_autopilot_on_to_off(advertiser_a, test_db):
    client = advertiser_a["client"]
    adv_id = advertiser_a["adv_id"]
    
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Toggle OFF",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0
    })
    camp_id = res.json()["id"]
    
    # Set ON
    await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True})

    # Set OFF
    res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": False})
    assert res.status_code == 200
    assert res.json()["autopilot"] is False

    from liteads.models.ad import Campaign
    camp = await test_db.get(Campaign, camp_id)
    assert camp.autopilot is False

@pytest.mark.asyncio
async def test_partial_update_regression(advertiser_a, test_db):
    client = advertiser_a["client"]
    adv_id = advertiser_a["adv_id"]
    
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Original Name",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0
    })
    camp_id = res.json()["id"]

    res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"name": "New Name"})
    assert res.status_code == 200
    assert res.json()["name"] == "New Name"
    assert res.json()["autopilot"] is False

    from liteads.models.ad import Campaign
    camp = await test_db.get(Campaign, camp_id)
    assert camp.name == "New Name"
    assert camp.autopilot is False

@pytest.mark.asyncio
async def test_authorization_regression(advertiser_a, advertiser_b):
    client_a = advertiser_a["client"]
    adv_a_id = advertiser_a["adv_id"]
    client_b = advertiser_b["client"]
    
    # A creates campaign
    res = await client_a.post("/api/v1/campaign", json={
        "advertiser_id": adv_a_id,
        "name": "Camp A",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0
    })
    camp_id = res.json()["id"]

    # B tries to patch A's autopilot
    res = await client_b.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True})
    assert res.status_code == 403
