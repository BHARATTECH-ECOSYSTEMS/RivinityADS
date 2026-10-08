import pytest
import asyncio
import json
import hmac
import hashlib
from decimal import Decimal
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock

from liteads.models.ad import Advertiser
from liteads.models.finance import Transaction
from liteads.models import User


def generate_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

def create_payload(order_id="order_test123", amount=10000, receipt="adv_99_topup", event="order.paid", status="paid", currency="INR"):
    return {
        "event": event,
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": amount,
                    "currency": currency,
                    "receipt": receipt,
                    "status": status,
                }
            }
        }
    }


@pytest.fixture
def mock_webhook_secret(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "test_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test_secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret_123")
    from liteads.common.config import get_settings
    settings = get_settings()
    original_secret = settings.payment.webhook_secret
    original_id = settings.payment.key_id
    original_key_secret = settings.payment.key_secret
    settings.payment.webhook_secret = "test_webhook_secret_123"
    settings.payment.key_id = "test_id"
    settings.payment.key_secret = "test_secret"
    yield "test_webhook_secret_123"
    settings.payment.webhook_secret = original_secret
    settings.payment.key_id = original_id
    settings.payment.key_secret = original_key_secret


@pytest.fixture
def mock_razorpay_client_verification(mock_webhook_secret):
    mock_client = MagicMock()
    
    def verify(body, signature):
        expected = generate_signature(mock_webhook_secret, body.encode("utf-8") if isinstance(body, str) else body)
        return hmac.compare_digest(expected, signature)
        
    mock_client.verify_webhook_signature.side_effect = verify
    
    from liteads.ad_server.main import app
    from liteads.ad_server.services.razorpay_client import get_razorpay_client
    app.dependency_overrides[get_razorpay_client] = lambda: mock_client
    yield mock_client
    app.dependency_overrides.pop(get_razorpay_client, None)


@pytest.mark.asyncio
async def test_webhook_missing_signature(client: AsyncClient, mock_razorpay_client_verification):
    # TEST 3 - MISSING SIGNATURE
    body = json.dumps(create_payload()).encode("utf-8")
    resp = await client.post("/api/v1/finance/webhooks/razorpay", content=body)
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_tampered_payload(client: AsyncClient, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 2 - TAMPERED PAYLOAD
    body = json.dumps(create_payload()).encode("utf-8")
    bad_signature = generate_signature(mock_webhook_secret, b"different payload")
    
    resp = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": bad_signature})
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_valid_signature_and_wallet_credit(client: AsyncClient, test_db: AsyncSession, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 1 - VALID SIGNATURE
    user = User(id=99, email="test99@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Valid Adv", owner_user_id=user.id, balance=Decimal("10.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    
    payload_dict = create_payload(receipt=f"adv_{adv.id}_topup", amount=25000)
    body = json.dumps(payload_dict).encode("utf-8")
    signature = generate_signature(mock_webhook_secret, body)
    
    resp = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature})
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    
    await test_db.refresh(adv)
    assert adv.balance == Decimal("260.0")
    
    result = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))
    transactions = result.scalars().all()
    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("250.0")
    assert transactions[0].reference_id == "order_test123"


@pytest.mark.asyncio
async def test_webhook_duplicate_delivery(client: AsyncClient, test_db: AsyncSession, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 4 & 5 - DUPLICATE WEBHOOK and MULTIPLE DUPLICATE DELIVERIES
    user = User(id=100, email="test100@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Duplicate Adv", owner_user_id=user.id, balance=Decimal("0.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    
    payload_dict = create_payload(order_id="order_dup1", receipt=f"adv_{adv.id}_topup", amount=15000)
    body = json.dumps(payload_dict).encode("utf-8")
    signature = generate_signature(mock_webhook_secret, body)
    
    resp1 = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature})
    assert resp1.status_code == 200
    
    resp2 = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature})
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "already_processed"}
    
    resp3 = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature})
    assert resp3.status_code == 200
    
    await test_db.refresh(adv)
    assert adv.balance == Decimal("150.0")
    
    result = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))
    transactions = result.scalars().all()
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_webhook_failed_payment_and_wrong_events(client: AsyncClient, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 6 & 7 - FAILED PAYMENT and WRONG/UNSUPPORTED EVENT
    payload_failed = create_payload(status="failed")
    body1 = json.dumps(payload_failed).encode("utf-8")
    sig1 = generate_signature(mock_webhook_secret, body1)
    resp1 = await client.post("/api/v1/finance/webhooks/razorpay", content=body1, headers={"x-razorpay-signature": sig1})
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ignored"
    
    payload_wrong_event = create_payload(event="payment.failed")
    body2 = json.dumps(payload_wrong_event).encode("utf-8")
    sig2 = generate_signature(mock_webhook_secret, body2)
    resp2 = await client.post("/api/v1/finance/webhooks/razorpay", content=body2, headers={"x-razorpay-signature": sig2})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_invalid_amount_and_currency(client: AsyncClient, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 8 & 9 - INVALID AMOUNT and WRONG CURRENCY
    payload_zero = create_payload(amount=0)
    body1 = json.dumps(payload_zero).encode("utf-8")
    sig1 = generate_signature(mock_webhook_secret, body1)
    resp1 = await client.post("/api/v1/finance/webhooks/razorpay", content=body1, headers={"x-razorpay-signature": sig1})
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ignored"
    
    payload_usd = create_payload(currency="USD")
    body2 = json.dumps(payload_usd).encode("utf-8")
    sig2 = generate_signature(mock_webhook_secret, body2)
    resp2 = await client.post("/api/v1/finance/webhooks/razorpay", content=body2, headers={"x-razorpay-signature": sig2})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_cross_advertiser_invalid(client: AsyncClient, mock_razorpay_client_verification, mock_webhook_secret):
    # TEST 10 - CROSS-ADVERTISER / INVALID ASSOCIATION
    payload = create_payload(receipt="invalid_receipt_format")
    body = json.dumps(payload).encode("utf-8")
    sig = generate_signature(mock_webhook_secret, body)
    resp = await client.post("/api/v1/finance/webhooks/razorpay", content=body, headers={"x-razorpay-signature": sig})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
