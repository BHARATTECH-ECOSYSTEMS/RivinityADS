import pytest
from httpx import AsyncClient
from decimal import Decimal
from liteads.models import Advertiser, Publisher, User, PlatformSettings
from liteads.models.finance import Transaction
from liteads.models.base import TransactionType
from liteads.common.security import create_access_token

@pytest.fixture
async def finance_test_data(test_db):
    # Create two users (admin and standard) and advertisers/publishers
    admin_user = User(id=1, email="admin@example.com", name="Admin", password_hash="hash", role="admin")
    adv_owner = User(id=2, email="adv@example.com", name="Adv", password_hash="hash", role="advertiser")
    pub_owner = User(id=3, email="pub@example.com", name="Pub", password_hash="hash", role="publisher")
    other_user = User(id=4, email="other@example.com", name="Other", password_hash="hash", role="advertiser")

    adv1 = Advertiser(id=1, name="Adv 1", balance=Decimal("100.0000"), owner_user_id=2)
    adv2 = Advertiser(id=2, name="Adv 2", balance=Decimal("50.0000"), owner_user_id=4)
    pub1 = Publisher(id=1, name="Pub 1", balance=Decimal("10.0000"), owner_user_id=3)
    pub2 = Publisher(id=2, name="Pub 2", balance=Decimal("20.0000"), owner_user_id=4)
    
    settings = PlatformSettings(id=1, publisher_rev_share=Decimal("0.700"))
    
    test_db.add_all([admin_user, adv_owner, pub_owner, other_user, adv1, adv2, pub1, pub2, settings])
    await test_db.commit()

def get_auth_headers(user_id: int, role: str):
    user = User(id=user_id, role=role, is_active=True, email="test@test.com", name="test", password_hash="test")
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_unauthenticated_endpoints(client: AsyncClient):
    res = await client.get("/api/v1/finance/advertiser/1/ledger")
    assert res.status_code == 401
    
    res = await client.get("/api/v1/finance/publisher/1/ledger")
    assert res.status_code == 401
    
    res = await client.post("/api/v1/finance/advertiser/1/deposit", json={"amount": 10.0})
    assert res.status_code == 401
    
    res = await client.post("/api/v1/finance/publisher/1/payout", json={"amount": 10.0})
    assert res.status_code == 401

@pytest.mark.asyncio
async def test_advertiser_ledger_access(client: AsyncClient, finance_test_data):
    # Owner access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(2, "advertiser")
    )
    assert res.status_code == 200
    assert float(res.json()["balance"]) == 100.0
    
    # Admin access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(1, "admin")
    )
    assert res.status_code == 200
    
    # Other advertiser access
    res = await client.get(
        "/api/v1/finance/advertiser/1/ledger", 
        headers=get_auth_headers(4, "advertiser")
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_deposit_funds_authorization(client: AsyncClient, finance_test_data):
    # Admin can deposit
    res = await client.post(
        "/api/v1/finance/advertiser/1/deposit", 
        headers=get_auth_headers(1, "admin"),
        json={"amount": 50.0, "reference_id": "test_dep_1"}
    )
    assert res.status_code == 200
    assert float(res.json()["amount"]) == 50.0
    
    # Owner cannot deposit directly
    res = await client.post(
        "/api/v1/finance/advertiser/1/deposit", 
        headers=get_auth_headers(2, "advertiser"),
        json={"amount": 50.0, "reference_id": "test_dep_2"}
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_publisher_ledger_access(client: AsyncClient, finance_test_data):
    # Owner access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(3, "publisher")
    )
    assert res.status_code == 200
    assert float(res.json()["balance"]) == 10.0
    
    # Admin access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(1, "admin")
    )
    assert res.status_code == 200
    
    # Other publisher access
    res = await client.get(
        "/api/v1/finance/publisher/1/ledger", 
        headers=get_auth_headers(4, "publisher")
    )
    assert res.status_code == 403

@pytest.mark.asyncio
async def test_request_payout_authorization(client: AsyncClient, finance_test_data):
    # Owner can payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(3, "publisher"),
        json={"amount": 5.0, "reference_id": "test_pay_1"}
    )
    assert res.status_code == 200
    
    # Admin can payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(1, "admin"),
        json={"amount": 1.0, "reference_id": "test_pay_2"}
    )
    assert res.status_code == 200
    
    # Other publisher cannot payout
    res = await client.post(
        "/api/v1/finance/publisher/1/payout", 
        headers=get_auth_headers(4, "publisher"),
        json={"amount": 1.0, "reference_id": "test_pay_3"}
    )
    assert res.status_code == 403
