"""Tests for Webhooks."""

import pytest
from httpx import AsyncClient

from liteads.models.webhook import Webhook

pytestmark = pytest.mark.asyncio


async def test_webhook_crud(client: AsyncClient, test_db) -> None:
    # 1. Register Advertiser
    res = await client.post(
        "/api/v1/auth/register",
        json={"name": "Webhook Adv", "email": "adv_wh@test.com", "password": "Password123!", "role": "advertiser"}
    )
    assert res.status_code == 201
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create Webhook
    res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/webhook", "events": ["impression", "conversion"]},
        headers=headers,
    )
    assert res.status_code == 201
    wh_data = res.json()
    assert wh_data["url"] == "https://example.com/webhook"
    assert "impression" in wh_data["events"]
    assert "conversion" in wh_data["events"]
    assert "secret" in wh_data
    secret = wh_data["secret"]
    wh_id = wh_data["id"]

    # 3. List Webhooks
    res = await client.get("/api/v1/webhooks", headers=headers)
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert "secret" not in res.json()[0]

    # 4. Update Webhook
    res = await client.patch(
        f"/api/v1/webhooks/{wh_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    # 5. Delete Webhook
    res = await client.delete(f"/api/v1/webhooks/{wh_id}", headers=headers)
    assert res.status_code == 204
    
    # 6. Verify Delete
    res = await client.get("/api/v1/webhooks", headers=headers)
    assert res.status_code == 200
    assert len(res.json()) == 0
