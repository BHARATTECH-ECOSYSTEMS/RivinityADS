import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from liteads.models.ad import Advertiser
from liteads.models.finance import Transaction
from liteads.models.base import TransactionType
from liteads.models import User
from liteads.common.security import create_access_token
from sqlalchemy import select


def get_auth_headers(user: User):
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_razorpay_client():
    with patch("liteads.ad_server.services.tools.get_razorpay_client") as mock_get:
        mock_client = MagicMock()
        
        import uuid
        def create_qr_mock(*args, **kwargs):
            return {
                "id": f"order_test_{uuid.uuid4().hex[:6]}",
                "amount": 10000,
                "currency": "INR",
                "receipt": "test_receipt",
                "status": "created",
                "image_url": "http://qr"
            }
            
        mock_client.create_qr_code.side_effect = create_qr_mock
        
        from liteads.ad_server.main import app
        from liteads.ad_server.services.razorpay_client import get_razorpay_client
        
        app.dependency_overrides[get_razorpay_client] = lambda: mock_client
        mock_get.return_value = mock_client
        
        yield mock_client
        app.dependency_overrides.pop(get_razorpay_client, None)


@pytest.mark.asyncio
async def test_unauthenticated_payment_request(client, test_db):
    # TEST 1: Unauthenticated request is rejected.
    adv = Advertiser(name="Test Adv", balance=Decimal("0"))
    test_db.add(adv)
    await test_db.commit()
    
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 100})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_payment_request_validation(client, test_db, mock_razorpay_client):
    user = User(id=99, email="test99@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Valid Adv", owner_user_id=user.id, balance=Decimal("10.0"))
    test_db.add_all([user, adv])
    await test_db.flush()
    user.advertiser_id = adv.id
    await test_db.commit()
    
    headers = get_auth_headers(user)
    
    # TEST 3: ₹99 is rejected.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 99.0}, headers=headers)
    assert resp.status_code == 422
    
    # TEST 6: zero is rejected.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 0}, headers=headers)
    assert resp.status_code == 422
    
    # TEST 7: negative amount is rejected.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": -50}, headers=headers)
    assert resp.status_code == 422
    
    # TEST 4: ₹100 is accepted.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 100.0}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"].startswith("order_test_")
    assert Decimal(data["amount"]) == Decimal("100.0")
    
    # TEST 5: ₹500 is accepted.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 500.0}, headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_payment_request_unauthorized_advertiser(client, test_db, mock_razorpay_client):
    user = User(id=101, email="test101@test.com", role="advertiser", name="test", password_hash="test")
    # Create an advertiser belonging to someone else
    adv = Advertiser(name="Other Adv", owner_user_id=100, balance=Decimal("0"))
    test_db.add_all([user, adv])
    await test_db.flush()
    user.advertiser_id = 999  # Some other advertiser_id
    await test_db.commit()
    
    # TEST 8: Advertiser cannot create a payment request for another advertiser.
    headers = get_auth_headers(user)
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 100.0}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_payment_request_wallet_safety(client, test_db, mock_razorpay_client):
    user = User(id=102, email="test102@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Safety Adv", owner_user_id=user.id, balance=Decimal("50.0"))
    test_db.add_all([user, adv])
    await test_db.flush()
    user.advertiser_id = adv.id
    await test_db.commit()
    
    headers = get_auth_headers(user)
    
    # TEST 2 & TEST 9: Authenticated advertiser can request a valid top-up, SDK correctly invoked.
    resp = await client.post(f"/api/v1/finance/advertiser/{adv.id}/payment-request", json={"amount": 200.0}, headers=headers)
    assert resp.status_code == 200
    
    mock_razorpay_client.create_qr_code.assert_called_once()
    
    await test_db.refresh(adv)
    
    # TEST 10: Creating a payment request does NOT modify Advertiser.balance.
    assert adv.balance == Decimal("50.0")
    
    # TEST 11: Creating a payment request does NOT create a successful wallet deposit transaction.
    result = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))
    transactions = result.scalars().all()
    assert len(transactions) == 0
