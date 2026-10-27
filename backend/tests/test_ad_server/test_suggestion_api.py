import pytest
from httpx import AsyncClient
from sqlalchemy import select

from decimal import Decimal
from liteads.models.ad import Suggestion, CampaignChangeLog, TargetingRule, Advertiser, Campaign
from liteads.models.base import Status


@pytest.fixture
async def auth_setup(client: AsyncClient, test_db):
    res = await client.post("/api/v1/auth/register", json={"name": "Adv", "email": "adv@adv.com", "password": "password1234", "role": "advertiser"})
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    
    adv_id = data["user"]["advertiser_id"]
    res = await client.post("/api/v1/campaign", json={
        "advertiser_id": adv_id,
        "name": "Camp A",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_amount": 1.0,
        "bid_type": 1,
        "status": 1
    }, headers=headers)
    camp = res.json()
    
    adv = await test_db.get(Advertiser, adv_id)
    adv.balance = Decimal("1000.00")
    camp_obj = await test_db.get(Campaign, camp["id"])
    camp_obj.status = Status.ACTIVE
    await test_db.commit()

    return {"headers": headers, "campaign": camp}


@pytest.fixture
async def sample_suggestion(test_db, auth_setup):
    camp_id = auth_setup["campaign"]["id"]
    sugg = Suggestion(
        campaign_id=camp_id,
        action={
            "action": "increase_budget_20",
            "confidence": 0.9,
            "proposed_budget_delta": 2.0
        },
        status="PENDING"
    )
    test_db.add(sugg)
    await test_db.commit()
    await test_db.refresh(sugg)
    return sugg


@pytest.mark.asyncio
async def test_get_suggestions(client: AsyncClient, auth_setup, sample_suggestion):
    headers = auth_setup["headers"]
    camp_id = auth_setup["campaign"]["id"]
    response = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == sample_suggestion.id
    assert data[0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_reject_suggestion(client: AsyncClient, auth_setup, test_db, sample_suggestion):
    headers = auth_setup["headers"]
    response = await client.patch(
        f"/api/v1/suggestion/{sample_suggestion.id}",
        json={"status": "REJECTED"},
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "REJECTED"

    await test_db.refresh(sample_suggestion)
    assert sample_suggestion.status == "REJECTED"
    
    res2 = await client.patch(
        f"/api/v1/suggestion/{sample_suggestion.id}",
        json={"status": "REJECTED"},
        headers=headers
    )
    assert res2.status_code == 400


@pytest.mark.asyncio
async def test_approve_suggestion(client: AsyncClient, auth_setup, test_db, sample_suggestion):
    headers = auth_setup["headers"]
    camp_id = auth_setup["campaign"]["id"]
    old_budget = auth_setup["campaign"]["budget_daily"]
    
    response = await client.patch(
        f"/api/v1/suggestion/{sample_suggestion.id}",
        json={"status": "APPLIED"},
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "APPLIED"

    await test_db.refresh(sample_suggestion)
    assert sample_suggestion.status == "APPLIED"
    
    # Verify changelog
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 1

    res2 = await client.patch(
        f"/api/v1/suggestion/{sample_suggestion.id}",
        json={"status": "APPLIED"},
        headers=headers
    )
    assert res2.status_code == 400


@pytest.mark.asyncio
async def test_stale_suggestion(client: AsyncClient, auth_setup, test_db, sample_suggestion):
    headers = auth_setup["headers"]
    camp_id = auth_setup["campaign"]["id"]
    
    # Make action unsafe by modifying budget
    res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"budget_daily": 1.0}, headers=headers)
    assert res.status_code == 200
    
    response = await client.patch(
        f"/api/v1/suggestion/{sample_suggestion.id}",
        json={"status": "APPLIED"},
        headers=headers
    )
    assert response.status_code == 400
    assert "Budget change exceeds 30% guardrail" in response.json()["detail"]

    await test_db.refresh(sample_suggestion)
    assert sample_suggestion.status == "PENDING"

